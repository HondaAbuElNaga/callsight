"""Which calls get a transcript, and when (spec 2026-11-14-transcription, decisions 2, 3, 6, 7, 10).

DB only: no torch, no model. Used by the ingest worker (hook after a recording download, beat jobs), the CLI
and the asr task. Creating a row is `INSERT ... ON CONFLICT DO NOTHING` on (call, model): only the process that
inserted the `queued` row sends the Celery message, after its commit, so a call is never queued twice.
"""
from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import RIYADH, ROOT
from ..db.models import Call, Transcript, TranscriptSegment
from .models import MODELS

log = logging.getLogger(__name__)

TASK_NAME = "callsight.asr.tasks.transcribe_call"
QUEUE = "asr"
MAX_TRIES = 2
STUCK_AFTER = timedelta(minutes=30)       # soft time limit is 15 min, so a row this old has no living task
MAX_BACKFILL = 500                        # CLI cap: ~1 night of GPU time; more = run it again
LIVE_QUEUE_WINDOW = timedelta(hours=1)    # first queued within 1 h of hang-up = a live call, not a backfill
CUSTOMER_DIRECTIONS = ("inbound", "outbound")   # internal calls between colleagues are not recorded


@dataclass(frozen=True)
class AsrConfig:
    model: str = "whisper-large-v3"
    min_talk: int = 5
    max_seconds: int = 3600
    start_date: date | None = None
    batch: int = 50
    idle_unload_s: int = 300

    @classmethod
    def from_env(cls) -> "AsrConfig":
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        start = (os.getenv("ASR_START_DATE") or "").strip()
        idle = int(os.getenv("ASR_IDLE_UNLOAD_S", "300"))
        if idle < 1:
            # 0 = "never unload": the model would stay on the GPU while its Redis GPU lock expires, and the nlp
            # task (Ollama) would then load a second model on the same 8 GB (call-analysis review).
            raise RuntimeError("ASR_IDLE_UNLOAD_S must be at least 1 second (0 = never unload is not allowed)")
        return cls(model=os.getenv("ASR_MODEL", "whisper-large-v3").strip(),
                   min_talk=int(os.getenv("ASR_MIN_TALK", "5")),
                   max_seconds=int(os.getenv("ASR_MAX_SECONDS", "3600")),
                   start_date=date.fromisoformat(start) if start else None,
                   batch=int(os.getenv("ASR_BATCH", "50")),
                   idle_unload_s=idle)

    @property
    def revision(self) -> str | None:
        cls = MODELS.get(self.model)
        return cls.revision if cls else None

    @property
    def start_time(self) -> datetime | None:
        """Riyadh midnight of ASR_START_DATE."""
        d = self.start_date
        return datetime(d.year, d.month, d.day, tzinfo=RIYADH) if d else None


def _eligible():
    """Answered customer calls whose recording is on disk."""
    return (Call.status == "answered", Call.direction.in_(CUSTOMER_DIRECTIONS), Call.recording_status == "ok")


def _no_row(cfg: AsrConfig):
    return ~exists().where(Transcript.call_id == Call.id, Transcript.model == cfg.model)


def skip_reason(call: Call, cfg: AsrConfig) -> str | None:
    if (call.talk_seconds or 0) < cfg.min_talk:
        return f"talk under {cfg.min_talk} s"
    if (call.talk_seconds or 0) > cfg.max_seconds:
        return f"longer than ASR_MAX_SECONDS ({cfg.max_seconds} s)"
    return None


def add_row(session: Session, call: Call, cfg: AsrConfig, now: datetime, force: bool = False) -> str | None:
    """Insert the transcript row (`queued`, or `skipped` with a reason). None = a row already existed.
    Caller commits, then sends the task only for "queued"."""
    reason = None if force else skip_reason(call, cfg)
    stmt = (insert(Transcript)
            .values(call_id=call.id, model=cfg.model, model_revision=cfg.revision,
                    status="skipped" if reason else "queued", error=reason, queued_at=now, first_queued_at=now,
                    finished_at=now if reason else None)
            .on_conflict_do_nothing(constraint="uq_transcript_call_model")
            .returning(Transcript.status))
    return session.scalar(stmt)


def _queue_calls(session: Session, cfg: AsrConfig, calls: list[Call], now: datetime) -> list[int]:
    queued = [c.id for c in calls if add_row(session, c, cfg, now) == "queued"]
    session.commit()
    return queued


