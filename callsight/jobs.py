"""Celery tasks + schedule. Worker: celery -A callsight.jobs worker; scheduler: celery -A callsight.jobs beat."""
from __future__ import annotations

import functools
import logging
from datetime import datetime, timedelta
from functools import lru_cache

from celery import Celery
from celery.schedules import crontab
from celery.signals import beat_init, worker_ready

from . import app as wiring
from .asr import queueing
from .config import RIYADH
from .ingest import service

log = logging.getLogger(__name__)
ANALYZE_TASK, NLP_QUEUE = "callsight.analysis.tasks.analyze_call", "nlp"   # = analysis.queueing (no import cycle)
logging.getLogger("httpx").setLevel(logging.WARNING)   # request URLs contain the access token

celery = Celery("callsight", broker=wiring.settings().redis_url)
celery.conf.update(
    timezone="Asia/Riyadh",
    task_default_queue="ingest",
    worker_prefetch_multiplier=1,
    task_acks_late=False,
    # Transcription runs only in the GPU asr worker (-Q asr). Enqueued by name: this module never imports torch.
    task_routes={"callsight.jobs.report_*": {"queue": "reports"},
                 "callsight.jobs.backup_nightly": {"queue": "maint"},
                 "callsight.jobs.retention_nightly": {"queue": "maint"},
                 "callsight.jobs.delete_backup_copy": {"queue": "maint"},
                 queueing.TASK_NAME: {"queue": queueing.QUEUE},
                 ANALYZE_TASK: {"queue": NLP_QUEUE}},
    beat_schedule={
        "ingest-every-2-min": {"task": "callsight.jobs.ingest_new", "schedule": 120.0,
                               "options": {"expires": 110}},
        "recordings-every-5-min": {"task": "callsight.jobs.fetch_recordings", "schedule": 300.0,
                                   "options": {"expires": 290}},
        "reconcile-nightly": {"task": "callsight.jobs.reconcile_yesterday", "schedule": crontab(hour=2, minute=0)},
        "sync-agents-daily": {"task": "callsight.jobs.sync_agents", "schedule": crontab(hour=3, minute=0)},
        # DB dump + new recordings/reports copy, before the 02:00 reconcile (which waits while the lock is held)
        "backup-nightly": {"task": "callsight.jobs.backup_nightly", "schedule": crontab(hour=1, minute=30)},
        # PDPL retention: does nothing unless RETENTION_ENABLED=true and days >= 30 (first run = dry run)
        "retention-nightly": {"task": "callsight.jobs.retention_nightly", "schedule": crontab(hour=4, minute=30)},
        # Saudi work week starts Sunday: Sunday 07:00 = last week (Sun-Sat); 1st 07:00 = last month (Riyadh time)
        "report-weekly": {"task": "callsight.jobs.report_week", "schedule": crontab(hour=7, minute=0, day_of_week=0)},
        "report-monthly": {"task": "callsight.jobs.report_month",
                           "schedule": crontab(hour=7, minute=0, day_of_month=1)},
        "transcripts-every-5-min": {"task": "callsight.jobs.queue_transcripts", "schedule": 300.0,
                                    "options": {"expires": 290}},
        "transcripts-reaper-every-10-min": {"task": "callsight.jobs.reap_transcripts", "schedule": 600.0,
                                            "options": {"expires": 590}},
        # call analysis (LLM): does nothing unless NLP_ENABLED=true (and the llm profile runs the nlp worker)
        "analyses-every-5-min": {"task": "callsight.jobs.queue_analyses", "schedule": 300.0,
                                 "options": {"expires": 290}},
        "analyses-reaper-every-10-min": {"task": "callsight.jobs.reap_analyses", "schedule": 600.0,
                                         "options": {"expires": 590}},
    },
)


@lru_cache(maxsize=1)
def redis_client():
    import redis
    return redis.Redis.from_url(wiring.settings().redis_url)


def tracked(job: str):
    """Write start / ok / error of a scheduled job into job_run (health page). Never hides the job's own error."""
    from .health import record_job

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            def mark(status: str) -> None:
                try:
                    with wiring.session_factory()() as s:
                        record_job(s, job, status)
                except Exception:  # noqa: BLE001  bookkeeping must not break the job
                    log.warning("job_run update failed for %s", job)
            mark("started")
            try:
                result = fn(*args, **kwargs)
            except Exception:
                mark("error")
                raise
            mark("ok")
            return result
        return wrapper
    return deco


@celery.task
@tracked("ingest")
def ingest_new() -> dict:
    with wiring.session_factory()() as s:
        r = service.run_ingest(s, wiring.pbx(), wiring.teams())
    log.info("ingest: %s", r)
    if r.calls:
        fetch_recordings.delay()   # recording ready within one ingest cycle (~2 min), not the next 5-min tick
    return vars(r)


