"""Celery tasks + schedule. Worker: celery -A callsight.jobs worker; scheduler: celery -A callsight.jobs beat."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import Celery
from celery.schedules import crontab

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
    beat_schedule={
        "ingest-every-2-min": {"task": "callsight.jobs.ingest_new", "schedule": 120.0,
                               "options": {"expires": 110}},
        "recordings-every-5-min": {"task": "callsight.jobs.fetch_recordings", "schedule": 300.0,
                                   "options": {"expires": 290}},
        "reconcile-nightly": {"task": "callsight.jobs.reconcile_yesterday", "schedule": crontab(hour=2, minute=0)},
        "sync-agents-daily": {"task": "callsight.jobs.sync_agents", "schedule": crontab(hour=3, minute=0)},
    },
)


@celery.task
def ingest_new() -> dict:
    with wiring.session_factory()() as s:
        r = service.run_ingest(s, wiring.pbx(), wiring.teams())
    log.info("ingest: %s", r)
    return vars(r)


@celery.task
def fetch_recordings() -> dict:
    with wiring.session_factory()() as s:
        ok, failed = service.fetch_recordings(s, wiring.pbx(), wiring.settings().audio_dir)
    log.info("recordings: %s ok, %s failed", ok, failed)
    return {"ok": ok, "failed": failed}


@celery.task
def reconcile_yesterday() -> dict:
    day = (datetime.now(RIYADH) - timedelta(days=1)).date()
    with wiring.session_factory()() as s:
        run = service.reconcile_day(s, wiring.pbx(), wiring.teams(), wiring.settings().audio_dir, day)
        result = {"day": str(day), "ok": run.ok, "missing_legs_after": run.missing_legs_after,
                  "missing_recordings_after": run.missing_recordings_after}
    log.info("reconcile: %s", result)
    return result


@celery.task
def sync_agents() -> dict:
    with wiring.session_factory()() as s:
        n_ext, n_q = service.sync_agents(s, wiring.pbx(), wiring.teams())
    return {"extensions": n_ext, "queues": n_q}
