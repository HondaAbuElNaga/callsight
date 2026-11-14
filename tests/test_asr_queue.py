"""Transcript queueing rules, reaper, the transcribe task (fake model), latency + status, migration 0005.

Runs against the test Postgres (TEST_DATABASE_URL); skipped when it isn't reachable.
"""
import functools
import importlib.util
import logging
import math
import os
import wave
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from callsight.asr import engine as asr_engine
from callsight.asr import queueing, tasks
from callsight.asr.models import TARGET_RATE
from callsight.config import RIYADH
from callsight.db.models import Base, Call, Transcript, TranscriptSegment

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
URL = os.getenv("TEST_DATABASE_URL")
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
SECRET_TEXT = "رقم الهوية سري جدا"


def cfg(**kw) -> queueing.AsrConfig:
    base = dict(model="whisper-large-v3", min_talk=5, max_seconds=3600, start_date=date(2026, 11, 18), batch=50,
                idle_unload_s=300)
    base.update(kw)
    return queueing.AsrConfig(**base)


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
        c.execute(text("TRUNCATE transcript_segment, transcript, call RESTART IDENTITY CASCADE"))
    with sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


_n = [0]


def make_call(s, start=None, talk=60, status="answered", direction="inbound", rec="ok", path="a.wav") -> Call:
    _n[0] += 1
    start = start or NOW - timedelta(hours=1)
    c = Call(uid=f"u{_n[0]}", direction=direction, entry="queue", start_time=start,
             end_time=start + timedelta(seconds=talk + 10), talk_seconds=talk, total_seconds=talk + 10,
             status=status, recording_status=rec, recording_path=path if rec == "ok" else None)
    s.add(c)
    s.commit()
    return c


def rows(s) -> list[Transcript]:
    s.expire_all()
    return s.scalars(select(Transcript).order_by(Transcript.call_id)).all()


# ---------- queueing rules ----------

def test_beat_does_nothing_without_start_date(session, caplog):
    make_call(session)
    with caplog.at_level(logging.WARNING):
        assert queueing.queue_new_calls(session, cfg(start_date=None), NOW) == []
    assert rows(session) == [] and "ASR_START_DATE" in caplog.text


def test_beat_only_calls_from_start_date_riyadh_midnight(session):
    start_day = datetime(2026, 11, 18, tzinfo=RIYADH)
    old = make_call(session, start=start_day - timedelta(minutes=1))
    new = make_call(session, start=start_day)
    assert queueing.queue_new_calls(session, cfg(), NOW) == [new.id]
    assert [t.call_id for t in rows(session)] == [new.id] and old.id


def test_beat_batch_cap_newest_first_and_no_repick(session):
    calls = [make_call(session, start=NOW - timedelta(hours=10 - i)) for i in range(5)]
    first = queueing.queue_new_calls(session, cfg(batch=2), NOW)
    assert first == [calls[4].id, calls[3].id]
    second = queueing.queue_new_calls(session, cfg(batch=2), NOW)
    assert second == [calls[2].id, calls[1].id]
    assert len(rows(session)) == 4


def test_only_answered_customer_calls_with_ok_recording(session):
    ok_in = make_call(session)
    ok_out = make_call(session, direction="outbound")
    make_call(session, direction="internal")
    make_call(session, status="no_answer")
    make_call(session, rec="pending")
    assert sorted(queueing.queue_new_calls(session, cfg(), NOW)) == sorted([ok_in.id, ok_out.id])


def test_short_and_too_long_calls_get_skipped_rows_and_are_not_repicked(session):
    short = make_call(session, talk=4)
    long_ = make_call(session, talk=3601)
    assert queueing.queue_new_calls(session, cfg(), NOW) == []
    by_call = {t.call_id: t for t in rows(session)}
    assert by_call[short.id].status == by_call[long_.id].status == "skipped"
    assert "5 s" in by_call[short.id].error and "3600" in by_call[long_.id].error
    assert queueing.queue_new_calls(session, cfg(), NOW) == []
    assert len(rows(session)) == 2


def test_on_conflict_no_duplicate_rows(session):
    c = make_call(session)
    assert queueing.add_row(session, c, cfg(), NOW) == "queued"
    assert queueing.add_row(session, c, cfg(), NOW) is None
    session.commit()
    assert len(rows(session)) == 1


