"""Backup logic (spec 2026-11-14-hardening, decisions 2-3) with fake pg_dump/pg_restore: rotation, file copy +
manifest + tmp/rename, snapshot counts against the test DB, verify refusals and scratch-DB clean-up.
The real pg_dump/pg_restore round trip is in test_backup_pg.py (Docker image only)."""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from callsight import backup
from callsight.backup import (BackupConfig, BackupError, CopyResult, SCRATCH_DB, copy_new, file_sha256,
                              read_manifest, rotation_victims)
from callsight.db.models import BackupRun, Call, ReportArchive
from test_api import URL, engine  # noqa: F401  (engine is a fixture)


# ---------- rotation ----------

def _name(t: datetime) -> str:
    return f"callsight-{t:%Y%m%d-%H%M%S}.dump"


def test_rotation_keeps_14_daily_and_8_weekly():
    start = datetime(2026, 1, 1, 1, 30)
    names = [_name(start + timedelta(days=i)) for i in range(120)]
    names.append(_name(start + timedelta(days=119, hours=2)))       # second run on the newest day
    victims = set(rotation_victims(names))
    kept = sorted(set(names) - victims)
    newest_days = {(start + timedelta(days=119 - i)).date() for i in range(14)}
    assert {datetime.strptime(n[10:25], "%Y%m%d-%H%M%S").date() for n in kept} >= newest_days
    assert _name(start + timedelta(days=119, hours=2)) in kept       # newest always kept
    assert _name(start + timedelta(days=119)) in victims              # older run of the same day goes
    # 14 days span 2-3 weeks; 8 weeks in total -> at most 14 + 8 files
    assert 14 + 5 <= len(kept) <= 14 + 8
    weeks = {backup._week_start(datetime.strptime(n[10:25], "%Y%m%d-%H%M%S").date()) for n in kept}
    assert len(weeks) == 8


def test_rotation_never_touches_foreign_files():
    start = datetime(2026, 1, 1)
    names = [_name(start + timedelta(days=i)) for i in range(60)]
    foreign = ["callsight-20250101-000000.dump.tmp", "other.dump", "callsight-latest.dump", "notes.txt",
               "callsight-2025-01-01.dump"]
    assert not set(rotation_victims(names + foreign)) & set(foreign)


def test_rotate_deletes_only_victims(tmp_path):
    start = datetime(2026, 1, 1)
    for i in range(40):
        (tmp_path / _name(start + timedelta(days=i))).write_bytes(b"x")
    (tmp_path / "mine.dump").write_bytes(b"keep")
    removed = backup.rotate(tmp_path)
    assert removed and all(not (tmp_path / n).exists() for n in removed)
    assert (tmp_path / "mine.dump").exists()
    assert len(list(tmp_path.glob("callsight-*.dump"))) == 40 - len(removed)


# ---------- files ----------

def _tree(tmp_path):
    src, dst = tmp_path / "audio", tmp_path / "backup" / "files"
    (src / "2026/11/01").mkdir(parents=True)
    (src / "2026/11/01/a.wav").write_bytes(b"A" * 1000)
    (src / "2026/11/01/b.wav").write_bytes(b"B" * 500)
    (tmp_path / "outside.wav").write_bytes(b"secret")
    return src, dst


def test_copy_new_then_unchanged_then_changed(tmp_path):
    src, dst = _tree(tmp_path)
    manifest: dict = {}
    rels = ["2026/11/01/a.wav", "2026/11/01/b.wav"]
    r = copy_new(src, rels, dst, "audio", manifest)
    assert (r.copied, r.unchanged, r.bytes) == (2, 0, 1500)
    assert (dst / "audio/2026/11/01/a.wav").read_bytes() == b"A" * 1000
    assert manifest["audio/2026/11/01/a.wav"] == file_sha256(src / "2026/11/01/a.wav")
    assert not list(dst.rglob("*.tmp"))

    r = copy_new(src, rels, dst, "audio", manifest)
    assert (r.copied, r.unchanged) == (0, 2)

    (src / "2026/11/01/b.wav").write_bytes(b"C" * 700)               # changed size -> copied again
    r = copy_new(src, rels, dst, "audio", manifest)
    assert (r.copied, r.unchanged) == (1, 1)
    assert manifest["audio/2026/11/01/b.wav"] == file_sha256(src / "2026/11/01/b.wav")


def test_copy_mtime_within_two_seconds_is_unchanged(tmp_path):
    src, dst = _tree(tmp_path)
    copy_new(src, ["2026/11/01/a.wav"], dst, "audio", {})
    copy = dst / "audio/2026/11/01/a.wav"
    st = copy.stat()
    os.utime(copy, (st.st_atime, st.st_mtime + 1.5))
    assert copy_new(src, ["2026/11/01/a.wav"], dst, "audio", {}).unchanged == 1
    os.utime(copy, (st.st_atime, st.st_mtime + 5))
    assert copy_new(src, ["2026/11/01/a.wav"], dst, "audio", {}).copied == 1


