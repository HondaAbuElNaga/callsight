"""Jobs that wait for / respect the maintenance lock (S7 review fixes)."""
import pytest

import callsight.jobs as jobs
import callsight.locks as locks


class Busy:
    def exists(self, key):
        return 1


def test_reconcile_waits_longer_than_the_lock_ttl():
    assert jobs.RECONCILE_MAX_WAITS * jobs.RECONCILE_WAIT_S > locks.MAINT_TTL
    assert jobs.reconcile_yesterday.max_retries == jobs.RECONCILE_MAX_WAITS


def test_reconcile_gives_up_loudly(monkeypatch):
    marked = []
    monkeypatch.setattr(jobs, "redis_client", lambda: Busy())
    monkeypatch.setattr(jobs, "_reconcile_gave_up", lambda: marked.append("error"))
    monkeypatch.setattr(jobs, "_reconcile", lambda: pytest.fail("must not reconcile while the lock is held"))
    task = jobs.reconcile_yesterday
    task.push_request(retries=jobs.RECONCILE_MAX_WAITS)
    try:
        with pytest.raises(RuntimeError, match="maintenance lock"):
            task.run()
    finally:
        task.pop_request()
    assert marked == ["error"]


def test_reaper_skips_while_maintenance_runs(monkeypatch):
    monkeypatch.setattr(jobs, "redis_client", lambda: Busy())
    monkeypatch.setattr(jobs, "asr_queue_length", lambda: pytest.fail("must not look at the queue"))
    assert jobs.reap_transcripts.run() == {"skipped": "maint"}


def test_cli_retention_refuses_when_lock_held(monkeypatch, capsys):
    import contextlib
    from callsight import cli
    from callsight import app as wiring

    class FakeSession:
        def __call__(self):
            return contextlib.nullcontext(object())

    monkeypatch.setattr(wiring, "retention_config", lambda: object())
    monkeypatch.setattr(wiring, "session_factory", lambda: FakeSession())
    monkeypatch.setattr(cli, "_maint_lock", lambda: contextlib.nullcontext(False))
    import callsight.retention as retention
    monkeypatch.setattr(retention, "run", lambda *a, **k: pytest.fail("must not run without the lock"))
    assert cli._retention(type("A", (), {"dry_run": False})()) == 1
    assert "maintenance lock" in capsys.readouterr().err
