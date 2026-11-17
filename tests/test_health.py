"""Health checks with fakes (disk, Redis, Celery ping, clock) + the admin-only /api/health endpoint."""
import json
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from callsight import health
from callsight.api import auth
from callsight.api.main import ApiConfig, create_app
from callsight.db.models import BackupRun, JobRun, ReconcileRun
from callsight.locks import MAINT_LOCK, maint_busy, maint_lock
from test_api import PASSWORD, SECRET, TEAM, engine  # noqa: F401  (engine is a fixture)


@pytest.fixture(autouse=True)
def _forward_link_off(monkeypatch):
    """These tests assume the customer-service link is off, whatever the machine's .env holds."""
    monkeypatch.delenv("CS_API_KEY", raising=False)
    monkeypatch.delenv("CS_USER_GUID", raising=False)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
Usage = namedtuple("Usage", "total used free")


class FakeRedis:
    def __init__(self, lengths=None, gpu=None, down=False):
        self.lengths, self.store, self.down = lengths or {}, {}, down
        if gpu:
            self.store[health.GPU_KEY] = gpu

    def llen(self, key):
        if self.down:
            raise ConnectionError("redis://secret-host:6379 refused")
        return self.lengths.get(key, 0)

    def get(self, key):
        if self.down:
            raise ConnectionError("down")
        return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.ttl = ex
        return True

    def exists(self, key):
        return int(key in self.store)

    def eval(self, script, n, key, token):
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


@pytest.fixture
def db(engine):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE job_run, backup_run, reconcile_run, app_user RESTART IDENTITY CASCADE"))
    return sessionmaker(engine, expire_on_commit=False)


def healthy(Session):
    with Session() as s:
        for job in health.JOBS:
            health.record_job(s, job, "ok", NOW - timedelta(minutes=1))
        s.add(BackupRun(started_at=NOW - timedelta(hours=10), finished_at=NOW - timedelta(hours=10), status="ok",
                        files_copied=0, files_bytes=0, verify_ok=True, verified_at=NOW - timedelta(hours=9)))
        for i in range(7):
            s.add(ReconcileRun(day=date(2026, 11, 13) - timedelta(days=i), pbx_team_calls=1, pbx_team_legs=1,
                               db_team_calls=1, missing_legs_before=0, missing_legs_after=0, recordings_expected=0,
                               missing_recordings_before=0, missing_recordings_after=0, ok=True))
        s.commit()


def deps(**kw) -> health.HealthDeps:
    base = dict(disk_paths={"system": Path("/c"), "audio": Path("/d")}, redis=FakeRedis(),
                ping=lambda: [{"celery@w": {"ok": "pong"}}], offsite_confirmed=True,
                usage=lambda p: Usage(1000, 500, 500), now=lambda: NOW)
    base.update(kw)
    return health.HealthDeps(**base)


def by_key(data):
    return {i["key"]: i for i in data["items"]}


def test_all_green(db):
    healthy(db)
    with db() as s:
        data = health.collect(s, deps())
    assert data["ok"] and data["problems"] == [], data["problems"]
    items = by_key(data)
    assert items["disk.audio"]["free_pct"] == 50.0 and items["workers"]["answering"] == 1
    assert items["gpu"]["state"] == "off"                                  # asr profile not running: grey
    assert items["reconcile.last7"]["state"] == "ok" and items["reconcile.last7"]["ok_count"] == 7


def test_low_disk_is_red(db):
    healthy(db)
    with db() as s:
        data = health.collect(s, deps(usage=lambda p: Usage(1000, 900, 100) if p == Path("/d") else Usage(1000, 0, 1000)))
    assert data["problems"] == ["disk.audio"]


def test_stopped_worker_and_old_jobs_are_red(db):
    healthy(db)
    with db() as s:
        health.record_job(s, "ingest", "ok", NOW - timedelta(hours=1))
        data = health.collect(s, deps(ping=lambda: []))
    assert set(data["problems"]) == {"workers", "job.ingest"}


def test_job_error_after_success_is_a_warning(db):
    healthy(db)
    with db() as s:
        health.record_job(s, "recordings", "error", NOW)
        row = s.get(JobRun, "recordings")
        assert row.last_ok_at is not None and row.last_status == "error"    # upsert kept the last success
        assert by_key(health.collect(s, deps()))["job.recordings"]["state"] == "warn"


