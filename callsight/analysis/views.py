"""API data for the call-detail analysis block and the rating page (staff only; agents see nothing, decision 9).

The LLM estimate is shown only when `blindset.estimate_hidden` allows it. Summary / reason / action items are
returned as plain strings; the web page renders them as text (React escapes), never as HTML.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db.models import (BlindSet, Call, CallAnalysis, CallAudioMetrics, CallFlag, SatisfactionLabel, Transcript)
from . import blindset

QUEUE_SIZE = 50
ESTIMATE_LABEL = "تقدير أولي — غير معتمد — ليس لتقييم الموظفين"


def shown_analysis(session: Session, call_id: int, model: str | None = None, pv: str | None = None
                   ) -> CallAnalysis | None:
    """Done row of the current model + prompt first, then the newest done row, else the newest row (status)."""
    base = select(CallAnalysis).where(CallAnalysis.call_id == call_id)
    current = case(((CallAnalysis.model == model) & (CallAnalysis.prompt_version == pv), 0), else_=1)
    done = session.scalars(base.where(CallAnalysis.status == "done")
                           .order_by(current, CallAnalysis.finished_at.desc().nulls_last(), CallAnalysis.id.desc())
                           .limit(1)).first()
    if done is not None:
        return done
    return session.scalars(base.order_by(current, CallAnalysis.queued_at.desc(), CallAnalysis.id.desc())
                           .limit(1)).first()


def _content(a: CallAnalysis | None, topics: dict[str, str]) -> dict | None:
    if a is None:
        return None
    d = {"status": a.status, "model": a.model, "prompt_version": a.prompt_version}
    if a.status == "done":
        d.update(summary=a.summary, topic=a.topic, topic_label=topics.get(a.topic or "", a.topic),
                 action_items=list(a.action_items or []), escalation=a.escalation, trimmed=a.trimmed)
    return d


def _estimate(a: CallAnalysis | None, hidden: str | None) -> dict | None:
    if a is None or a.status != "done" or hidden is not None or a.llm_score is None:
        return None
    return {"score": a.llm_score, "reason": a.reason, "label": ESTIMATE_LABEL}


def call_analysis(session: Session, call_id: int, user_id: int, topics: dict[str, str],
                  model: str | None = None, pv: str | None = None) -> dict:
    a = shown_analysis(session, call_id, model, pv)
    hidden = blindset.estimate_hidden(session, call_id, user_id)
    flags = session.scalars(select(CallFlag).where(CallFlag.call_id == call_id)
                            .order_by(CallFlag.start_seconds, CallFlag.id)).all()
    m = session.get(CallAudioMetrics, call_id)
    return {
        "call_id": call_id,
        "analysis": _content(a, topics),
        "estimate": _estimate(a, hidden),
        "estimate_hidden": hidden if a is not None and a.status == "done" else None,
        "flags": [{"kind": f.kind, "phrase": f.phrase, "start": f.start_seconds, "end": f.end_seconds}
                  for f in flags],
        "silence": None if m is None else {
            "share": m.silence_share, "seconds": m.silence_seconds, "longest": m.longest_silence_s,
            "count": m.silences, "long_count": m.long_silences, "version": m.metrics_version},
    }


def _done_transcript():
    return exists().where(Transcript.call_id == Call.id, Transcript.status == "done")


def rating_queue(session: Session, user_id: int) -> dict:
    """Calls with a done transcript this user has not rated: blind calls first (by rank), then newest."""
    mine = exists().where(SatisfactionLabel.call_id == Call.id, SatisfactionLabel.user_id == user_id)
    rows = session.execute(
        select(Call.id, Call.start_time, Call.talk_seconds, BlindSet.rank)
        .outerjoin(BlindSet, BlindSet.call_id == Call.id)
        .where(_done_transcript(), ~mine, Call.status == "answered")
        .order_by(BlindSet.rank.asc().nulls_last(), Call.start_time.desc(), Call.id.desc())
        .limit(QUEUE_SIZE)).all()
    rated = session.scalar(select(exists().where(SatisfactionLabel.user_id == user_id)))
    return {"progress": {**blindset.progress(session), "mine": bool(rated)},
            "items": [{"call_id": cid, "start_time": st.isoformat(), "talk_seconds": talk, "blind": rank is not None}
                      for cid, st, talk, rank in rows]}


def _label(session: Session, call_id: int, user_id: int) -> SatisfactionLabel | None:
    return session.scalars(select(SatisfactionLabel).where(SatisfactionLabel.call_id == call_id,
                                                           SatisfactionLabel.user_id == user_id)).first()


def _prefill(session: Session, call_id: int, user_id: int, a: CallAnalysis | None) -> int | None:
    """The LLM estimate offered as pre-fill on the rating page: never for blind calls / before the freeze."""
    if a is None or a.status != "done":
        return None
    return a.llm_score if blindset.estimate_hidden(session, call_id, user_id, rating_page=True) is None else None


def rating_call(session: Session, call: Call, user_id: int, topics: dict[str, str],
                model: str | None = None, pv: str | None = None) -> dict:
    a = shown_analysis(session, call.id, model, pv)
    hidden = blindset.estimate_hidden(session, call.id, user_id, rating_page=True)
    lab = _label(session, call.id, user_id)
    return {
        "call_id": call.id, "start_time": call.start_time.isoformat(), "talk_seconds": call.talk_seconds,
        "has_recording": call.recording_status == "ok", "blind": blindset.is_blind(session, call.id),
        "analysis": _content(a, topics),
        "estimate": _estimate(a, hidden),            # omitted (None) for every blind call, always (requirement 5)
        "my_label": None if lab is None else {"score": lab.score, "note": lab.note},
    }


def save_label(session: Session, call: Call, user_id: int, score: int, note: str | None,
               model: str | None = None, pv: str | None = None) -> dict:
    """Upsert this user's rating of the call. `prefill` = what the page showed (server side, not trusted input)."""
    a = shown_analysis(session, call.id, model, pv)
    prefill = _prefill(session, call.id, user_id, a)
    note = (note or "").strip() or None
    values = dict(score=score, note=note, blind=blindset.is_blind(session, call.id), prefill=prefill,
                  changed_from_prefill=None if prefill is None else score != prefill,
                  updated_at=datetime.now(timezone.utc))
    session.execute(insert(SatisfactionLabel).values(call_id=call.id, user_id=user_id, **values)
                    .on_conflict_do_update(constraint="uq_satisfaction_label_call_user", set_=values))
    session.commit()
    return {"score": score, "note": note}
