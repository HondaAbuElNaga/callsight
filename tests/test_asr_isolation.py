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