def test_copy_refuses_paths_outside_and_counts_missing(tmp_path):
    src, dst = _tree(tmp_path)
    r = copy_new(src, ["../outside.wav", "2026/11/01/gone.wav", "2026/11/01/a.wav"], dst, "audio", {})
    assert (r.refused, r.missing, r.copied) == (1, 1, 1)
    assert not list(dst.rglob("outside.wav"))


def test_failed_copy_leaves_no_tmp_and_no_half_file(tmp_path, monkeypatch):
    src, dst = _tree(tmp_path)

    def boom(a, b):
        raise OSError("disk full")
    monkeypatch.setattr(backup.os, "replace", boom)
    with pytest.raises(OSError):
        copy_new(src, ["2026/11/01/a.wav"], dst, "audio", {})
    assert not list(dst.rglob("*.tmp")) and not (dst / "audio/2026/11/01/a.wav").exists()


def test_manifest_round_trip(tmp_path):
    m = {"audio/x.wav": "ab" * 32, "reports/w.pdf": "cd" * 32}
    backup.write_manifest(tmp_path / "manifest.sha256", m)
    assert read_manifest(tmp_path / "manifest.sha256") == m
    assert not (tmp_path / "manifest.sha256.tmp").exists()


# ---------- DB: snapshot counts + run_backup with a fake pg_dump ----------

@pytest.fixture
def db(engine, tmp_path):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE backup_run, report_archive, call_leg, call RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    audio, reports = tmp_path / "audio", tmp_path / "reports"
    (audio / "2026/11/01").mkdir(parents=True)
    reports.mkdir()
    (audio / "2026/11/01/r1.wav").write_bytes(b"R1" * 100)
    (reports / "week-2026-11-01.pdf").write_bytes(b"%PDF")
    now = datetime.now(timezone.utc)
    with Session() as s:
        for i, (status, path) in enumerate([("ok", "2026/11/01/r1.wav"), ("pending", None), ("ok", "2026/11/01/gone.wav")]):
            s.add(Call(uid=f"b{i}", direction="inbound", entry="queue", start_time=now, end_time=now,
                       status="answered", recording_status=status, recording_path=path))
        s.add(ReportArchive(period_type="week", period_start=now.date(), period_end=now.date(), scope="all",
                            file="week-2026-11-01.pdf", size_bytes=4))
        s.commit()
    cfg = BackupConfig(database_url=URL, backup_dir=tmp_path / "backup", audio_dir=audio, reports_dir=reports)
    return Session, cfg


class FakePg:
    """Stands in for subprocess.run: answers --version and writes a dump file for pg_dump."""
    def __init__(self, major=16, fail=False):
        self.major, self.fail, self.calls = major, fail, []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if args[-1] == "--version":
            return subprocess.CompletedProcess(args, 0, f"{args[0]} (PostgreSQL) {self.major}.6 (Debian)\n", "")
        if args[0] == "pg_dump":
            out = Path(args[args.index("-f") + 1])
            out.write_bytes(b"PGDMP fake")
            return subprocess.CompletedProcess(args, 1 if self.fail else 0, "", "boom: host secret-host" if self.fail else "")
        raise AssertionError(args)


def test_snapshot_counts_every_table(db):
    Session, cfg = db
    with backup.Snapshot(cfg.database_url) as snap:
        assert snap.id and snap.server_major >= 16
        assert snap.counts["call"] == 3 and snap.counts["report_archive"] == 1
        assert "backup_run" in snap.counts and snap.db_bytes > 0


def test_run_backup_ok(db):
    Session, cfg = db
    pg = FakePg()
    with Session() as s:
        run = backup.run_backup(s, cfg, runner=pg)
    assert run.status == "ok", run.error
    assert (cfg.db_dir / run.dump_file).read_bytes() == b"PGDMP fake"
    assert not list(cfg.backup_dir.rglob("*.tmp"))
    assert run.counts["call"] == 3 and run.files_copied == 2               # r1.wav + the report (gone.wav missing)
    assert (cfg.files_dir / "audio/2026/11/01/r1.wav").is_file()
    assert (cfg.files_dir / "reports/week-2026-11-01.pdf").is_file()
    assert set(read_manifest(cfg.files_dir / "manifest.sha256")) == {"audio/2026/11/01/r1.wav",
                                                                    "reports/week-2026-11-01.pdf"}
    dump_args, dump_kw = next(c for c in pg.calls if c[0][0] == "pg_dump" and c[0][-1] != "--version")
    assert "-Fc" in dump_args and any(a.startswith("--snapshot=") for a in dump_args)
    assert not any(cfg.database_url.split("@")[0].split(":")[-1] in a for a in dump_args)   # no password in args
    assert dump_kw["env"]["PGDATABASE"] == URL.rsplit("/", 1)[1]


