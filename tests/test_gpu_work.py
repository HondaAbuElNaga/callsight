"""AWS GPU wake-up design (spec 2026-11-15-aws-deploy): the "work left" count, queueing it now, the health item
"oldest call waiting for the GPU", and the boot/idle loop of the GPU server (fake commands, fake clock).

DB tests run on TEST_DATABASE_URL (a *_test database); skipped when it isn't reachable.
"""
import importlib.util
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from callsight import cli, gpuwork, health
from callsight.analysis import queueing as nlpq
from callsight.asr import queueing as asrq
from callsight.config import ROOT
from callsight.db.models import Base, Call, CallAnalysis, Transcript

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
URL = os.getenv("TEST_DATABASE_URL")
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)          # 15:00 in Riyadh
MODEL, LLM, PV = "whisper-large-v3", "qwen2.5:7b", "p1-abc"

_spec = importlib.util.spec_from_file_location("gpu_run", ROOT / "deploy" / "aws" / "gpu_run.py")
gpu_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gpu_run)


def cfg(**kw) -> asrq.AsrConfig:
    base = dict(model=MODEL, min_talk=5, max_seconds=3600, start_date=date(2026, 11, 18), batch=50)
    base.update(kw)
    return asrq.AsrConfig(**base)


@pytest.fixture(scope="module")
def engine():
    if not URL or "_test" not in URL:
        pytest.skip("TEST_DATABASE_URL not set (must point to a *_test database)")
    eng = create_engine(URL, connect_args={"connect_timeout": 3})
    try:
        with eng.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database not reachable: {exc}")
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE call_analysis, transcript_segment, transcript, call RESTART IDENTITY CASCADE"))
    with sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


_n = [0]


def make_call(s, start=None, talk=60, status="answered", direction="inbound", rec="ok") -> Call:
    _n[0] += 1
    start = start or NOW - timedelta(hours=1)
    c = Call(uid=f"g{_n[0]}", direction=direction, entry="queue", start_time=start,
             end_time=start + timedelta(seconds=talk + 10), talk_seconds=talk, total_seconds=talk + 10,
             status=status, recording_status=rec, recording_path="a.wav" if rec == "ok" else None)
    s.add(c)
    s.commit()
    return c


def transcript(s, call, status="done", model=MODEL) -> Transcript:
    t = Transcript(call_id=call.id, model=model, status=status, queued_at=NOW, first_queued_at=NOW)
    s.add(t)
    s.commit()
    return t


def analysis(s, call, status="done", model=LLM, pv=PV) -> CallAnalysis:
    a = CallAnalysis(call_id=call.id, model=model, prompt_version=pv, status=status, queued_at=NOW)
    s.add(a)
    s.commit()
    return a


# ---------- work left (requirement 4) ----------

def test_nothing_waiting_is_zero(session):
    done = make_call(session)
    transcript(session, done)
    analysis(session, done)
    make_call(session, status="no_answer", rec="none")              # nothing to transcribe
    w = gpuwork.pending(session, cfg(), nlp_enabled=True)
    assert (w.transcripts, w.analyses, w.total, w.oldest) == (0, 0, 0, None)


def test_counts_queued_and_running_rows(session):
    a, b, c = make_call(session), make_call(session), make_call(session)
    transcript(session, a, "queued")
    transcript(session, b, "running")
    transcript(session, c, "error")                                 # given up: not work
    analysis(session, c, "error")
    d, e = make_call(session), make_call(session)
    transcript(session, d)
    transcript(session, e)
    analysis(session, d, "queued")
    analysis(session, e, "running")
    w = gpuwork.pending(session, cfg(), nlp_enabled=True)
    assert (w.transcripts, w.analyses) == (2, 2)


def test_counts_calls_the_beat_job_has_not_queued_yet(session):
    make_call(session)                                              # recording there, no transcript row yet
    done = make_call(session)
    transcript(session, done)                                       # transcript done, analysis not queued yet
    w = gpuwork.pending(session, cfg(), nlp_enabled=True)           # (requirement 3: the server must stay up)
    assert (w.transcripts, w.analyses) == (1, 1)


def test_respects_start_date_talk_limits_and_recording(session):
    make_call(session, start=datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc))   # before ASR_START_DATE
    make_call(session, talk=3)                                      # under ASR_MIN_TALK: will be marked skipped
    make_call(session, talk=4000)                                   # over ASR_MAX_SECONDS
    make_call(session, rec="pending")                               # recording not downloaded yet
    make_call(session, direction="internal")
    assert gpuwork.pending(session, cfg(), nlp_enabled=True).total == 0
    assert gpuwork.pending(session, cfg(min_talk=2), nlp_enabled=True).transcripts == 1
    make_call(session)
    assert gpuwork.pending(session, cfg(start_date=None), nlp_enabled=True).total == 0   # automatic ASR off


