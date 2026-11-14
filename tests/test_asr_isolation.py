"""The ingest worker and the API must not load torch/numpy or the ASR engine (requirement 6, decision 6)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECK = """
import sys
import callsight.jobs, callsight.api.main, callsight.cli
bad = sorted(m for m in ("torch", "numpy", "soxr", "transformers", "callsight.asr.engine", "callsight.asr.tasks")
             if m in sys.modules)
print(",".join(bad))
"""


def test_ingest_worker_and_api_do_not_import_gpu_code():
    out = subprocess.run([sys.executable, "-c", CHECK], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == ""


def test_transcribe_task_routed_to_asr_queue():
    from callsight import jobs
    from callsight.asr import queueing
    assert jobs.celery.conf.task_routes[queueing.TASK_NAME] == {"queue": "asr"}
    assert jobs.celery.conf.task_default_queue == "ingest"
    beat = {v["task"] for v in jobs.celery.conf.beat_schedule.values()}
    assert {"callsight.jobs.queue_transcripts", "callsight.jobs.reap_transcripts"} <= beat


def test_task_registered_with_time_limit():
    from callsight.asr import tasks
    assert tasks.transcribe_call.name == "callsight.asr.tasks.transcribe_call"
    assert tasks.transcribe_call.soft_time_limit == 15 * 60


class FakeRedis:
    def __init__(self, lists):
        self.lists = lists

    def llen(self, key):
        return len(self.lists.get(key, []))                 # Redis: missing key -> 0


class FakeChannel:
    priority_steps = [0, 3, 6, 9]
    sep = "\x06\x16"

    def __init__(self, lists):
        self.client = FakeRedis(lists)

    def _q_for_pri(self, queue, pri):
        return f"{queue}{self.sep}{pri}" if pri else queue


def test_queue_length_is_zero_for_missing_key_and_sums_priorities():
    from callsight import jobs
    assert jobs.redis_queue_length(FakeChannel({}), "asr") == 0
    lists = {"asr": [1, 2], "asr\x06\x163": [3], "ingest": [4]}
    assert jobs.redis_queue_length(FakeChannel(lists), "asr") == 3


def test_bad_asr_setting_never_breaks_recording_download(monkeypatch, caplog):
    import logging
    from callsight import jobs

    class C:
        id = 7
    monkeypatch.setenv("ASR_START_DATE", "14-11-2026")       # typo: not ISO
    with caplog.at_level(logging.WARNING):
        jobs._queue_after_recording(object(), C())
    assert "call 7" in caplog.text and "ValueError" in caplog.text


def test_settings_load_without_pbx_secret_and_pbx_fails_clearly(monkeypatch):
    import pytest
    from callsight import app, config
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    for k in ("YEASTAR_BASE_URL", "YEASTAR_CLIENT_ID", "YEASTAR_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    s = config.Settings.from_env()
    assert s.database_url and s.yeastar_client_secret == ""
    monkeypatch.setattr(app, "settings", lambda: s)
    app.pbx.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="YEASTAR_CLIENT_SECRET"):
            app.pbx()
    finally:
        app.pbx.cache_clear()