def queue_new_calls(session: Session, cfg: AsrConfig, now: datetime) -> list[int]:
    """Beat job: newest calls since ASR_START_DATE without a row, at most ASR_BATCH. Returns call ids to send."""
    if cfg.start_date is None:
        log.warning("ASR_START_DATE is not set in .env: no calls are queued for transcription")
        return []
    calls = session.scalars(select(Call).where(*_eligible(), _no_row(cfg), Call.start_time >= cfg.start_time)
                            .order_by(Call.start_time.desc()).limit(cfg.batch)).all()
    return _queue_calls(session, cfg, calls, now)


def queue_recent(session: Session, cfg: AsrConfig, days: int, limit: int, now: datetime) -> list[int]:
    """CLI backfill: newest calls of the last `days` days, capped (ignores ASR_START_DATE on purpose)."""
    if not 1 <= limit <= MAX_BACKFILL or not 1 <= days <= 90:
        raise ValueError(f"--limit must be 1..{MAX_BACKFILL} and --days 1..90")
    calls = session.scalars(select(Call).where(*_eligible(), _no_row(cfg), Call.start_time >= now - timedelta(days=days))
                            .order_by(Call.start_time.desc()).limit(limit)).all()
    return _queue_calls(session, cfg, calls, now)


def queue_call(session: Session, cfg: AsrConfig, call_id: int, force: bool, now: datetime) -> tuple[str, bool]:
    """One call from the CLI -> (status, send?). `force` re-queues done/error/skipped and ignores length rules."""
    call = session.get(Call, call_id)
    if call is None:
        raise ValueError(f"call {call_id} not found")
    if call.recording_status != "ok" or not call.recording_path:
        raise ValueError(f"call {call_id} has no downloaded recording")
    status = add_row(session, call, cfg, now, force=force)
    if status is None:
        row = session.scalars(select(Transcript).where(Transcript.call_id == call_id, Transcript.model == cfg.model)
                              .with_for_update()).one()
        if force and row.status != "running":
            row.status, row.tries, row.error, row.queued_at, row.redo = "queued", 0, None, now, True
            row.started_at = row.finished_at = None
            status = "queued"
        else:
            session.commit()
            return row.status, False
    session.commit()
    return status, status == "queued"


def after_recording(session: Session, call: Call, cfg: AsrConfig, send: Callable[[int], object]) -> bool:
    """Hook after a recording was downloaded OK: queue at once instead of waiting for the 5-min beat.
    Never raises: the recording download must not fail because Redis is down (the reaper re-sends later)."""
    try:
        start = cfg.start_time
        if start is None or call.start_time < start or call.status != "answered" \
                or call.direction not in CUSTOMER_DIRECTIONS or call.recording_status != "ok":
            return False
        if add_row(session, call, cfg, datetime.now(RIYADH)) != "queued":
            session.commit()
            return False
        session.commit()
        send(call.id)
        return True
    except Exception as exc:                                    # noqa: BLE001
        session.rollback()
        log.warning("could not queue transcript for call %s: %s", call.id, type(exc).__name__)
        return False


def reap(session: Session, cfg: AsrConfig, now: datetime, queue_length: int) -> list[int]:
    """Rows stuck for 30 min: back to `queued` (+ re-send) while tries < 2, else `error`.
    Queued rows only count as lost when the asr queue is empty (a long backlog is not a failure)."""
    cutoff = now - STUCK_AFTER
    cond = (Transcript.status == "running") & (Transcript.started_at < cutoff)
    if not queue_length:
        cond = cond | ((Transcript.status == "queued") & (Transcript.queued_at < cutoff))
    stuck = select(Transcript).where(Transcript.model == cfg.model, cond).with_for_update(skip_locked=True)
    resend = []
    for row in session.scalars(stuck).all():
        if row.status == "running":
            give_up = row.tries >= MAX_TRIES                    # the worker died during its last allowed try
        else:
            row.tries += 1                                      # a lost message counts too: no endless re-sending
            give_up = row.tries > MAX_TRIES
        if give_up:
            row.status, row.error, row.finished_at = "error", f"no result after {row.tries} tries", now
            continue
        row.status, row.queued_at, row.started_at = "queued", now, None
        resend.append(row.call_id)
    session.commit()
    if resend:
        log.warning("transcripts re-queued after 30 min without result: %s", len(resend))
    return resend


