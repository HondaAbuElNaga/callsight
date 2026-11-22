"""Retention (PDPL): off by default, N >= 30, first run dry, deletes only DB-row paths inside AUDIO_DIR (+ backup
copies), audit rows kept 2 years. Temp folders only - never real audio."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from callsight import retention
from callsight.db.models import AppUser, Call, RecordingPlay, RetentionLog, RetentionRun
from callsight.retention import RetentionConfig
from test_api import engine  # noqa: F401  (engine is a fixture)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
ON = RetentionConfig(enabled=True, days=90)


def test_config_is_off_by_default(tmp_path):
    f = tmp_path / "retention.yaml"
    f.write_text("days: null\naudit_days: 730\n", encoding="utf-8")
    assert RetentionConfig.load(f, env={}).problem().startswith("retention is off")
    assert "at least 30" in RetentionConfig.load(f, env={"RETENTION_ENABLED": "true"}).problem()
    f.write_text("days: 10\n", encoding="utf-8")
    assert "at least 30" in RetentionConfig.load(f, env={"RETENTION_ENABLED": "true"}).problem()
    f.write_text("days: 30\naudit_days: 100\n", encoding="utf-8")
    cfg = RetentionConfig.load(f, env={"RETENTION_ENABLED": "true"})
    assert cfg.problem() is None and cfg.days == 30 and cfg.audit_days == 730     # audit never below 2 years
    assert RetentionConfig.load(tmp_path / "missing.yaml", env={"RETENTION_ENABLED": "true"}).problem()


def test_shipped_config_is_off():
    from callsight.config import ROOT
    assert RetentionConfig.load(ROOT / "config" / "retention.yaml", env={}).problem().startswith("retention is off")


@pytest.fixture
def env(engine, tmp_path):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE retention_log, retention_run, recording_play, app_user, call_leg, call "
                       "RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    audio, files = tmp_path / "audio", tmp_path / "backup" / "files"
    for root in (audio, files / "audio"):
        (root / "old").mkdir(parents=True)
        (root / "new").mkdir(parents=True)
        (root / "old" / "a.wav").write_bytes(b"old")
        (root / "new" / "b.wav").write_bytes(b"new")
    (tmp_path / "outside.wav").write_bytes(b"not ours")
    (audio / "old" / "unlisted.wav").write_bytes(b"no DB row")
    with Session() as s:
        def call(uid, days, path, status="ok"):
            t = NOW - timedelta(days=days)
            c = Call(uid=uid, direction="inbound", entry="queue", start_time=t, end_time=t, status="answered",
                     recording_status=status, recording_path=path)
            s.add(c)
            return c
        old = call("old", 100, "old/a.wav")
        call("new", 10, "new/b.wav")
        call("evil", 100, "../outside.wav")
        call("norec", 100, None, "none")
        s.flush()
        u = AppUser(username="u", password_hash="x", role="admin", active=True, token_version=0, failed_logins=0)
        s.add(u)
        s.flush()
        s.add_all([RecordingPlay(user_id=u.id, call_id=old.id, played_at=NOW - timedelta(days=800)),
                   RecordingPlay(user_id=u.id, call_id=old.id, played_at=NOW - timedelta(days=10))])
        s.commit()
    return Session, audio, files, tmp_path


def test_off_does_nothing(env):
    Session, audio, files, _ = env
    with Session() as s:
        r = retention.run(s, RetentionConfig(enabled=False, days=90), audio, files, NOW)
        assert r["enabled"] is False and s.scalar(select(func.count()).select_from(RetentionRun)) == 0
    assert (audio / "old" / "a.wav").exists()


def test_first_run_is_dry_then_deletes(env):
    Session, audio, files, tmp_path = env
    with Session() as s:
        r = retention.run(s, ON, audio, files, NOW)
        assert r == {"enabled": True, "dry_run": True, "days": 90, "calls": 2, "files": 1, "audit_rows": 1,
                     "kept_rating": 0, "kept_open": 0, "snapshots": 0}
        assert (audio / "old" / "a.wav").exists() and s.scalar(select(func.count()).select_from(RecordingPlay)) == 2

        r = retention.run(s, ON, audio, files, NOW)
        assert r["dry_run"] is False and r["calls"] == 2 and r["files"] == 2
    assert not (audio / "old" / "a.wav").exists() and not (files / "audio" / "old" / "a.wav").exists()
    assert (audio / "new" / "b.wav").exists() and (files / "audio" / "new" / "b.wav").exists()
    assert (tmp_path / "outside.wav").exists()                        # path outside AUDIO_DIR never touched
    assert (audio / "old" / "unlisted.wav").exists()                  # no glob: files without a DB row stay
    with Session() as s:
        statuses = dict(s.execute(select(Call.uid, Call.recording_status)).all())
        assert statuses == {"old": "deleted", "new": "ok", "evil": "deleted", "norec": "none"}
        assert s.scalar(select(func.count()).select_from(RetentionLog)) == 2
        assert s.scalar(select(func.count()).select_from(RecordingPlay)) == 1     # only the > 2 years one went
        assert s.scalar(select(Call.recording_path).where(Call.uid == "old")) is None


def test_force_dry_run_never_deletes(env):
    Session, audio, files, _ = env
    with Session() as s:
        retention.run(s, ON, audio, files, NOW)
        r = retention.run(s, ON, audio, files, NOW, force_dry_run=True)
    assert r["dry_run"] and (audio / "old" / "a.wav").exists()


def test_changing_days_needs_a_new_dry_run(env):
    Session, audio, files, _ = env
    with Session() as s:
        retention.run(s, ON, audio, files, NOW)
        r = retention.run(s, RetentionConfig(enabled=True, days=60), audio, files, NOW)
    assert r["dry_run"] and (audio / "old" / "a.wav").exists()


def test_transcripts_of_old_calls_are_deleted(env, engine):  # noqa: F811
    Session, audio, files, _ = env
    with engine.begin() as c:
        # real table since the transcription merge (segments go with it: ON DELETE CASCADE)
        c.execute(text("INSERT INTO transcript (call_id, model, status) SELECT id, 'm', 'done' FROM call"))
    try:
        with Session() as s:
            retention.run(s, ON, audio, files, NOW)
            retention.run(s, ON, audio, files, NOW)
            left = set(s.scalars(text("SELECT c.uid FROM transcript t JOIN call c ON c.id = t.call_id")))
            what = s.scalar(select(RetentionLog.what).join(Call, Call.id == RetentionLog.call_id)
                            .where(Call.uid == "old"))
        assert left == {"new", "norec"} and what == "recording,backup_copy,transcript"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM transcript"))


def test_deleted_backup_copies_leave_the_manifest(env):
    from callsight.backup import MANIFEST, read_manifest, write_manifest
    Session, audio, files, _ = env
    write_manifest(files / MANIFEST, {"audio/old/a.wav": "aa", "audio/new/b.wav": "bb", "reports/x.pdf": "cc"})
    with Session() as s:
        retention.run(s, ON, audio, files, NOW)                       # dry run: manifest untouched
        assert len(read_manifest(files / MANIFEST)) == 3
        retention.run(s, ON, audio, files, NOW)
    assert read_manifest(files / MANIFEST) == {"audio/new/b.wav": "bb", "reports/x.pdf": "cc"}