def test_never_run_job_is_red(db):
    with db() as s:
        data = health.collect(s, deps())
    assert {"job.ingest", "job.backup", "backup.age"} <= set(data["problems"])


def test_backup_older_than_26h_is_red_and_same_disk_warns(db):
    healthy(db)
    with db() as s:
        s.add(BackupRun(started_at=NOW - timedelta(hours=30), finished_at=NOW - timedelta(hours=27), status="ok",
                        files_copied=0, files_bytes=0))
        s.commit()
        items = by_key(health.collect(s, deps(offsite_confirmed=False)))
    assert items["backup.age"]["state"] == "bad" and items["backup.age"]["hours"] == 27.0
    assert items["backup.target"]["state"] == "warn"


def test_queue_lengths_and_redis_down(db):
    healthy(db)
    with db() as s:
        items = by_key(health.collect(s, deps(redis=FakeRedis({"ingest": 3, "asr": 80}))))
        assert items["queue.ingest"]["waiting"] == 3 and items["queue.asr"]["state"] == "warn"
        data = health.collect(s, deps(redis=FakeRedis(down=True)))
    assert "queues" in data["problems"]
    assert "secret-host" not in json.dumps(data)                            # no exception text leaks


def test_gpu_heartbeat_round_trip(db):
    class Cuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def mem_get_info():
            return 3 * 2**30, 8 * 2**30

    class Torch:
        cuda = Cuda
    r = FakeRedis()
    assert health.publish_gpu(r, Torch, NOW) and r.ttl == health.GPU_TTL
    assert not health.publish_gpu(FakeRedis(), None, NOW)                   # no torch -> nothing published
    healthy(db)
    with db() as s:
        g = by_key(health.collect(s, deps(redis=r)))["gpu"]
    assert g == {"key": "gpu", "state": "ok", "running": True, "used_mb": 5120, "total_mb": 8192, "at": NOW.isoformat()}


def test_health_payload_has_no_paths(db):
    healthy(db)
    with db() as s:
        data = health.collect(s, deps(disk_paths={"audio": Path("/data/audio-secret-dir")}))
    assert "audio-secret-dir" not in json.dumps(data)


def test_tracked_jobs_write_job_run(db, monkeypatch):
    from callsight import jobs
    monkeypatch.setattr(jobs.wiring, "session_factory", lambda: db)

    @jobs.tracked("t_ok")
    def ok():
        return 1

    @jobs.tracked("t_bad")
    def bad():
        raise ValueError("job failed")
    assert ok() == 1
    with pytest.raises(ValueError):
        bad()
    with db() as s:
        a, b = s.get(JobRun, "t_ok"), s.get(JobRun, "t_bad")
        assert a.last_status == "ok" and a.last_ok_at and a.last_started_at
        assert b.last_status == "error" and b.last_error_at and b.last_ok_at is None


def test_maint_lock():
    r = FakeRedis()
    with maint_lock(r) as got:
        assert got and maint_busy(r)
        with maint_lock(r) as second:
            assert not second
        assert maint_busy(r)                                                # the loser did not release ours
    assert not maint_busy(r) and MAINT_LOCK not in r.store


# ---------- endpoint ----------

@pytest.fixture
def client(db, tmp_path):
    with db() as s:
        auth.create_user(s, "admin", PASSWORD, "admin")
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
        auth.create_user(s, "ali", PASSWORD, "agent", "119", TEAM.ext_team)
    app = create_app(db, ApiConfig(secret=SECRET, audio_dir=tmp_path, teams=lambda: TEAM,
                                   health=lambda s: health.collect(s, deps())))
    return TestClient(app)


@pytest.mark.parametrize("user,code", [("admin", 200), ("boss", 403), ("ali", 403)])
def test_health_endpoint_admin_only(client, user, code):
    assert client.get("/api/health").status_code == 401
    assert client.post("/api/login", json={"username": user, "password": PASSWORD}).status_code == 200
    r = client.get("/api/health")
    assert r.status_code == code
    if code == 200:
        assert "items" in r.json() and "ok" in r.json()
