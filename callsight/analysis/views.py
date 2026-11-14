"""API data for the call-detail analysis block and the rating page (staff only; agents see nothing, decision 9).

The LLM estimate is shown only when `blindset.estimate_hidden` allows it. For blind-set calls the WHOLE analysis
block (summary, topic, action items, escalation) is hidden too - on the rating page always, in call detail until
the viewing user has rated the call: the LLM's judgement must not anchor the human score (review 2026-11-14).
A blind label is final: once given it can't be changed (the rater may have seen the estimate afterwards).
Summary / reason / action items are returned as plain strings; the web page renders them as text (React escapes),
never as HTML.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import case, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db.models import (BlindSet, Call, CallAnalysis, CallAudioMetrics, CallFlag, SatisfactionLabel, Transcript)
from . import blindset

QUEUE_SIZE = 50
ESTIMATE_LABEL = "تقدير أولي — غير معتمد — ليس لتقييم الموظفين"
_LONG_S = re.compile(r"l([0-9]+(?:\.[0-9]+)?)$")     # SilenceRules.version ends with l<long_silence_s>


class LabelLocked(Exception):
    """A blind-set call already rated by this user: the label can't be changed any more (-> HTTP 409)."""


def _long_s(metrics_version: str | None) -> float | None:
    """The "long silence" threshold the stored metrics were computed with (rules.yaml may have changed since)."""
    m = _LONG_S.search(metrics_version or "")
    return float(m.group(1)) if m else None


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
        # blind call not yet rated by this user: no LLM judgement at all (summary/escalation would anchor the score)
        "analysis": None if hidden == "blind" else _content(a, topics),
        "estimate": _estimate(a, hidden),
        "estimate_hidden": hidden if a is not None and a.status == "done" else None,
        "flags": [{"kind": f.kind, "phrase": f.phrase, "start": f.start_seconds, "end": f.end_seconds}
                  for f in flags],
        "silence": None if m is None else {
            "share": m.silence_share, "seconds": m.silence_seconds, "longest": m.longest_silence_s,
            "count": m.silences, "long_count": m.long_silences, "version": m.metrics_version,
            "long_s": _long_s(m.metrics_version)},
    }


def has_done_analysis(result: dict) -> bool:
    """True when `call_analysis` returned LLM text (summary / reason): that read is audited by the API."""
    a = result.get("analysis")
    return bool(a and a.get("status") == "done") or result.get("estimate") is not None


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


def _prefill(session: Session, call_id: int, user_id: int, shown: int | None) -> int | None:
    """The pre-fill this rating was given with = what the page says it displayed (`shown`), accepted only when
    it really is a stored LLM estimate of this call and estimates may be shown for it on the rating page.
    (The current DB state is not used: the analysis may have finished, or been re-scored, after the page loaded.)"""
    if shown is None or blindset.estimate_hidden(session, call_id, user_id, rating_page=True) is not None:
        return None
    known = session.scalar(select(CallAnalysis.id).where(
        CallAnalysis.call_id == call_id, CallAnalysis.status == "done", CallAnalysis.llm_score == shown).limit(1))
    return shown if known is not None else None


def rating_call(session: Session, call: Call, user_id: int, topics: dict[str, str],
                model: str | None = None, pv: str | None = None) -> dict:
    a = shown_analysis(session, call.id, model, pv)
    hidden = blindset.estimate_hidden(session, call.id, user_id, rating_page=True)
    lab = _label(session, call.id, user_id)
    blind = blindset.is_blind(session, call.id)
    return {
        "call_id": call.id, "start_time": call.start_time.isoformat(), "talk_seconds": call.talk_seconds,
        "has_recording": call.recording_status == "ok", "blind": blind,
        "analysis": None if blind else _content(a, topics),   # blind: no summary / escalation either
        "estimate": _estimate(a, hidden),            # omitted (None) for every blind call, always (requirement 5)
        "my_label": None if lab is None else {"score": lab.score, "note": lab.note},
        "locked": blind and lab is not None,         # a blind label is final
    }


def save_label(session: Session, call: Call, user_id: int, score: int, note: str | None,
               prefill_shown: int | None = None) -> dict:
    """Upsert this user's rating of the call.
    - Blind-set call already rated by this user -> LabelLocked (after rating, call detail shows the estimate: a
      later change would be a "blind" label given with the estimate in view).
    - `prefill` = the estimate the page displayed, checked against the stored estimates; on an update the FIRST
      pre-fill is kept (what the rater saw when deciding), and `changed_from_prefill` compares with it."""
    blind = blindset.is_blind(session, call.id)
    old = _label(session, call.id, user_id)
    if old is not None and (blind or old.blind):
        raise LabelLocked()
    prefill = old.prefill if old is not None else _prefill(session, call.id, user_id, prefill_shown)
    note = (note or "").strip() or None
    values = dict(score=score, note=note, blind=blind, prefill=prefill,
                  changed_from_prefill=None if prefill is None else score != prefill,
                  updated_at=datetime.now(timezone.utc))
    session.execute(insert(SatisfactionLabel).values(call_id=call.id, user_id=user_id, **values)
                    .on_conflict_do_update(constraint="uq_satisfaction_label_call_user", set_=values))
    session.commit()
    return {"score": score, "note": note}
