"""Is any work left for the GPU server? (spec 2026-11-15-aws-deploy, requirements 2-4, 10)

On AWS the GPU server is started by a schedule and powers itself off when this module says "nothing left".
"The queues are empty" is not enough: a transcript can be done while its analysis is not queued yet, and a call can
have its recording but no transcript row yet (both are queued by 5-minute beat jobs). So the count is made from the
database, with the same rules as those beat jobs:

  transcripts = rows `queued`/`running` for ASR_MODEL
              + calls the transcript beat job would still queue (since ASR_START_DATE, talk time within the limits)
  analyses    = rows `queued`/`running`
              + calls with a done transcript and no analysis row at all        (only when NLP_ENABLED=true)

DB only: no torch, no model. Used by `python -m callsight gpu-work` and the health page.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Callable

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from .analysis import queueing as nlpq
from .asr import queueing as asrq
from .config import RIYADH
from .db.models import Call, CallAnalysis, Transcript
from .health import Item

OPEN = ("queued", "running")
WAIT_WARN = timedelta(minutes=90)             # health page: a call waiting longer than this = a wake-up was missed
DAY_START, DAY_END = time(8, 0), time(23, 30)  # Riyadh time: the hours in which the GPU server is woken
QUEUE_ROUNDS = 20                             # --queue: at most 20 batches per run (20 x ASR_BATCH calls)


@dataclass(frozen=True)
class Work:
    transcripts: int                # calls waiting for a transcript
    analyses: int                   # calls waiting for an analysis
    oldest: datetime | None         # hang-up time of the call that waits longest

    @property
    def total(self) -> int:
        return self.transcripts + self.analyses


def _unqueued_transcripts(cfg: asrq.AsrConfig):
    """Calls the transcript beat job will queue (not the ones it will mark `skipped`). None = automatic
    transcription is off (ASR_START_DATE not set)."""
    if cfg.start_date is None:
        return None
    talk = func.coalesce(Call.talk_seconds, 0)
    return (select(Call.id, Call.end_time, Call.start_time)
            .where(*asrq._eligible(), asrq._no_row(cfg), Call.start_time >= cfg.start_time,
                   talk >= cfg.min_talk, talk <= cfg.max_seconds))


def _unqueued_analyses():
    """Same rule as analysis.queueing.queue_new: done transcript, no analysis row of any model."""
    return (select(Call.id, Call.end_time, Call.start_time)
            .where(nlpq._done_transcript(), ~exists().where(CallAnalysis.call_id == Call.id)))


def pending(session: Session, cfg: asrq.AsrConfig, nlp_enabled: bool) -> Work:
    waiting: dict[int, datetime] = {}

    def add(stmt) -> int:
        ids = set()
        for call_id, end, start in session.execute(stmt):
            ids.add(call_id)
            waiting[call_id] = end or start
        return len(ids)

    transcripts = add(select(Call.id, Call.end_time, Call.start_time)
                      .join(Transcript, Transcript.call_id == Call.id)
                      .where(Transcript.model == cfg.model, Transcript.status.in_(OPEN)))
    unqueued = _unqueued_transcripts(cfg)
    if unqueued is not None:
        transcripts += add(unqueued)
    analyses = 0
    if nlp_enabled:
        open_rows = (select(Call.id, Call.end_time, Call.start_time)
                     .join(CallAnalysis, CallAnalysis.call_id == Call.id).where(CallAnalysis.status.in_(OPEN)))
        analyses = add(open_rows.distinct()) + add(_unqueued_analyses())
    return Work(transcripts, analyses, min(waiting.values()) if waiting else None)


def queue_now(session: Session, cfg: asrq.AsrConfig, nlp_enabled: bool,
              queue_transcripts: Callable[[], object], queue_analyses: Callable[[], object],
              rounds: int = QUEUE_ROUNDS) -> None:
    """Run the two beat jobs now instead of waiting up to 5 minutes for them. They insert the `queued` row with
    ON CONFLICT DO NOTHING and only the inserter sends the task, so a call is never queued twice - also not when
    the beat job on the main server runs at the same moment."""
    def left(stmt) -> bool:
        return stmt is not None and session.execute(stmt.limit(1)).first() is not None

    for _ in range(rounds):
        if not left(_unqueued_transcripts(cfg)):
            break
        queue_transcripts()
    if not nlp_enabled:
        return
    for _ in range(rounds):
        if not left(_unqueued_analyses()):
            break
        queue_analyses()


def health_item(work: Work | None, now: datetime) -> Item:
    """Health page: "oldest call waiting for the GPU". Warning when a call has waited more than 90 minutes inside
    the wake-up hours (08:00-23:30 Riyadh): e.g. AWS had no free GPU server at the wake-ups. Calls that came after
    23:30 wait for 08:00 by design, so their waiting time counts from 08:00."""
    if work is None:
        return Item("gpu.wait", "off", {"waiting": None, "oldest_min": None})
    if work.oldest is None:
        return Item("gpu.wait", "ok", {"waiting": 0, "oldest_min": None})
    local = now.astimezone(RIYADH)
    waited = now - work.oldest
    in_hours = DAY_START <= local.time() <= DAY_END
    if in_hours:
        day_start = local.replace(hour=DAY_START.hour, minute=DAY_START.minute, second=0, microsecond=0)
        counted = now - max(work.oldest, day_start)
    else:
        counted = timedelta(0)
    return Item("gpu.wait", "warn" if counted > WAIT_WARN else "ok",
                {"waiting": work.total, "oldest_min": max(0, round(waited.total_seconds() / 60))})


def work_from_env(session: Session) -> Work | None:
    """For the health page: None when automatic transcription is off (ASR_START_DATE not set)."""
    from .analysis.settings import LlmConfig
    cfg = asrq.AsrConfig.from_env()
    if cfg.start_date is None:
        return None
    return pending(session, cfg, LlmConfig.from_env().enabled)
