"""The asr task waits while backup/retention holds the maintenance lock (merge of transcription + hardening)."""
from callsight.asr import tasks


def test_transcribe_waits_while_maintenance_runs(monkeypatch):
    sent = []
    monkeypatch.setattr(tasks, "_maint_busy", lambda: True)
    monkeypatch.setattr(tasks, "holder", lambda: type("H", (), {"name": "whisper-large-v3"})())
    monkeypatch.setattr(tasks, "send_transcribe", lambda cid, model, countdown=0: sent.append((cid, model, countdown)))
    monkeypatch.setattr(tasks, "process", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    r = tasks.transcribe_call.run(7)
    assert r == {"call_id": 7, "status": "queued", "maint_wait": True}
    assert sent == [(7, "whisper-large-v3", tasks.MAINT_WAIT_S)]


def test_redis_trouble_never_blocks_transcription(monkeypatch):
    import callsight.jobs as jobs

    def boom():
        raise ConnectionError("redis down")
    monkeypatch.setattr(jobs, "redis_client", boom)
    assert tasks._maint_busy() is False