def test_other_models_rows_are_not_transcript_work(session):
    c = make_call(session)
    transcript(session, c, "queued", model="audar-asr-turbo")       # no worker for that model is running
    w = gpuwork.pending(session, cfg(), nlp_enabled=False)
    assert w.transcripts == 1                                       # ...but the call has no row for OUR model yet


def test_nlp_disabled_counts_no_analyses(session):
    a, b = make_call(session), make_call(session)
    transcript(session, a)
    transcript(session, b)
    analysis(session, b, "queued")
    assert gpuwork.pending(session, cfg(), nlp_enabled=False).total == 0
    assert gpuwork.pending(session, cfg(), nlp_enabled=True).analyses == 2


def test_one_call_with_two_open_analysis_rows_counts_once(session):
    c = make_call(session)
    transcript(session, c)
    analysis(session, c, "queued", pv="p1-old")
    analysis(session, c, "queued", pv=PV)
    assert gpuwork.pending(session, cfg(), nlp_enabled=True).analyses == 1


def test_oldest_is_the_earliest_hang_up(session):
    old = make_call(session, start=NOW - timedelta(hours=3))
    make_call(session, start=NOW - timedelta(minutes=10))
    w = gpuwork.pending(session, cfg(), nlp_enabled=True)
    assert w.transcripts == 2 and w.oldest == old.end_time


def _beat_jobs(session, c, sent_t, sent_a):
    def queue_transcripts():
        sent_t.extend(asrq.queue_new_calls(session, c, NOW))

    def queue_analyses():
        sent_a.extend(nlpq.queue_new(session, LLM, PV, 20, NOW))
    return queue_transcripts, queue_analyses


def test_queue_now_never_queues_a_call_twice(session):
    calls = [make_call(session) for _ in range(5)]
    done = make_call(session)
    transcript(session, done)
    sent_t, sent_a = [], []
    qt, qa = _beat_jobs(session, cfg(batch=2), sent_t, sent_a)     # batch 2: needs 3 rounds for 5 calls
    gpuwork.queue_now(session, cfg(batch=2), True, qt, qa)
    gpuwork.queue_now(session, cfg(batch=2), True, qt, qa)         # second run (or the beat job) finds nothing
    assert sorted(sent_t) == sorted(c.id for c in calls) and sent_a == [done.id]
    assert len(session.scalars(select(Transcript).where(Transcript.status == "queued")).all()) == 5
    w = gpuwork.pending(session, cfg(), nlp_enabled=True)
    assert (w.transcripts, w.analyses) == (5, 1)                    # queued rows are still work


def test_queue_now_gets_past_a_batch_of_too_short_calls(session):
    for _ in range(3):
        make_call(session, talk=2, start=NOW - timedelta(minutes=5))   # newest: the beat job marks them skipped
    real = make_call(session, start=NOW - timedelta(hours=2))
    sent_t, sent_a = [], []
    qt, qa = _beat_jobs(session, cfg(batch=3), sent_t, sent_a)
    gpuwork.queue_now(session, cfg(batch=3), False, qt, qa)
    assert sent_t == [real.id] and sent_a == []


def test_queue_now_does_nothing_when_automatic_asr_is_off(session):
    make_call(session)
    calls = []
    gpuwork.queue_now(session, cfg(start_date=None), False, lambda: calls.append("t"), lambda: calls.append("a"))
    assert calls == []


# ---------- command line ----------

