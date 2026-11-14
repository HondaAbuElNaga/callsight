"""Which calls get an analysis, and when. DB only (no HTTP, no model): used by the ingest/asr workers, beat, CLI.

Same pattern as transcription: `INSERT ... ON CONFLICT DO NOTHING` on (call, model, prompt_version) creates the
`queued` row; only the process that inserted it sends the Celery message (after its commit). Analysis is idempotent
per (call, model, prompt_version) (requirement 6).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db.models import Call, CallAnalysis, Transcript

log = logging.getLogger(__name__)

TASK_NAME = "callsight.analysis.tasks.analyze_call"
QUEUE = "nlp"
MAX_TRIES = 2
STUCK_AFTER = timedelta(minutes=30)
MAX_BACKFILL = 500


def _done_transcript():
    return exists().where(Transcript.call_id == Call.id, Transcript.status == "done")


def add_row(session: Session, call_id: int, model: str, pv: str, now: datetime) -> str | None:
    """Insert the `queued` row. None = a row for this (call, model, prompt_version) existed. Caller commits."""
    stmt = (insert(CallAnalysis)
            .values(call_id=call_id, model=model, prompt_version=pv, status="queued", queued_at=now)
            .on_conflict_do_nothing(constraint="uq_call_analysis_version")
            .returning(CallAnalysis.status))
    return session.scalar(stmt)


def _queue(session: Session, ids: list[int], model: str, pv: str, now: datetime) -> list[int]:
    queued = [cid for cid in ids if add_row(session, cid, model, pv, now) == "queued"]
    session.commit()
    return queued


def queue_new(session: Session, model: str, pv: str, batch: int, now: datetime) -> list[int]:
    """Beat job: newest calls with a done transcript and no analysis row at all (old calls only via --rescore)."""
    no_row = ~exists().where(CallAnalysis.call_id == Call.id)
    ids = list(session.scalars(select(Call.id).where(_done_transcript(), no_row)
                               .order_by(Call.start_time.desc()).limit(batch)))
    return _queue(session, ids, model, pv, now)


def queue_recent(session: Session, model: str, pv: str, days: int, limit: int, now: datetime,
                 rescore: bool = False) -> list[int]:
    """CLI backfill: newest calls of the last `days` days with a done transcript and no analysis yet; with
    `rescore` (decision 13): no analysis by the current model + prompt version (after a model/prompt change)."""
    if not 1 <= limit <= MAX_BACKFILL or not 1 <= days <= 400:
        raise ValueError(f"--limit must be 1..{MAX_BACKFILL} and --days 1..400")
    if rescore:
        current = ~exists().where(CallAnalysis.call_id == Call.id, CallAnalysis.model == model,
                                  CallAnalysis.prompt_version == pv)
    else:
        current = ~exists().where(CallAnalysis.call_id == Call.id)
    ids = list(session.scalars(select(Call.id).where(_done_transcript(), current,
                                                     Call.start_time >= now - timedelta(days=days))
                               .order_by(Call.start_time.desc()).limit(limit)))
    return _queue(session, ids, model, pv, now)


def queue_call(session: Session, call_id: int, model: str, pv: str, force: bool, now: datetime) -> tuple[str, bool]:
    """One call from the CLI -> (status, send?). `force` re-queues a done/error/skipped row."""
    if session.get(Call, call_id) is None:
        raise ValueError(f"call {call_id} not found")
    if not session.scalar(select(exists().where(Transcript.call_id == call_id, Transcript.status == "done"))):
        raise ValueError(f"call {call_id} has no done transcript")
    status = add_row(session, call_id, model, pv, now)
    if status is None:
        row = session.scalars(select(CallAnalysis).where(
            CallAnalysis.call_id == call_id, CallAnalysis.model == model, CallAnalysis.prompt_version == pv)
            .with_for_update()).one()
        if force and row.status != "running":
            row.status, row.tries, row.error, row.queued_at = "queued", 0, None, now
            row.started_at = row.finished_at = None
            status = "queued"
        else:
            session.commit()
            return row.status, False
    session.commit()
    return status, status == "queued"


def after_transcript(session: Session, call_id: int, model: str, pv: str, send: Callable[[int], object]) -> bool:
    """Hook when a transcript became done (asr worker). Never raises: the transcript must not fail because of it."""
    try:
        if add_row(session, call_id, model, pv, datetime.now().astimezone()) != "queued":
            session.commit()
            return False
        session.commit()
        send(call_id)
        return True
    except Exception as exc:                                    # noqa: BLE001
        session.rollback()
        log.warning("could not queue analysis for call %s: %s", call_id, type(exc).__name__)
        return False


def claim(session: Session, row_id: int, now: datetime) -> bool:
    """queued -> running (+1 try) in one statement: two deliveries of one message can't both run."""
    n = session.execute(update(CallAnalysis).where(CallAnalysis.id == row_id, CallAnalysis.status == "queued")
                        .values(status="running", tries=CallAnalysis.tries + 1, started_at=now,
                                finished_at=None, error=None)).rowcount
    session.commit()
    return n == 1


def reap(session: Session, model: str, now: datetime, queue_length: int) -> list[tuple[int, str]]:
    """Rows stuck for 30 min: queued again (+ re-send) while tries < 2, else `error`. Queued rows only count as
    lost when the nlp queue is empty."""
    cutoff = now - STUCK_AFTER
    cond = (CallAnalysis.status == "running") & (CallAnalysis.started_at < cutoff)
    if not queue_length:
        cond = cond | ((CallAnalysis.status == "queued") & (CallAnalysis.queued_at < cutoff))
    rows = session.scalars(select(CallAnalysis).where(CallAnalysis.model == model, cond)
                           .with_for_update(skip_locked=True)).all()
    resend = []
    for row in rows:
        if row.status == "queued":
            row.tries += 1
        if row.tries >= MAX_TRIES + (row.status == "queued"):
            row.status, row.error, row.finished_at = "error", f"no result after {row.tries} tries", now
            continue
        row.status, row.queued_at, row.started_at = "queued", now, None
        resend.append((row.call_id, row.prompt_version))
    session.commit()
    if resend:
        log.warning("analyses re-queued after 30 min without result: %s", len(resend))
    return resend


def status_counts(session: Session, model: str, pv: str) -> dict:
    return dict(session.execute(select(CallAnalysis.status, func.count()).where(
        CallAnalysis.model == model, CallAnalysis.prompt_version == pv).group_by(CallAnalysis.status)).all())
