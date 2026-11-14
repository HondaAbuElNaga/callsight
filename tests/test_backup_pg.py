"""Real pg_dump / pg_restore round trip against the test DB (runs only where postgresql-client-16 is installed,
i.e. inside the app image). Restores into the scratch DB callsight_restore_check and checks it is dropped after."""
import shutil
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from callsight import backup
from callsight.backup import SCRATCH_DB, BackupConfig
from callsight.db.models import BackupRun, Call
from test_api import URL, engine  # noqa: F401  (engine is a fixture)

pytestmark = pytest.mark.skipif(shutil.which("pg_dump") is None, reason="pg_dump not installed (app image only)")


@pytest.fixture
def env(engine, tmp_path):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE backup_run, call_leg, call RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with Session() as s:
        s.add_all(Call(uid=f"pg{i}", direction="inbound", entry="queue", start_time=now, end_time=now,
                       status="answered") for i in range(25))
        s.commit()
    cfg = BackupConfig(database_url=URL, backup_dir=tmp_path / "backup", audio_dir=tmp_path, reports_dir=tmp_path)
    return Session, cfg, engine


def _scratch_exists(eng) -> bool:
    with eng.connect() as c:
        return bool(c.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": SCRATCH_DB}))


def test_client_major_equals_server_major(env):
    _, cfg, _ = env
    with backup.Snapshot(cfg.database_url) as snap:
        assert backup.client_major() == snap.server_major == 16
    assert backup.client_major(tool="pg_restore") == 16


def test_backup_and_restore_drill(env):
    Session, cfg, eng = env
    with Session() as s:
        run = backup.run_backup(s, cfg)
        assert run.status == "ok", run.error
        assert run.counts["call"] == 25
        res = backup.verify(s, cfg)
    assert res.ok, res.mismatches
    assert res.tables == len(run.counts)
    assert not _scratch_exists(eng)                       # personal data copy removed after the drill
    with Session() as s:
        assert s.get(BackupRun, run.id).verify_ok is True


def test_restore_drill_detects_wrong_counts(env):
    Session, cfg, eng = env
    with Session() as s:
        run = backup.run_backup(s, cfg)
        run.counts = {**run.counts, "call": 999}
        s.commit()
        res = backup.verify(s, cfg)
    assert not res.ok and res.mismatches["call"] == (999, 25)
    assert not _scratch_exists(eng)