def test_other_model_gets_its_own_row(session):
    c = make_call(session)
    queueing.add_row(session, c, cfg(), NOW)
    assert queueing.add_row(session, c, cfg(model="audar-asr-turbo"), NOW) == "queued"
    session.commit()
    assert {t.model for t in rows(session)} == {"whisper-large-v3", "audar-asr-turbo"}
    assert rows(session)[0].model_revision                      # pinned revision stored


def test_backfill_ignores_start_date_but_is_capped(session):
    calls = [make_call(session, start=NOW - timedelta(days=5, hours=i)) for i in range(4)]
    make_call(session, start=NOW - timedelta(days=9))             # outside --days
    got = queueing.queue_recent(session, cfg(start_date=None), days=7, limit=3, now=NOW)
    assert got == [c.id for c in calls[:3]]
    with pytest.raises(ValueError):
        queueing.queue_recent(session, cfg(), days=7, limit=queueing.MAX_BACKFILL + 1, now=NOW)


def test_queue_call_force_retries_error_and_skipped(session):
    c = make_call(session, talk=3)
    assert queueing.queue_call(session, cfg(), c.id, force=False, now=NOW) == ("skipped", False)
    assert queueing.queue_call(session, cfg(), c.id, force=False, now=NOW) == ("skipped", False)
    assert queueing.queue_call(session, cfg(), c.id, force=True, now=NOW) == ("queued", True)
    t = rows(session)[0]
    assert t.status == "queued" and t.tries == 0 and t.error is None


def test_queue_call_unknown_or_no_recording(session):
    with pytest.raises(ValueError):
        queueing.queue_call(session, cfg(), 999, force=False, now=NOW)
    c = make_call(session, rec="missing")
    with pytest.raises(ValueError):
        queueing.queue_call(session, cfg(), c.id, force=True, now=NOW)


def test_after_recording_hook_sends_once_and_respects_start_date(session):
    sent = []
    c = make_call(session)
    assert queueing.after_recording(session, c, cfg(), sent.append)
    assert not queueing.after_recording(session, c, cfg(), sent.append)
    assert sent == [c.id]
    c2 = make_call(session)
    assert not queueing.after_recording(session, c2, cfg(start_date=None), sent.append)
    old = make_call(session, start=datetime(2026, 11, 1, tzinfo=RIYADH))
    assert not queueing.after_recording(session, old, cfg(), sent.append)
    assert sent == [c.id]


def test_after_recording_hook_survives_broker_down(session, caplog):
    def down(_):
        raise ConnectionError("redis down")
    c = make_call(session)
    with caplog.at_level(logging.WARNING):
        assert not queueing.after_recording(session, c, cfg(), down)
    assert rows(session)[0].status == "queued"                  # reaper re-sends it later


# ---------- reaper ----------

def _row(s, call, status, tries=0, age_min=0):
    t = Transcript(call_id=call.id, model="whisper-large-v3", status=status, tries=tries,
                   queued_at=NOW - timedelta(minutes=age_min),
                   started_at=NOW - timedelta(minutes=age_min) if status == "running" else None)
    s.add(t)
    s.commit()
    return t


def test_reaper(session):
    lost = _row(session, make_call(session), "queued", age_min=40)
    fresh = _row(session, make_call(session), "queued", age_min=5)
    died = _row(session, make_call(session), "running", tries=1, age_min=40)
    died_twice = _row(session, make_call(session), "running", tries=2, age_min=40)
    busy = _row(session, make_call(session), "running", tries=1, age_min=10)
    done = _row(session, make_call(session), "done", tries=1, age_min=100)
    resend = queueing.reap(session, cfg(), NOW, queue_length=0)
    assert sorted(resend) == sorted([lost.call_id, died.call_id])
    st = {t.id: (t.status, t.tries) for t in rows(session)}
    assert st[lost.id] == ("queued", 1) and st[died.id] == ("queued", 1)
    assert st[died_twice.id][0] == "error" and st[fresh.id] == ("queued", 0)
    assert st[busy.id] == ("running", 1) and st[done.id][0] == "done"