def retry_errors(session: Session, cfg: AsrConfig, since: date, limit: int, now: datetime) -> list[int]:
    """CLI bulk recovery: `error` rows of calls since `since` (Riyadh day), newest first, capped -> queued again
    with fresh tries. Returns call ids to send."""
    if not 1 <= limit <= MAX_BACKFILL:
        raise ValueError(f"--limit must be 1..{MAX_BACKFILL}")
    start = datetime(since.year, since.month, since.day, tzinfo=RIYADH)
    rows = session.scalars(select(Transcript).join(Call, Call.id == Transcript.call_id)
                           .where(Transcript.model == cfg.model, Transcript.status == "error", Call.start_time >= start)
                           .order_by(Call.start_time.desc()).limit(limit).with_for_update(of=Transcript)).all()
    for row in rows:
        row.status, row.tries, row.error, row.queued_at, row.redo = "queued", 0, None, now, True
        row.started_at = row.finished_at = None
    session.commit()
    return [r.call_id for r in rows]


def resplit(session: Session, cfg: AsrConfig, limit: int, now: datetime, segmenter: str) -> list[int]:
    """Diarization spec: done transcripts made before word times (segmenter_version != `segmenter`) -> queued again
    (redo) so they get word times + the speaker split. Rating-set calls first, then the newest since ASR_START_DATE.
    Returns call ids to send."""
    from ..db.models import RatingPick
    if not 1 <= limit <= MAX_BACKFILL:
        raise ValueError(f"--limit must be 1..{MAX_BACKFILL}")
    old = (Transcript.model == cfg.model, Transcript.status == "done",
           Transcript.segmenter_version.is_distinct_from(segmenter))
    picked = select(RatingPick.call_id)
    rows = list(session.scalars(select(Transcript).where(*old, Transcript.call_id.in_(picked))
                                .order_by(Transcript.call_id).limit(limit).with_for_update()))
    if len(rows) < limit and cfg.start_time is not None:
        rows += session.scalars(select(Transcript).join(Call, Call.id == Transcript.call_id)
                                .where(*old, Transcript.call_id.not_in(picked), Call.start_time >= cfg.start_time)
                                .order_by(Call.start_time.desc()).limit(limit - len(rows))
                                .with_for_update(of=Transcript)).all()
    for row in rows:
        row.status, row.tries, row.error, row.queued_at, row.redo = "queued", 0, None, now, True
        row.started_at = row.finished_at = None
    session.commit()
    return [r.call_id for r in rows]


def latency_seconds(finished_at: datetime, call_end: datetime) -> float:
    """Hang-up -> transcript done (target: within 15 min)."""
    return (finished_at - call_end).total_seconds()


def status(session: Session, cfg: AsrConfig, queue_length: int | None, now: datetime, days: int = 7) -> dict:
    counts = dict(session.execute(select(Transcript.status, func.count()).where(Transcript.model == cfg.model)
                                  .group_by(Transcript.status)).all())
    since = now - timedelta(days=days)
    done = session.execute(select(Transcript.finished_at, Transcript.first_queued_at, Call.end_time)
                           .join(Call, Call.id == Transcript.call_id)
                           .where(Transcript.model == cfg.model, Transcript.status == "done",
                                  Transcript.redo.is_(False), Call.start_time >= since)).all()
    lat = sorted(latency_seconds(f, e) for f, q, e in done if q - e <= LIVE_QUEUE_WINDOW)
    return {"model": cfg.model, "start_date": cfg.start_date, "queue_length": queue_length, "counts": counts,
            "latency_days": days, "latency_n": len(lat),
            "latency_median_s": statistics.median(lat) if lat else None, "latency_max_s": lat[-1] if lat else None}


def format_status(st: dict) -> str:
    def mins(s):
        return "-" if s is None else f"{s / 60:.1f} min"
    counts = ", ".join(f"{k} {v}" for k, v in sorted(st["counts"].items())) or "none yet"
    ql = "unknown (broker not reachable)" if st["queue_length"] is None else st["queue_length"]
    return "\n".join([
        f"model: {st['model']}   ASR_START_DATE: {st['start_date'] or 'NOT SET (automatic transcription off)'}",
        f"asr queue length: {ql}",
        f"transcripts: {counts}",
        f"latency hang-up -> transcript (live calls, last {st['latency_days']} days, n={st['latency_n']}): "
        f"median {mins(st['latency_median_s'])}, max {mins(st['latency_max_s'])} (target 15 min)",
    ])


def segment_count(session: Session, transcript_id: int) -> int:
    return session.scalar(select(func.count()).select_from(TranscriptSegment)
                          .where(TranscriptSegment.transcript_id == transcript_id)) or 0


def claim(session: Session, transcript_id: int, now: datetime) -> bool:
    """queued -> running (+1 try) in one statement, so two deliveries of one message can't both run."""
    n = session.execute(update(Transcript).where(Transcript.id == transcript_id, Transcript.status == "queued")
                        .values(status="running", tries=Transcript.tries + 1, started_at=now,
                                finished_at=None, error=None)).rowcount
    session.commit()
    return n == 1
