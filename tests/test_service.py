"""Ingest/recordings/reconcile against a real test Postgres (TEST_DATABASE_URL) and a fake PBX.

Skipped when the test database isn't reachable (start it with: docker compose up -d postgres).
"""
import io
import json
import os
import wave
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from callsight.config import RIYADH
from callsight.db.models import Agent, Base, Call, CallLeg, Queue, ReconcileRun
from callsight.ingest import service
from callsight.ingest.classify import ABANDONED_QUEUE, ANSWERED, TeamConfig, leg_kind
from callsight.pbx.base import PbxAdapter, PbxError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
URL = os.getenv("TEST_DATABASE_URL")
FIX = json.loads((Path(__file__).parent / "fixtures" / "cdr_shapes.json").read_text(encoding="utf-8"))
TEAM = TeamConfig(ext_team={"104": "customer_service", "119": "customer_service", "116": "after_sales"},
                  queue_team={"6401": "customer_service"})
ALL_ROWS = [r for c in FIX for r in c["legs"]]
TEAM_UIDS = {c["legs"][0]["uid"] for c in FIX if c["team"]}


def _engine():
    if not URL or "_test" not in URL:
        pytest.skip("TEST_DATABASE_URL not set (must point to a *_test database)")
    eng = create_engine(URL, connect_args={"connect_timeout": 3})
    try:
        with eng.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database not reachable: {exc}")
    return eng


@pytest.fixture(scope="module")
def engine():
    eng = _engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE call_leg, call, agent, queue, ingest_state, reconcile_run RESTART IDENTITY CASCADE"))
    with sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


def wav_bytes(seconds=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000 * seconds)
    return buf.getvalue()


class FakePbx(PbxAdapter):
    def __init__(self, rows, bad_files=()):
        self.rows = rows
        self.bad_files = set(bad_files)
        self.downloads = 0

    def fetch_cdr_since(self, last_id, overlap=300):
        return [r for r in self.rows if int(r["id"]) > last_id - overlap]

    def fetch_cdr_between(self, start, end):
        lo, hi = start.timestamp(), end.timestamp()
        return [r for r in self.rows if lo <= int(r["timestamp"]) < hi]

    def download_recording(self, file, dest):
        self.downloads += 1
        if file in self.bad_files:
            raise PbxError("404")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(wav_bytes())
        return dest

    def list_queues(self):
        return [{"number": "6401", "name": "CS", "agents": [{"ext": "104", "name": "A"}, {"ext": "119", "name": "B"}]},
                {"number": "6400", "name": "Sales", "agents": [{"ext": "302", "name": "C"}]}]

    def list_extensions(self):
        return [{"ext": "104", "name": "A"}, {"ext": "116", "name": "D"}]


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


# ---------- ingest ----------

def test_only_team_calls_are_stored(session):
    res = service.ingest_rows(session, ALL_ROWS, TEAM)
    session.commit()
    assert set(session.scalars(select(Call.uid))) == TEAM_UIDS
    assert res.calls == len(TEAM_UIDS)
    team_legs = sum(len(c["legs"]) for c in FIX if c["team"])
    assert count(session, CallLeg) == team_legs == res.new_legs


def test_ingest_twice_is_idempotent(session):
    service.ingest_rows(session, ALL_ROWS, TEAM); session.commit()
    snap = [(c.uid, c.status, c.talk_seconds, c.agent_ext) for c in session.scalars(select(Call).order_by(Call.uid))]
    res = service.ingest_rows(session, ALL_ROWS, TEAM); session.commit()
    assert res.new_legs == 0
    assert snap == [(c.uid, c.status, c.talk_seconds, c.agent_ext) for c in session.scalars(select(Call).order_by(Call.uid))]


def test_late_agent_leg_turns_abandoned_into_answered(session):
    legs = next(c["legs"] for c in FIX if c["team"] and c["shape"] == "Inbound: IVR:ANSWERED > EXT:ANSWERED > Q:ANSWERED")
    early = [r for r in legs if leg_kind(r) != "extension"]
    late = [r for r in legs if leg_kind(r) == "extension"]
    service.ingest_rows(session, early, TEAM); session.commit()
    call = session.scalars(select(Call)).one()
    assert call.status == ABANDONED_QUEUE and call.recording_status == "pending"
    service.ingest_rows(session, late, TEAM); session.commit()
    session.refresh(call)
    assert call.status == ANSWERED and call.agent_ext == "119" and len(call.legs) == 3


def test_team_call_whose_first_leg_is_not_team_is_kept_when_rest_arrives(session):
    legs = next(c["legs"] for c in FIX if c["team"] and c["shape"] == "Inbound: IVR:ANSWERED > Q:NO ANSWER")
    service.ingest_rows(session, legs[:1], TEAM); session.commit()       # IVR leg alone: not team yet
    assert count(session, Call) == 0
    service.ingest_rows(session, legs, TEAM); session.commit()           # overlap re-reads it with the queue leg
    assert session.scalars(select(Call.status)).one() == ABANDONED_QUEUE


def test_run_ingest_moves_cursor(session):
    pbx = FakePbx(ALL_ROWS)
    service.set_cursor(session, [{"id": 0}]); session.commit()   # skip the "first run = last 24 h" path
    service.run_ingest(session, pbx, TEAM)
    assert service.get_cursor(session) == max(int(r["id"]) for r in ALL_ROWS)


