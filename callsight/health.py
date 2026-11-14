"""Health page data (spec 2026-11-14-hardening, decision 5) + job bookkeeping + the GPU heartbeat.

Admin only. Holds no paths, hostnames or exception texts: only names, numbers, times and ok/warn/bad/off.
Every outside dependency (disk, Redis, Celery, clock) is passed in, so tests use fakes.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .db.models import BackupRun, JobRun, ReconcileRun

log = logging.getLogger(__name__)

WARN_FREE = 0.15                                  # disk: warn below 15 % free
QUEUES = ("ingest", "asr", "nlp", "reports", "maint")
QUEUE_WARN = 50                                   # waiting tasks
GPU_KEY = "callsight:gpu"                         # Redis heartbeat written by the asr worker
GPU_TTL = 90                                      # seconds; key gone -> "not running"
BACKUP_MAX_AGE = timedelta(hours=26)
# job name -> the longest normal gap between two successes
JOBS: dict[str, timedelta] = {
    "ingest": timedelta(minutes=10),
    "recordings": timedelta(minutes=20),
    "reconcile": timedelta(hours=26),
    "backup": BACKUP_MAX_AGE,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- job bookkeeping (job_run table) ----------

def record_job(session: Session, job: str, status: str, now: datetime | None = None) -> None:
    """status: started / ok / error. One row per job, updated in place."""
    now = now or _utcnow()
    values: dict[str, Any] = {"job": job}
    if status == "started":
        values["last_started_at"] = now
    else:
        values["last_status"] = status
        values["last_ok_at" if status == "ok" else "last_error_at"] = now
    stmt = insert(JobRun).values(**values)
    session.execute(stmt.on_conflict_do_update(index_elements=["job"],
                                               set_={k: stmt.excluded[k] for k in values if k != "job"}))
    session.commit()


# ---------- single checks ----------

@dataclass
class Item:
    key: str
    state: str                       # ok / warn / bad / off (grey: not running / not configured)
    value: dict = field(default_factory=dict)


def disks(paths: dict[str, Path], usage: Callable[[Path], Any] = shutil.disk_usage) -> list[Item]:
    out = []
    for name, path in paths.items():
        try:
            u = usage(path)
        except OSError:
            out.append(Item(f"disk.{name}", "off"))
            continue
        free = u.free / u.total if u.total else 0.0
        out.append(Item(f"disk.{name}", "ok" if free >= WARN_FREE else "bad",
                        {"free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1),
                         "free_pct": round(free * 100, 1)}))
    return out


def db_size(session: Session) -> Item:
    n = session.scalar(select(func.pg_database_size(func.current_database())))
    return Item("db.size", "ok", {"gb": round((n or 0) / 1e9, 2)})


def queues(redis) -> list[Item]:
    out = []
    for q in QUEUES:
        try:
            n = int(redis.llen(q))
        except Exception:  # noqa: BLE001  Redis down -> one grey item, no exception text
            return [Item("queues", "bad", {"reachable": False})]
        out.append(Item(f"queue.{q}", "ok" if n < QUEUE_WARN else "warn", {"waiting": n}))
    return out


def jobs(session: Session, now: datetime) -> list[Item]:
    rows = {r.job: r for r in session.scalars(select(JobRun))}
    out = []
    for job, gap in JOBS.items():
        r = rows.get(job)
        last_ok = r.last_ok_at if r else None
        if last_ok is None:
            state = "bad"
        else:
            state = "ok" if now - last_ok <= gap else "bad"
            if state == "ok" and r.last_status == "error":
                state = "warn"
        out.append(Item(f"job.{job}", state, {
            "last_ok": last_ok.isoformat() if last_ok else None,
            "last_error": r.last_error_at.isoformat() if r and r.last_error_at else None,
            "max_gap_hours": round(gap.total_seconds() / 3600, 2)}))
    return out


def workers(ping: Callable[[], list]) -> Item:
    try:
        replies = ping() or []
    except Exception:  # noqa: BLE001
        replies = []
    return Item("workers", "ok" if replies else "bad", {"answering": len(replies)})


def gpu(redis, now: datetime) -> Item:
    try:
        raw = redis.get(GPU_KEY)
    except Exception:  # noqa: BLE001
        raw = None
    if not raw:
        return Item("gpu", "off", {"running": False})
    try:
        d = json.loads(raw)
        used, total = int(d["used_mb"]), int(d["total_mb"])
    except (ValueError, KeyError, TypeError):
        return Item("gpu", "off", {"running": False})
    return Item("gpu", "ok", {"running": True, "used_mb": used, "total_mb": total, "at": d.get("at")})


def backup(session: Session, now: datetime, offsite_confirmed: bool) -> list[Item]:
    last_ok = session.scalars(select(BackupRun).where(BackupRun.status == "ok")
                              .order_by(BackupRun.id.desc()).limit(1)).first()
    last = session.scalars(select(BackupRun).order_by(BackupRun.id.desc()).limit(1)).first()
    age_h = (now - last_ok.finished_at).total_seconds() / 3600 if last_ok and last_ok.finished_at else None
    state = "ok" if age_h is not None and age_h <= BACKUP_MAX_AGE.total_seconds() / 3600 else "bad"
    items = [Item("backup.age", state, {
        "hours": round(age_h, 1) if age_h is not None else None,
        "last_failed": bool(last and last.status == "error"),
        "verified_ok": last_ok.verify_ok if last_ok else None,
        "verified_at": last_ok.verified_at.isoformat() if last_ok and last_ok.verified_at else None})]
    # Permanent until the user moves BACKUP_DIR to an encrypted second disk/NAS (decision 2)
    items.append(Item("backup.target", "ok" if offsite_confirmed else "warn", {"offsite": offsite_confirmed}))
    return items


def reconciles(session: Session, n: int = 7) -> Item:
    latest: dict = {}
    for day, ok in session.execute(select(ReconcileRun.day, ReconcileRun.ok)
                                   .order_by(ReconcileRun.day.desc(), ReconcileRun.id.desc()).limit(n * 10)):
        latest.setdefault(day, ok)
    days = sorted(latest, reverse=True)[:n]
    runs = [{"day": d.isoformat(), "ok": latest[d]} for d in days]
    good = len(runs) == n and all(r["ok"] for r in runs)
    return Item("reconcile.last7", "ok" if good else "warn", {"runs": runs, "ok_count": sum(r["ok"] for r in runs)})


# ---------- everything ----------

@dataclass
class HealthDeps:
    disk_paths: dict[str, Path]
    redis: Any
    ping: Callable[[], list]
    offsite_confirmed: bool = False
    usage: Callable[[Path], Any] = shutil.disk_usage
    now: Callable[[], datetime] = _utcnow


def collect(session: Session, deps: HealthDeps) -> dict:
    now = deps.now()
    items = [*disks(deps.disk_paths, deps.usage), db_size(session), *queues(deps.redis), *jobs(session, now),
             workers(deps.ping), gpu(deps.redis, now), *backup(session, now, deps.offsite_confirmed),
             reconciles(session)]
    problems = [i.key for i in items if i.state == "bad"]
    return {"ok": not problems, "problems": problems, "checked_at": now.isoformat(),
            "items": [{"key": i.key, "state": i.state, **i.value} for i in items]}


# ---------- GPU heartbeat (asr worker only) ----------

def gpu_payload(torch, now: datetime) -> str | None:
    """JSON for the Redis heartbeat, or None without a CUDA device."""
    if torch is None or not torch.cuda.is_available():
        return None
    free, total = torch.cuda.mem_get_info()
    return json.dumps({"used_mb": (total - free) // 2**20, "total_mb": total // 2**20, "at": now.isoformat()})


def publish_gpu(redis, torch, now: datetime | None = None) -> bool:
    payload = gpu_payload(torch, now or _utcnow())
    if payload is None:
        return False
    redis.set(GPU_KEY, payload, ex=GPU_TTL)
    return True


def start_gpu_heartbeat(redis_url: str, every: float = 30.0) -> threading.Thread | None:
    """Started by the Celery `worker_ready` signal; does nothing in workers without torch (ingest/maint)."""
    import importlib.util
    if importlib.util.find_spec("torch") is None:
        return None

    def loop() -> None:
        import redis as redis_lib
        import torch
        r = redis_lib.Redis.from_url(redis_url)
        while True:
            try:
                publish_gpu(r, torch)
            except Exception:  # noqa: BLE001  a heartbeat must never kill the worker
                log.debug("gpu heartbeat failed", exc_info=True)
            time.sleep(every)

    t = threading.Thread(target=loop, name="gpu-heartbeat", daemon=True)
    t.start()
    return t


def offsite_confirmed() -> bool:
    return os.getenv("BACKUP_OFFSITE_CONFIRMED", "false").lower() in ("1", "true", "yes")
