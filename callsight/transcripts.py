"""Transcript view, .txt export and word search (spec 2026-11-14-transcript-view).

Search runs on `transcript.search_text`: the kept segment texts after `search_normalize()` (benchmark `normalize()`
plus ى→ي), matched with LIKE on a pg_trgm GIN index. Transcript text is personal data: nothing here logs it, and
error messages never quote it or the searched words.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from .asr.normalize import normalize
from .config import RIYADH
from .db.models import Transcript, TranscriptSegment

MAX_WORDS = 5
MIN_WORD = 2
MAX_QUERY = 100
DROPPED_TEXT = "— (صمت/غير واضح)"
DISCLAIMER = "تفريغ آلي — قد يحتوي أخطاء؛ الأرقام المنطوقة في المكالمة غير مخفية"
RLM = "‏"
DIRECTION_AR = {"inbound": "وارد", "outbound": "صادر", "internal": "داخلي"}

_SEARCH_FOLD = str.maketrans({"ى": "ي"})


def search_normalize(text: str) -> str:
    """Benchmark normalisation (normalize.py stays unchanged for WER comparability) plus ى→ي for search."""
    return normalize(text).translate(_SEARCH_FOLD)


def search_text(segments) -> str:
    """Normalised text of the kept windows only: dropped/hallucination-flagged windows are never searchable."""
    return " ".join(t for t in (search_normalize(s.text) for s in segments if not s.flag and s.text) if t)


class BadSearch(ValueError):
    """Invalid search words. The message never quotes the input."""


def parse_words(raw: str | None) -> tuple[str, ...]:
    """Typed words -> normalised words for the filter. Empty -> no filter. Raises BadSearch (-> 422)."""
    if raw is None or not raw.strip():
        return ()
    if len(raw) > MAX_QUERY:
        raise BadSearch(f"search: at most {MAX_QUERY} characters")
    if not all(ch.isalnum() or ch.isspace() or unicodedata.category(ch).startswith("M") or ch == "ـ" for ch in raw):
        raise BadSearch("search: letters, digits and spaces only")
    words = search_normalize(raw).split()
    if not 1 <= len(words) <= MAX_WORDS or any(len(w) < MIN_WORD for w in words):
        raise BadSearch(f"search: 1-{MAX_WORDS} words, each at least {MIN_WORD} characters")
    return tuple(dict.fromkeys(words))


def displayed_order(model: str):
    """Order of transcripts of one call: current ASR_MODEL first, then the newest."""
    return (case((Transcript.model == model, 0), else_=1), Transcript.finished_at.desc().nulls_last(),
            Transcript.id.desc())


def displayed(session: Session, call_id: int, model: str) -> Transcript | None:
    """The transcript shown for a call: the done one for ASR_MODEL, else the newest done; with none done, the row
    of ASR_MODEL (else the newest) so its status can be shown."""
    base = select(Transcript).where(Transcript.call_id == call_id)
    done = session.scalars(base.where(Transcript.status == "done").order_by(*displayed_order(model)).limit(1)).first()
    if done is not None:
        return done
    return session.scalars(base.order_by(case((Transcript.model == model, 0), else_=1),
                                         Transcript.queued_at.desc(), Transcript.id.desc()).limit(1)).first()


def segments(session: Session, t: Transcript) -> list[TranscriptSegment]:
    return list(session.scalars(select(TranscriptSegment).where(TranscriptSegment.transcript_id == t.id)
                                .order_by(TranscriptSegment.seq)))


def is_dropped(seg: TranscriptSegment) -> bool:
    return bool(seg.flag) or not (seg.text or "").strip()


def turns(session: Session, t: Transcript) -> list:
    from .db.models import TranscriptTurn
    return list(session.scalars(select(TranscriptTurn).where(TranscriptTurn.transcript_id == t.id)
                                .order_by(TranscriptTurn.seq)))


def display_rows(session: Session, t: Transcript) -> list[dict]:
    """What the page and the export show. Split by speaker (diarization spec): the speaker lines, plus every window
    without word times (dropped windows as "silence"; kept windows without times as one whole line, speaker None),
    in time order. Not split: one line per window, as before."""
    segs = segments(session, t)
    tt = turns(session, t) if t.diarization_version else []
    if not tt:
        return [{"start": s.start_seconds, "end": s.end_seconds, "speaker": None, "dropped": is_dropped(s),
                 "text": "" if is_dropped(s) else s.text} for s in segs]
    out = [{"start": x.start_seconds, "end": x.end_seconds, "speaker": x.speaker, "dropped": False, "text": x.text}
           for x in tt]
    out += [{"start": s.start_seconds, "end": s.end_seconds, "speaker": None, "dropped": is_dropped(s),
             "text": "" if is_dropped(s) else s.text} for s in segs if is_dropped(s) or s.words is None]
    return sorted(out, key=lambda d: (d["start"], d["dropped"]))


def as_dict(session: Session, t: Transcript, staff: bool = False) -> dict:
    """staff=True adds the talk metrics (open question 2: agents do not see them)."""
    done = t.status == "done"
    rows = display_rows(session, t) if done else []
    d = {
        "call_id": t.call_id, "status": t.status, "model": t.model,
        "created_at": (t.finished_at or t.queued_at).isoformat() if (t.finished_at or t.queued_at) else None,
        "dropped_windows": t.dropped_windows,
        "split": bool(done and t.diarization_version and any(r["speaker"] for r in rows)),
        "speaker_unsure": bool(t.speaker_unsure),
        "segments": [{"seq": i, **r} for i, r in enumerate(rows)],
    }
    if staff and done and t.diarization_version and t.speaker_unsure is False:
        d["talk"] = {"agent_s": t.agent_talk_s, "customer_s": t.customer_talk_s, "ratio": t.talk_ratio,
                     "interruptions_agent": t.interruptions_agent,
                     "interruptions_customer": t.interruptions_customer, "longest_wait_s": t.longest_wait_s}
    return d


SPEAKER_AR = {"agent": "الموظف", "customer": "العميل", "speaker_1": "متحدث 1", "speaker_2": "متحدث 2",
              "unknown": "متحدث"}


def mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def mask_number(number: str | None) -> str:
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if not digits:
        return "–"
    return "****" + digits[-4:] if len(digits) > 4 else digits


def txt_filename(call) -> str:
    return f"callsight-call-{call.id}-{call.start_time.astimezone(RIYADH):%Y%m%d}.txt"


def export_txt(call, agent_name: str | None, t: Transcript, segs: list[dict]) -> bytes:
    """UTF-8 with BOM, CRLF, every line starts with RLM (keeps `[mm:ss]` on the right in Notepad)."""
    start: datetime = call.start_time.astimezone(RIYADH)
    ext = call.agent_ext or call.missed_agent_ext
    lines = [
        f"CallSight — تفريغ المكالمة رقم {call.id}",
        f"التاريخ: {start:%Y-%m-%d %H:%M:%S} (توقيت الرياض)",
        f"الموظف: {ext or '–'}{' · ' + agent_name if agent_name else ''}",
        f"النوع: {DIRECTION_AR.get(call.direction, call.direction)}",
        f"رقم العميل: {mask_number(call.customer_number)}",
        f"النموذج: {t.model}",
        DISCLAIMER,
        "",
    ]
    for s in segs:                      # display_rows(): speaker lines when the call is split, else windows
        who = f"{SPEAKER_AR[s['speaker']]}: " if s.get("speaker") in SPEAKER_AR else ""
        lines.append(f"[{mmss(s['start'])}] " + (DROPPED_TEXT if s["dropped"] else who + " ".join(s["text"].split())))
    return ("﻿" + "\r\n".join(RLM + line for line in lines) + "\r\n").encode("utf-8")


def reindex(session: Session, batch: int = 200) -> int:
    """Rebuild search_text of every done transcript (after a normalisation change). Returns rows updated."""
    n, last = 0, 0
    while True:
        ids = list(session.scalars(select(Transcript.id).where(Transcript.status == "done", Transcript.id > last)
                                   .order_by(Transcript.id).limit(batch)))
        if not ids:
            return n
        for tid in ids:
            # lock the row first: a concurrent --force redo (asr _save) then either waits for us and writes its own
            # search_text afterwards, or has committed and we read its new segments (no stale overwrite)
            if session.scalars(select(Transcript.id).where(Transcript.id == tid, Transcript.status == "done")
                               .with_for_update()).first() is None:
                continue
            segs = session.scalars(select(TranscriptSegment).where(TranscriptSegment.transcript_id == tid)
                                   .order_by(TranscriptSegment.seq)).all()
            session.execute(update(Transcript).where(Transcript.id == tid).values(search_text=search_text(segs)))
            n += 1
        session.commit()
        last = ids[-1]


def system_first_speaker(session: Session, call_id: int, model: str) -> str:
    """First-speaker check: who the speaker split says talks first in the shown transcript.
    agent / customer, or unsure (agent not named: "متحدث 1 / 2"), or not_split (no speaker lines yet)."""
    from .db.models import TranscriptTurn
    t = displayed(session, call_id, model)
    if t is None or t.status != "done" or t.speaker_unsure is None:
        return "not_split"
    if t.speaker_unsure:
        return "unsure"
    first = session.scalars(select(TranscriptTurn.speaker).where(TranscriptTurn.transcript_id == t.id,
                                                                 TranscriptTurn.speaker.in_(("agent", "customer")))
                            .order_by(TranscriptTurn.start_seconds, TranscriptTurn.seq).limit(1)).first()
    return first or "not_split"