# ---------- recordings ----------

def test_recordings_downloaded_once_and_marked_ok(session, tmp_path):
    service.ingest_rows(session, ALL_ROWS, TEAM); session.commit()
    pbx = FakePbx(ALL_ROWS)
    ok, failed = service.fetch_recordings(session, pbx, tmp_path, limit=500)
    with_rec = session.scalars(select(Call).where(Call.recording_file.is_not(None))).all()
    assert ok == len(with_rec) > 0 and failed == 0
    for c in with_rec:
        assert c.recording_status == "ok" and (tmp_path / c.recording_path).exists()
        local = c.start_time.astimezone(RIYADH)
        assert c.recording_path.startswith(f"{local:%Y}/{local:%m}/{local:%d}/")
    before = pbx.downloads
    assert service.fetch_recordings(session, pbx, tmp_path) == (0, 0)
    assert pbx.downloads == before


def test_failed_recording_retried_then_missing(session, tmp_path):
    service.ingest_rows(session, ALL_ROWS, TEAM); session.commit()
    bad = session.scalars(select(Call.recording_file).where(Call.recording_file.is_not(None))).first()
    pbx = FakePbx(ALL_ROWS, bad_files={bad})
    for _ in range(5):
        service.fetch_recordings(session, pbx, tmp_path, limit=500)
    call = session.scalars(select(Call).where(Call.recording_file == bad)).one()
    assert call.recording_status == "missing" and call.recording_attempts == 3


def test_corrupt_wav_is_not_accepted(session, tmp_path):
    service.ingest_rows(session, ALL_ROWS, TEAM); session.commit()

    class Corrupt(FakePbx):
        def download_recording(self, file, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"<html>error</html>")
            return dest
    service.fetch_recordings(session, Corrupt(ALL_ROWS), tmp_path, limit=1)
    call = session.scalars(select(Call).where(Call.recording_attempts == 1)).one()
    assert call.recording_status == "error" and call.recording_path is None
    assert not any(p.is_file() for p in tmp_path.rglob("*"))


def test_unsafe_recording_name_never_written(session, tmp_path):
    service.ingest_rows(session, ALL_ROWS, TEAM); session.commit()
    call = session.scalars(select(Call).where(Call.recording_file.is_not(None))).first()
    call.recording_file = "../../evil.wav"; session.commit()
    service.fetch_one_recording(session, FakePbx(ALL_ROWS), tmp_path / "audio", call)
    assert call.recording_status == "error"
    assert not (tmp_path / "evil.wav").exists()


# ---------- reconcile ----------

def _days_with_team_calls():
    days = {}
    for c in FIX:
        if c["team"]:
            d = datetime.fromtimestamp(min(int(r["timestamp"]) for r in c["legs"]), tz=RIYADH).date()
            days[d] = days.get(d, 0) + 1
    return days


def test_reconcile_fills_missing_legs_and_recordings(session, tmp_path):
    day, _ = max(_days_with_team_calls().items(), key=lambda kv: kv[1])
    pbx = FakePbx(ALL_ROWS)
    run = service.reconcile_day(session, pbx, TEAM, tmp_path, day)
    assert run.missing_legs_before == run.pbx_team_legs > 0
    assert run.missing_legs_after == 0
    assert run.db_team_calls == run.pbx_team_calls
    assert run.missing_recordings_after == 0 and run.ok
    run2 = service.reconcile_day(session, pbx, TEAM, tmp_path, day)
    assert run2.missing_legs_before == 0 and run2.missing_recordings_before == 0 and run2.ok
    assert count(session, ReconcileRun) == 2


def test_reconcile_reports_not_ok_when_recording_missing(session, tmp_path):
    day, _ = max(_days_with_team_calls().items(), key=lambda kv: kv[1])
    start, end = service.day_bounds(day)
    files = {r["record_file"] for r in ALL_ROWS if r.get("record_file") and r["uid"] in TEAM_UIDS
             and start.timestamp() <= int(r["timestamp"]) < end.timestamp()}
    if not files:
        pytest.skip("no recorded team call on that day in fixtures")
    run = service.reconcile_day(session, FakePbx(ALL_ROWS, bad_files=files), TEAM, tmp_path, day)
    assert run.missing_recordings_after > 0 and not run.ok


def test_reconcile_empty_day(session, tmp_path):
    run = service.reconcile_day(session, FakePbx(ALL_ROWS), TEAM, tmp_path, date(2020, 1, 1))
    assert run.pbx_team_calls == 0 and run.ok


# ---------- agents & stats ----------

def test_sync_agents(session):
    n_ext, n_q = service.sync_agents(session, FakePbx([]), TEAM)
    assert (n_ext, n_q) == (4, 2)
    assert session.get(Agent, "104").in_team and session.get(Agent, "104").team == "customer_service"
    assert not session.get(Agent, "302").in_team
    assert session.get(Queue, "6401").team == "customer_service"
    service.sync_agents(session, FakePbx([]), TEAM)          # idempotent
    assert count(session, Agent) == 4


def test_day_stats_counts_match(session):
    service.ingest_rows(session, ALL_ROWS, TEAM); session.commit()
    total = 0
    for d in _days_with_team_calls():
        total += sum(r[3] for r in service.day_stats(session, d))
    assert total == len(TEAM_UIDS)