def test_cli_prints_one_number(session, engine, monkeypatch, capsys):
    make_call(session)
    monkeypatch.setattr(cli.wiring, "session_factory", lambda: sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setenv("ASR_START_DATE", "2026-11-18")
    monkeypatch.setenv("ASR_MODEL", MODEL)
    monkeypatch.setenv("NLP_ENABLED", "false")
    assert cli.main(["gpu-work"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "1" and "transcripts 1" in out.err


def test_cli_error_gives_no_number(monkeypatch, capsys):
    def boom():
        raise RuntimeError("postgresql://callsight:secret@10.20.1.10/callsight refused")
    monkeypatch.setattr(cli.wiring, "session_factory", boom)
    assert cli.main(["gpu-work"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "secret" not in out.err and "10.20.1.10" not in out.err


# ---------- health item (requirement 10) ----------

def at(hour, minute=0):
    """A Riyadh wall-clock time on 2026-11-20, as UTC."""
    return datetime(2026, 11, 20, hour, minute, tzinfo=timezone.utc) - timedelta(hours=3)


def test_health_item_states():
    off = gpuwork.health_item(None, at(12))
    assert (off.key, off.state) == ("gpu.wait", "off")
    assert gpuwork.health_item(gpuwork.Work(0, 0, None), at(12)).state == "ok"
    fresh = gpuwork.health_item(gpuwork.Work(2, 1, at(11, 20)), at(12))
    assert fresh.state == "ok" and fresh.value == {"waiting": 3, "oldest_min": 40}
    late = gpuwork.health_item(gpuwork.Work(1, 0, at(10, 20)), at(12))
    assert late.state == "warn" and late.value["oldest_min"] == 100


def test_health_item_night_calls_wait_for_the_morning():
    night_call = at(23, 40) - timedelta(days=1)                     # hung up after the last wake-up
    assert gpuwork.health_item(gpuwork.Work(1, 0, night_call), at(3)).state == "ok"       # night: no wake-ups
    assert gpuwork.health_item(gpuwork.Work(1, 0, night_call), at(8, 10)).state == "ok"   # counted from 08:00
    assert gpuwork.health_item(gpuwork.Work(1, 0, night_call), at(9, 31)).state == "warn"
    assert gpuwork.health_item(gpuwork.Work(1, 0, at(21)), at(23, 45)).state == "ok"      # after 23:30


def test_health_collect_shows_the_item_and_survives_its_failure(session):
    from test_health import FakeRedis, Usage
    base = dict(disk_paths={"audio": Path("/x")}, redis=FakeRedis(), ping=lambda: [{"w": "pong"}],
                usage=lambda p: Usage(1000, 500, 500), now=lambda: at(12))
    work = gpuwork.Work(1, 0, at(10))
    data = health.collect(session, health.HealthDeps(**base, gpu_wait=lambda s, now: gpuwork.health_item(work, now)))
    item = {i["key"]: i for i in data["items"]}["gpu.wait"]
    assert item["state"] == "warn" and item["waiting"] == 1 and "gpu.wait" not in data["problems"]

    def broken(s, now):
        raise ValueError("ASR_START_DATE=tomorrow is not a date")
    data = health.collect(session, health.HealthDeps(**base, gpu_wait=broken))
    assert {i["key"]: i for i in data["items"]}["gpu.wait"]["state"] == "off"
    assert "gpu.wait" not in {i["key"] for i in health.collect(session, health.HealthDeps(**base))["items"]}


# ---------- boot/idle loop of the GPU server (requirements 2, 3) ----------

class Box:
    """Fake server: a clock that only moves when the loop sleeps, and a record of what was done."""

    def __init__(self, answers, stay_on_until=None):
        self.t, self.answers, self.done, self.asked = 0.0, list(answers), [], 0
        self.stay_on_until = stay_on_until

    def work(self):
        self.asked += 1
        a = self.answers.pop(0) if self.answers else 0             # after the list: nothing left
        if isinstance(a, Exception):
            raise a
        return a

    def run(self, **kw):
        return gpu_run.run(self.work, lambda: self.done.append("start"), lambda: self.done.append("stop"),
                           lambda: self.done.append(f"poweroff@{self.t:.0f}"),
                           sleep=lambda s: setattr(self, "t", self.t + s), clock=lambda: self.t,
                           stay_on=lambda: self.stay_on_until is not None and self.t < self.stay_on_until,
                           log=lambda m: None, **kw)


def test_idle_loop_powers_off_after_two_quiet_minutes():
    box = Box([])
    assert box.run() == "idle"
    assert box.done == ["start", "stop", "poweroff@120"]            # asked at 0, 30, 60, 90, 120 s
    assert box.asked == 5


def test_idle_loop_stays_up_while_work_is_left():
    box = Box([3, 2, 0, 0, 1, 0, 0, 0])                             # a quiet minute, then new work: timer restarts
    assert box.run() == "idle"
    assert box.done[-1] == "poweroff@270"                           # last work seen at 120 s; quiet from 150 s on


def test_idle_loop_failed_question_is_not_zero():
    box = Box([None, RuntimeError("docker"), None])                 # 3 failed checks, then answers of 0
    assert box.run() == "idle"
    assert box.done[-1] == "poweroff@210"                           # quiet counted only from 90 s
    box = Box([None] * 100)
    assert box.run() == "no-answer"
    assert box.done[-1] == "poweroff@600"                           # 10 minutes without an answer


def test_idle_loop_three_hour_cap():
    box = Box([1] * 10_000)
    assert box.run() == "cap"
    assert box.done == ["start", "stop", "poweroff@10800"]


def test_idle_loop_stay_on_file_blocks_power_off():
    box = Box([], stay_on_until=1000)
    assert box.run() == "idle"
    assert box.done[-1] == "poweroff@1020"                          # first check after the file is gone


def test_idle_loop_failed_start_or_stop_still_powers_off():
    off = []

    def bad():
        raise OSError("docker not running")
    t = [0.0]
    reason = gpu_run.run(lambda: None, bad, bad, lambda: off.append(t[0]),
                         sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0], log=lambda m: None)
    assert reason == "no-answer" and off == [600.0]


def test_work_command_output_parsing(monkeypatch):
    class R:
        def __init__(self, code, out):
            self.returncode, self.stdout = code, out

    def fake(result):
        monkeypatch.setattr(gpu_run.subprocess, "run", lambda *a, **k: result)
        return gpu_run._work()
    assert fake(R(0, "7\n")) == 7 and fake(R(0, "warning\n0\n")) == 0
    assert fake(R(1, "0\n")) is None and fake(R(0, "")) is None and fake(R(0, "Traceback\n")) is None
