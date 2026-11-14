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
from .config import RIYADH
from .ingest import service

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)   # request URLs contain the access token

celery = Celery("callsight", broker=wiring.settings().redis_url)
celery.conf.update(
    timezone="Asia/Riyadh",
    task_default_queue="ingest",
    worker_prefetch_multiplier=1,
    task_acks_late=False,
    task_routes={"callsight.jobs.report_*": {"queue": "reports"},
                 "callsight.jobs.backup_nightly": {"queue": "maint"},
                 "callsight.jobs.retention_nightly": {"queue": "maint"}},
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


@celery.task
@tracked("recordings")
def fetch_recordings() -> dict:
    with wiring.session_factory()() as s:
        ok, failed = service.fetch_recordings(s, wiring.pbx(), wiring.settings().audio_dir)
    log.info("recordings: %s ok, %s failed", ok, failed)
    return {"ok": ok, "failed": failed}


@celery.task(bind=True, max_retries=12)
def reconcile_yesterday(self) -> dict:
    from .locks import maint_busy
    if maint_busy(redis_client()):            # backup/retention running: try again in 10 minutes
        raise self.retry(countdown=600)
    return _reconcile()


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


@worker_ready.connect
def _gpu_heartbeat(sender=None, **kwargs) -> None:
    """Only the GPU asr worker has torch: it publishes GPU memory to Redis for the health page."""
    from .health import start_gpu_heartbeat
    start_gpu_heartbeat(wiring.settings().redis_url)
