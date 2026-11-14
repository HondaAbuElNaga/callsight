"""Warning flags from config/rules.yaml word lists (no LLM, decision 10).

Phrases and transcript windows go through the same `search_normalize` as the word search; a phrase matches only
as whole words. Dropped / hallucination-flagged windows are never searched. Each hit keeps its window time so the
supervisor can listen ("needs listening"). Flags are never counted in KPIs.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import CallFlag, Transcript, TranscriptSegment
from ..transcripts import search_normalize
from .settings import Rules


@dataclass(frozen=True)
class Hit:
    kind: str
    phrase: str
    start: float
    end: float


def find(segments, rules: Rules) -> list[Hit]:
    """One hit per (kind, phrase, window)."""
    phrases = [(kind, p, f" {search_normalize(p)} ") for kind, ps in rules.flags.items() for p in ps]
    phrases = [(k, p, n) for k, p, n in phrases if n.strip()]
    hits = []
    for s in segments:
        if s.flag or not (s.text or "").strip():
            continue
        text = f" {search_normalize(s.text)} "
        hits += [Hit(kind, p, s.start_seconds, s.end_seconds) for kind, p, n in phrases if n in text]
    return hits


def store(session: Session, call_id: int, transcript: Transcript, rules: Rules) -> int:
    """Replace the call's flags with a fresh match of its displayed transcript. Caller commits. Returns count."""
    session.execute(delete(CallFlag).where(CallFlag.call_id == call_id))
    segs = session.scalars(select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id)
                           .order_by(TranscriptSegment.seq)).all()
    hits = find(segs, rules)
    session.add_all(CallFlag(call_id=call_id, transcript_id=transcript.id, kind=h.kind, phrase=h.phrase,
                             start_seconds=h.start, end_seconds=h.end, rules_version=rules.version) for h in hits)
    return len(hits)


def recompute(session: Session, rules: Rules, asr_model: str, call_id: int | None = None, batch: int = 200) -> int:
    """`flags --recompute`: every call with a done transcript (or one call). Returns calls processed."""
    from ..transcripts import displayed
    q = select(Transcript.call_id).where(Transcript.status == "done").distinct().order_by(Transcript.call_id)
    if call_id is not None:
        q = q.where(Transcript.call_id == call_id)
    ids = list(session.scalars(q))
    for i, cid in enumerate(ids, 1):
        t = displayed(session, cid, asr_model)
        if t is not None and t.status == "done":
            store(session, cid, t, rules)
        if i % batch == 0:
            session.commit()
    session.commit()
    return len(ids)