def send_transcribe(call_id: int, model: str, countdown: int | None = None) -> None:
    celery.send_task(queueing.TASK_NAME, args=[call_id, model], queue=queueing.QUEUE, countdown=countdown)


def send_analyze(call_id: int, model: str, prompt_version: str, countdown: int | None = None) -> None:
    celery.send_task(ANALYZE_TASK, args=[call_id, model, prompt_version], queue=NLP_QUEUE, countdown=countdown)


def redis_queue_length(channel, queue: str) -> int:
    """LLEN of the queue's Redis lists (kombu keeps one list per priority step). A missing key = empty queue = 0
    (a passive queue_declare would raise there and look like "broker down")."""
    steps = getattr(channel, "priority_steps", None) or [0]
    return sum(int(channel.client.llen(channel._q_for_pri(queue, p))) for p in steps)


def asr_queue_length(queue: str = queueing.QUEUE) -> int | None:
    """Messages waiting in the asr (or another) queue; None only when the broker can't be reached."""
    try:
        with celery.connection_for_read() as conn:
            return redis_queue_length(conn.default_channel, queue)
    except Exception as exc:                # noqa: BLE001  broker down / not reachable
        log.warning("%s queue length unknown: %s", queue, type(exc).__name__)
        return None


def _queue_after_recording(session, call) -> None:
    """Never breaks the recording download (Phase 1), not even with a typo in an ASR_* setting."""
    try:
        cfg = queueing.AsrConfig.from_env()
        queueing.after_recording(session, call, cfg, lambda cid: send_transcribe(cid, cfg.model))
    except Exception as exc:                # noqa: BLE001
        log.warning("transcript not queued for call %s: %s", call.id, type(exc).__name__)


@celery.task
@tracked("recordings")
def fetch_recordings() -> dict:
    with wiring.session_factory()() as s:
        ok, failed = service.fetch_recordings(s, wiring.pbx(), wiring.settings().audio_dir,
                                              after_ok=_queue_after_recording)
    log.info("recordings: %s ok, %s failed", ok, failed)
    return {"ok": ok, "failed": failed}


RECONCILE_WAIT_S = 600
RECONCILE_MAX_WAITS = 40                      # 40 x 10 min > MAINT_TTL (6 h): outlasts even a stale lock


@celery.task(bind=True, max_retries=RECONCILE_MAX_WAITS)
def reconcile_yesterday(self) -> dict:
    from .locks import maint_busy
    if maint_busy(redis_client()):            # backup/retention running: try again in 10 minutes
        if self.request.retries >= RECONCILE_MAX_WAITS:
            _reconcile_gave_up()              # never silent: job_run -> error, health page red
            raise RuntimeError("reconcile skipped: maintenance lock held for too long")
        raise self.retry(countdown=RECONCILE_WAIT_S)
    return _reconcile()


def _reconcile_gave_up() -> None:
    from .health import record_job
    try:
        with wiring.session_factory()() as s:
            record_job(s, "reconcile", "error")
    except Exception:  # noqa: BLE001
        log.warning("job_run update failed for reconcile")


@tracked("reconcile")
def _reconcile() -> dict:
    day = (datetime.now(RIYADH) - timedelta(days=1)).date()
    with wiring.session_factory()() as s:
        run = service.reconcile_day(s, wiring.pbx(), wiring.teams(), wiring.settings().audio_dir, day)
        result = {"day": str(day), "ok": run.ok, "missing_legs_after": run.missing_legs_after,
                  "missing_recordings_after": run.missing_recordings_after}
    log.info("reconcile: %s", result)
    return result


@celery.task
@tracked("sync_agents")
def sync_agents() -> dict:
    with wiring.session_factory()() as s:
        n_ext, n_q = service.sync_agents(s, wiring.pbx(), wiring.teams())
    return {"extensions": n_ext, "queues": n_q}


# ---------- scheduled PDF reports ----------

def _report(kind: str) -> dict:
    from .reports import archive
    with wiring.session_factory()() as s:
        row = archive.generate(s, kind, wiring.teams(), wiring.reports_dir())
        result = {"period": kind, "start": str(row.period_start), "file": row.file, "bytes": row.size_bytes}
    log.info("report: %s", result)
    return result


@celery.task
def report_week() -> dict:
    return _report("week")


@celery.task
def report_month() -> dict:
    return _report("month")


@celery.task
def report_catch_up() -> list[str]:
    """Latest closed week + month, only if not archived yet (the PC may have been off at 07:00)."""
    from .reports import archive
    with wiring.session_factory()() as s:
        made = [f"{r.period_type} {r.period_start}" for r in archive.catch_up(s, wiring.teams(), wiring.reports_dir())]
    log.info("report catch-up: %s", made or "nothing missing")
    return made