def test_reaper_leaves_waiting_rows_while_queue_has_messages(session):
    waiting = _row(session, make_call(session), "queued", age_min=50)
    assert queueing.reap(session, cfg(), NOW, queue_length=7) == []
    assert rows(session)[0].status == "queued" and waiting.tries == 0


def test_lost_message_twice_becomes_error(session):
    _row(session, make_call(session), "queued", tries=2, age_min=40)
    assert queueing.reap(session, cfg(), NOW, queue_length=0) == []
    assert rows(session)[0].status == "error"


# ---------- latency + status ----------

def test_latency_formula_fixed_timestamps():
    end = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    assert queueing.latency_seconds(end + timedelta(minutes=7, seconds=30), end) == 450.0


def test_status_counts_and_live_latency(session):
    for minutes in (2, 6, 10):
        c = make_call(session, start=NOW - timedelta(hours=2))
        t = _row(session, c, "done", tries=1)
        t.first_queued_at = c.end_time + timedelta(seconds=30)
        t.queued_at = NOW                                          # rewritten by a retry: must not matter
        t.finished_at = c.end_time + timedelta(minutes=minutes)
    old = make_call(session, start=NOW - timedelta(days=3))       # backfilled: not counted in latency
    t = _row(session, old, "done", tries=1)
    t.first_queued_at = t.queued_at = t.finished_at = NOW
    c = make_call(session, start=NOW - timedelta(hours=3))        # redone with --force hours later: left out
    t = _row(session, c, "done", tries=1)
    t.first_queued_at = c.end_time + timedelta(seconds=30)
    t.finished_at, t.redo = NOW, True
    _row(session, make_call(session), "error", tries=2)
    _row(session, make_call(session), "queued")
    session.commit()
    st = queueing.status(session, cfg(), queue_length=1, now=NOW)
    assert st["queue_length"] == 1
    assert st["counts"] == {"done": 5, "error": 1, "queued": 1}
    assert st["latency_n"] == 3 and st["latency_median_s"] == 360.0 and st["latency_max_s"] == 600.0
    out = queueing.format_status(st)
    assert "queue" in out and "6.0 min" in out and "10.0 min" in out


# ---------- the task (fake model, no GPU) ----------

def _wav(path: Path, seconds: float, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(int(3000 * math.sin(i * 0.3)).to_bytes(2, "little", signed=True)
                               for i in range(int(seconds * rate))))