def test_run_backup_refuses_other_client_major(db):
    Session, cfg = db
    with Session() as s:
        run = backup.run_backup(s, cfg, runner=FakePg(major=15))
    assert run.status == "error" and "cannot safely dump" in run.error
    assert not cfg.db_dir.exists() or not list(cfg.db_dir.iterdir())


def test_run_backup_failed_dump_leaves_no_tmp_and_short_error(db):
    Session, cfg = db
    with Session() as s:
        run = backup.run_backup(s, cfg, runner=FakePg(fail=True))
        assert s.scalar(select(BackupRun.status).where(BackupRun.id == run.id)) == "error"
    assert run.error == "pg_dump failed (exit 1)"                         # no stderr text, no host name
    assert not list(cfg.db_dir.iterdir())


def test_run_backup_needs_free_space(db):
    Session, cfg = db
    with Session() as s:
        run = backup.run_backup(s, cfg, runner=FakePg(), free_bytes=lambda p: 10)
    assert run.status == "error" and "free disk" in run.error


# ---------- verify ----------

def test_verify_refuses_live_db_named_like_scratch(tmp_path):
    cfg = BackupConfig(database_url=f"postgresql+psycopg://u:p@h:5432/{SCRATCH_DB}", backup_dir=tmp_path,
                       audio_dir=tmp_path, reports_dir=tmp_path)

    def never(*a, **k):
        raise AssertionError("must not connect")
    with pytest.raises(BackupError, match="refusing"):
        backup.verify(None, cfg, runner=never, connect=never)


def test_verify_needs_a_finished_dump(db):
    Session, cfg = db
    with Session() as s, pytest.raises(BackupError, match="no backup dump"):
        backup.verify(s, cfg, runner=FakePg(), connect=lambda *a, **k: None)
    cfg.db_dir.mkdir(parents=True)
    (cfg.db_dir / "callsight-20261114-013000.dump").write_bytes(b"half")      # no sidecar = pg_dump never finished
    with Session() as s, pytest.raises(BackupError, match="no backup dump"):
        backup.verify(s, cfg, runner=FakePg(), connect=lambda *a, **k: None)


def test_backup_without_record_writes_nothing_to_the_db(db):
    Session, cfg = db
    with Session() as s:
        run = backup.run_backup(s, cfg, runner=FakePg(), record=False)
        assert run.status == "ok" and run.id is None
        assert s.scalar(select(BackupRun.id)) is None
    meta = json.loads((cfg.db_dir / (run.dump_file + ".json")).read_text(encoding="utf-8"))
    assert meta["counts"]["call"] == 3 and meta["alembic_version"] is None


def test_rotation_removes_sidecars_too(tmp_path):
    start = datetime(2026, 1, 1)
    for i in range(40):
        n = _name(start + timedelta(days=i))
        (tmp_path / n).write_bytes(b"x")
        (tmp_path / (n + ".json")).write_text("{}")
    removed = backup.rotate(tmp_path)
    assert all(not (tmp_path / (n + ".json")).exists() for n in removed)
    assert len(list(tmp_path.glob("*.json"))) == 40 - len(removed)


class FakeConn:
    def __init__(self, log, counts):
        self.log, self.counts = log, counts

    def cursor(self):
        return self

    def execute(self, sql, *a):
        self.log.append(str(sql) if isinstance(sql, str) else sql.as_string(None) if hasattr(sql, "as_string") else str(sql))
        self._last = self.log[-1]

    def fetchall(self):
        return [(t,) for t in self.counts]

    def fetchone(self):
        return (0,)

    def close(self):
        self.log.append("close")


def test_verify_drops_scratch_before_and_after_even_on_failure(db):
    Session, cfg = db
    with Session() as s:
        backup.run_backup(s, cfg, runner=FakePg())
    log: list = []
    seen_dbs = []

    def connect(url, database=None, autocommit=False):
        seen_dbs.append(database)
        return FakeConn(log, {})

    def restore(args, **kw):
        if args[-1] == "--version":
            return subprocess.CompletedProcess(args, 0, "pg_restore (PostgreSQL) 16.6\n", "")
        assert args[args.index("-d") + 1] == SCRATCH_DB and "--no-owner" in args and "--no-acl" in args
        return subprocess.CompletedProcess(args, 1, "", "error")
    with Session() as s, pytest.raises(BackupError, match="pg_restore failed"):
        backup.verify(s, cfg, runner=restore, connect=connect)
    drops = [q for q in log if q.startswith("DROP DATABASE")]
    assert drops == [f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'] * 2
    assert seen_dbs == ["postgres"]                                         # never the live DB
    assert not any(URL.rsplit("/", 1)[1] in q for q in log)