@beat_init.connect
def _catch_up_on_beat_start(sender=None, **kwargs) -> None:
    report_catch_up.delay()


@celery.task
def queue_transcripts() -> dict:
    """New answered calls since ASR_START_DATE -> asr queue (DB only; runs on the ingest worker)."""
    cfg = queueing.AsrConfig.from_env()
    with wiring.session_factory()() as s:
        ids = queueing.queue_new_calls(s, cfg, datetime.now(RIYADH))
    for cid in ids:
        send_transcribe(cid, cfg.model)
    return {"queued": len(ids)}


@celery.task
def reap_transcripts() -> dict:
    from .locks import maint_busy
    try:
        busy = maint_busy(redis_client())
    except Exception:                         # noqa: BLE001  Redis down: the queue length check below says so
        busy = False
    if busy:                                  # asr tasks are parked (countdown, LLEN 0) during backup: not lost
        return {"skipped": "maint"}
    cfg = queueing.AsrConfig.from_env()
    ql = asr_queue_length()
    with wiring.session_factory()() as s:
        ids = queueing.reap(s, cfg, datetime.now(RIYADH), queue_length=ql if ql is not None else 1)
    for cid in ids:
        send_transcribe(cid, cfg.model)
    return {"requeued": len(ids), "queue_length": ql}


# ---------- call analysis (queue `nlp`, spec 2026-11-14-call-analysis) ----------

def _analysis_setup():
    from .analysis import llm
    from .analysis.settings import LlmConfig, load_topics
    cfg = LlmConfig.from_env()
    return cfg, llm.prompt_version(load_topics(cfg.topics_file))


@celery.task
def queue_analyses() -> dict:
    """Freeze the blind set once the pool is big enough, then send new done transcripts to the nlp queue.
    Off unless NLP_ENABLED=true."""
    from .analysis import blindset
    from .analysis import queueing as nlpq
    cfg, pv = _analysis_setup()
    if not cfg.enabled:
        return {"enabled": False}
    with wiring.session_factory()() as s:
        frozen = blindset.ensure(s)
        ids = nlpq.queue_new(s, cfg.model, pv, cfg.batch, datetime.now(RIYADH))
    for cid in ids:
        send_analyze(cid, cfg.model, pv)
    return {"queued": len(ids), "blind_set_created": frozen}


@celery.task
def reap_analyses() -> dict:
    from .analysis import queueing as nlpq
    cfg, _ = _analysis_setup()
    if not cfg.enabled:
        return {"enabled": False}
    ql = asr_queue_length(NLP_QUEUE)
    with wiring.session_factory()() as s:
        rows = nlpq.reap(s, cfg.model, datetime.now(RIYADH), queue_length=ql if ql is not None else 1)
    for cid, row_pv in rows:
        send_analyze(cid, cfg.model, row_pv)
    return {"requeued": len(rows), "queue_length": ql}


# ---------- maintenance (queue `maint`): backup + retention ----------

@celery.task
@tracked("backup")
def backup_nightly() -> dict:
    from .backup import run_backup
    from .locks import maint_lock
    with maint_lock(redis_client()) as got:
        if not got:
            raise RuntimeError("maintenance lock held by another backup/retention run")
        with wiring.session_factory()() as s:
            run = run_backup(s, wiring.backup_config())
            result = {"id": run.id, "status": run.status, "files_copied": run.files_copied}
    if result["status"] != "ok":
        raise RuntimeError(f"backup run {result['id']} failed")    # job_run -> error, health page red
    return result


@celery.task
@tracked("retention")
def retention_nightly() -> dict:
    from . import retention
    from .locks import maint_lock
    cfg = wiring.retention_config()
    if cfg.problem():
        return {"enabled": False}
    with maint_lock(redis_client()) as got:
        if not got:
            raise RuntimeError("maintenance lock held")
        with wiring.session_factory()() as s:
            return retention.run(s, cfg, wiring.settings().audio_dir, wiring.backup_config().files_dir)


DELETE_BACKUP_TASK = "callsight.jobs.delete_backup_copy"


@celery.task(name=DELETE_BACKUP_TASK)
def delete_backup_copy(call_id: int, rel_path: str) -> dict:
    """Admin deleted a call's content on the web page: the API cannot write BACKUP_DIR, the worker removes the copy."""
    from . import retention
    return {"call_id": call_id, "deleted": retention.delete_backup_copy(wiring.backup_config().files_dir, rel_path)}


@worker_ready.connect
def _gpu_heartbeat(sender=None, **kwargs) -> None:
    """Only the GPU asr worker has torch: it publishes GPU memory to Redis for the health page."""
    from .health import start_gpu_heartbeat
    start_gpu_heartbeat(wiring.settings().redis_url)