def fake_resample(samples, rate):
    return [v / 32768 for v in samples for _ in range(TARGET_RATE // rate)]


LOAD = functools.partial(asr_engine.load_audio, resample=fake_resample)


class FakeAdapter:
    name = "whisper-large-v3"
    revision = "fakerev"
    window_s = 30.0

    def __init__(self, fail=None):
        self.fail = list(fail or [])
        self.calls = 0

    def load(self):
        pass

    def transcribe(self, audio16k):
        self.calls += 1
        if self.fail:
            raise self.fail.pop(0)
        return SECRET_TEXT


def holder(adapter):
    return asr_engine.ModelHolder("whisper-large-v3", factory=lambda n: adapter, start_timer=False)


class OutOfMemoryError(RuntimeError):
    """Same class name as torch.cuda.OutOfMemoryError."""


@pytest.fixture
def audio_dir(tmp_path):
    _wav(tmp_path / "2026/11/20/a.wav", 180)
    return tmp_path


def queued_call(s, path="2026/11/20/a.wav"):
    c = make_call(s, path=path)
    queueing.add_row(s, c, cfg(), NOW)
    s.commit()
    return c


def test_task_transcribes_call_and_is_idempotent(session, audio_dir, caplog):
    c = queued_call(session)
    a = FakeAdapter()
    with caplog.at_level(logging.DEBUG):
        r = tasks.process(session, c.id, "whisper-large-v3", holder(a), audio_dir, load=LOAD, now=lambda: NOW)
    assert r["status"] == "done" and r["segments"] == 7
    t = rows(session)[0]
    assert t.status == "done" and t.text and t.tries == 1 and t.model_revision == "fakerev"
    assert t.segmenter_version == asr_engine.SEGMENTER_VERSION and t.diarization_version is None
    assert t.audio_seconds == pytest.approx(180) and t.finished_at == NOW
    segs = session.scalars(select(TranscriptSegment).where(TranscriptSegment.transcript_id == t.id)
                           .order_by(TranscriptSegment.seq)).all()
    assert len(segs) == 7 and all(sg.speaker is None for sg in segs)
    assert all(a.end_seconds == b.start_seconds for a, b in zip(segs, segs[1:]))
    # privacy: no transcript text in the task result or the logs
    assert SECRET_TEXT not in repr(r) and SECRET_TEXT not in caplog.text

    again = tasks.process(session, c.id, "whisper-large-v3", holder(a), audio_dir, load=LOAD, now=lambda: NOW)
    assert again["skipped"] and a.calls == 7
    assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 7
    assert len(rows(session)) == 1


def test_task_missing_file_is_error_at_once(session, audio_dir):
    c = queued_call(session, path="2026/11/20/gone.wav")
    r = tasks.process(session, c.id, "whisper-large-v3", holder(FakeAdapter()), audio_dir, load=LOAD,
                      now=lambda: NOW)
    t = rows(session)[0]
    assert r["status"] == "error" and t.status == "error" and "not found" in t.error and t.tries == 1


def test_task_path_outside_audio_dir_refused(session, audio_dir):
    c = queued_call(session, path="../../etc/passwd")
    tasks.process(session, c.id, "whisper-large-v3", holder(FakeAdapter()), audio_dir, load=LOAD, now=lambda: NOW)
    assert rows(session)[0].status == "error"


def test_task_out_of_memory_retried_once_then_error(session, audio_dir):
    c = queued_call(session)
    a = FakeAdapter(fail=[OutOfMemoryError("CUDA out of memory"), OutOfMemoryError("CUDA out of memory")])
    r1 = tasks.process(session, c.id, "whisper-large-v3", holder(a), audio_dir, load=LOAD, now=lambda: NOW)
    assert r1["retry"] and rows(session)[0].status == "queued"
    r2 = tasks.process(session, c.id, "whisper-large-v3", holder(a), audio_dir, load=LOAD, now=lambda: NOW)
    t = rows(session)[0]
    assert not r2.get("retry") and t.status == "error" and t.tries == 2 and "out of memory" in t.error


def test_task_other_error_has_no_text_in_message(session, audio_dir):
    c = queued_call(session)
    a = FakeAdapter(fail=[ValueError(f"bad token near {SECRET_TEXT}")])
    tasks.process(session, c.id, "whisper-large-v3", holder(a), audio_dir, load=LOAD, now=lambda: NOW)
    t = rows(session)[0]
    assert t.status == "error" and t.error == "model failed (ValueError)" and SECRET_TEXT not in t.error


def test_task_for_other_model_left_queued_without_a_try(session, audio_dir):
    c = make_call(session, path="2026/11/20/a.wav")
    queueing.add_row(session, c, cfg(model="audar-asr-turbo"), NOW)
    session.commit()
    a = FakeAdapter()
    r = tasks.process(session, c.id, "audar-asr-turbo", holder(a), audio_dir, load=LOAD, now=lambda: NOW)
    t = rows(session)[0]
    assert r["wrong_model"] and t.status == "queued" and t.tries == 0 and t.error is None and a.calls == 0


class NoModel(FakeAdapter):
    def load(self):
        raise OSError("not in the offline cache")


def test_task_model_load_failure_leaves_row_queued_without_a_try(session, audio_dir):
    c = queued_call(session)
    h = holder(NoModel())
    for _ in range(2):                                             # 2nd time: holder back-off, still no try
        r = tasks.process(session, c.id, "whisper-large-v3", h, audio_dir, load=LOAD, now=lambda: NOW)
        assert r["not_ready"]
    t = rows(session)[0]
    assert t.status == "queued" and t.tries == 0 and t.error is None and t.started_at is None
    # the delayed re-send is unacked outside the Redis list (LLEN 0): the reaper must not count it as lost
    assert queueing.reap(session, cfg(), NOW + timedelta(minutes=31), queue_length=0) == []
    t = rows(session)[0]
    assert t.status == "queued" and t.tries == 0


def test_not_ready_task_sends_exactly_one_delayed_message(session, audio_dir, monkeypatch):
    c = queued_call(session)
    sent = []
    h = holder(NoModel())
    monkeypatch.setattr(tasks, "holder", lambda: h)
    monkeypatch.setattr(tasks, "send_transcribe", lambda cid, model, countdown=None: sent.append((cid, countdown)))
    monkeypatch.setattr(tasks.wiring, "session_factory", lambda: (lambda: session))
    monkeypatch.setattr(tasks.wiring, "settings", lambda: type("S", (), {"audio_dir": audio_dir})())
    r = tasks.transcribe_call.run(c.id, "whisper-large-v3")
    assert r["not_ready"] and sent == [(c.id, tasks.NOT_READY_RETRY_S)]


def test_wrong_model_row_not_reaped_right_away(session, audio_dir):
    c = make_call(session, path="2026/11/20/a.wav")
    queueing.add_row(session, c, cfg(model="audar-asr-turbo"), NOW - timedelta(hours=2))
    session.commit()
    tasks.process(session, c.id, "audar-asr-turbo", holder(FakeAdapter()), audio_dir, load=LOAD, now=lambda: NOW)
    assert queueing.reap(session, cfg(model="audar-asr-turbo"), NOW + timedelta(minutes=10), queue_length=0) == []


def test_task_model_unloaded_and_reload_fails_gives_back_the_try(session, audio_dir, monkeypatch):
    c = queued_call(session)
    h = holder(FakeAdapter())

    def gone():
        raise asr_engine.ModelNotReady("gone")
    monkeypatch.setattr(h, "use", gone)
    r = tasks.process(session, c.id, "whisper-large-v3", h, audio_dir, load=LOAD, now=lambda: NOW)
    t = rows(session)[0]
    assert r["not_ready"] and t.status == "queued" and t.tries == 0


def test_task_soft_time_limit_is_error(session, audio_dir):
    from celery.exceptions import SoftTimeLimitExceeded
    c = queued_call(session)
    tasks.process(session, c.id, "whisper-large-v3", holder(FakeAdapter(fail=[SoftTimeLimitExceeded()])),
                  audio_dir, load=LOAD, now=lambda: NOW)
    t = rows(session)[0]
    assert t.status == "error" and "time limit" in t.error


def test_task_save_failure_is_error_without_text_in_logs(engine, session, audio_dir, monkeypatch, caplog):
    """A DB error while saving (here: a too-long column value) must not put transcript text in logs/results."""
    from callsight.db.session import make_session_factory
    c = queued_call(session)
    monkeypatch.setattr(asr_engine, "SEGMENTER_VERSION", "x" * 40)    # column is String(30) -> DataError
    sf = make_session_factory(URL)
    assert sf.kw["bind"].hide_parameters
    with sf() as s2, caplog.at_level(logging.DEBUG):
        r = tasks.process(s2, c.id, "whisper-large-v3", holder(FakeAdapter()), audio_dir, load=LOAD,
                          now=lambda: NOW)
    t = rows(session)[0]
    assert r["status"] == "error" and t.status == "error" and t.error == "saving transcript failed (DataError)"
    assert SECRET_TEXT not in caplog.text and SECRET_TEXT not in repr(r) and t.text is None
    assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0


def test_retry_errors_capped_since_day(session):
    old = _row(session, make_call(session, start=datetime(2026, 11, 10, tzinfo=RIYADH)), "error", tries=2)
    errs = [_row(session, make_call(session, start=NOW - timedelta(hours=i + 1)), "error", tries=2)
            for i in range(3)]
    _row(session, make_call(session), "skipped")
    ids = queueing.retry_errors(session, cfg(), date(2026, 11, 18), limit=2, now=NOW)
    assert ids == [errs[0].call_id, errs[1].call_id]
    st = {t.id: (t.status, t.tries) for t in rows(session)}
    assert st[errs[0].id] == ("queued", 0) and st[errs[2].id] == ("error", 2) and st[old.id] == ("error", 2)
    with pytest.raises(ValueError):
        queueing.retry_errors(session, cfg(), date(2026, 11, 18), limit=queueing.MAX_BACKFILL + 1, now=NOW)


def test_task_without_row_does_nothing(session, audio_dir):
    c = make_call(session)
    r = tasks.process(session, c.id, "whisper-large-v3", holder(FakeAdapter()), audio_dir, load=LOAD,
                      now=lambda: NOW)
    assert r["skipped"] and rows(session) == []


def test_force_redo_replaces_segments(session, audio_dir):
    c = queued_call(session)
    tasks.process(session, c.id, "whisper-large-v3", holder(FakeAdapter()), audio_dir, load=LOAD, now=lambda: NOW)
    queueing.queue_call(session, cfg(), c.id, force=True, now=NOW)
    tasks.process(session, c.id, "whisper-large-v3", holder(FakeAdapter()), audio_dir, load=LOAD, now=lambda: NOW)
    assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 7


def test_call_delete_cascades(session, audio_dir):
    c = queued_call(session)
    tasks.process(session, c.id, "whisper-large-v3", holder(FakeAdapter()), audio_dir, load=LOAD, now=lambda: NOW)
    session.execute(text("DELETE FROM call WHERE id = :i"), {"i": c.id})
    session.commit()
    assert rows(session) == [] and session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0


# ---------- migration 0005 ----------

def test_migration_0005_up_and_down(engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parent.parent / "callsight/db/migrations/versions/0005_transcripts.py"
    spec = importlib.util.spec_from_file_location("m0005", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("0005", "0004")
    try:
        with engine.begin() as conn:
            Base.metadata.tables["transcript_segment"].drop(conn)
            Base.metadata.tables["transcript"].drop(conn)
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()
        insp = inspect(engine)
        assert {"transcript", "transcript_segment"} <= set(insp.get_table_names())
        model_cols = {c.name for c in Base.metadata.tables["transcript"].columns}
        assert {c["name"] for c in insp.get_columns("transcript")} == model_cols
        seg_cols = {c.name for c in Base.metadata.tables["transcript_segment"].columns}
        assert {c["name"] for c in insp.get_columns("transcript_segment")} == seg_cols
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.downgrade()
        names = set(inspect(engine).get_table_names())
        assert "transcript" not in names and "call" in names       # additive: nothing else touched
    finally:
        Base.metadata.create_all(engine)


# ---------- CLI ----------

def test_cli_backfill_call_and_status(session, engine, monkeypatch, capsys):
    from callsight import app as wiring, cli, jobs
    sent = []
    monkeypatch.setattr(wiring, "session_factory", lambda: sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(jobs, "send_transcribe", lambda cid, model, countdown=None: sent.append((cid, model)))
    monkeypatch.setattr(jobs, "asr_queue_length", lambda: 3)
    monkeypatch.setenv("ASR_MODEL", "whisper-large-v3")
    now = datetime.now(timezone.utc)
    calls = [make_call(session, start=now - timedelta(hours=i + 1)) for i in range(7)]
    assert cli.main(["transcribe", "--days", "2", "--limit", "5"]) == 0
    assert [c for c, _ in sent] == [c.id for c in calls[:5]]
    assert cli.main(["transcribe", "--call", str(calls[0].id)]) == 0          # already queued: not re-sent
    assert len(sent) == 5
    assert cli.main(["transcribe", "--call", str(calls[6].id)]) == 0
    assert len(sent) == 6
    assert cli.main(["transcribe", "status"]) == 0
    out = capsys.readouterr().out
    assert "asr queue length: 3" in out and "queued 6" in out
    with pytest.raises(SystemExit):
        cli.main(["transcribe", "--days", "2"])
    with pytest.raises(SystemExit):
        cli.main(["transcribe", "status", "--call", "1"])
    assert cli.main(["transcribe", "--days", "2", "--limit", "501"]) == 1
    session.execute(text("UPDATE transcript SET status = 'error' WHERE call_id = :i"), {"i": calls[1].id})
    session.commit()
    since = (now - timedelta(days=1)).date().isoformat()
    assert cli.main(["transcribe", "--retry-errors", "--since", since]) == 0
    assert sent[-1][0] == calls[1].id and len(sent) == 7
    with pytest.raises(SystemExit):
        cli.main(["transcribe", "--retry-errors"])
