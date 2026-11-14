"""Gold-set picking/cutting and the admin-only typing API (test Postgres + fake WAV files)."""
import json
import os
import wave
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

from callsight.api import auth
from callsight.api.main import ApiConfig, create_app
from callsight.asr.goldset import cut_wav, pick, wav_seconds
from callsight.db.models import Base, Call, GoldClip, RecordingPlay
from callsight.ingest import service
from callsight.ingest.classify import TeamConfig

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
URL = os.getenv("TEST_DATABASE_URL")
FIX = json.loads((Path(__file__).parent / "fixtures" / "cdr_shapes.json").read_text(encoding="utf-8"))
TEAM = TeamConfig(ext_team={"104": "customer_service", "119": "customer_service", "116": "after_sales"},
                  queue_team={"6401": "customer_service"})
AGENTS = ["104", "116", "119"]
PW = "correct-horse-battery"


def write_wav(path: Path, seconds: float, rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(int(i % 2000).to_bytes(2, "little") for i in range(int(seconds * rate))))


@pytest.fixture(scope="module")
def engine():
    if not URL or "_test" not in URL:
        pytest.skip("TEST_DATABASE_URL not set")
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
def env(engine, tmp_path):
    with engine.begin() as c:
        c.execute(text("TRUNCATE gold_clip, recording_play, app_user, call_leg, call, agent, queue, ingest_state, "
                       "reconcile_run RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as s:
        service.ingest_rows(s, [r for c in FIX for r in c["legs"]], TEAM); s.commit()
        # give every answered, recorded call a 2-minute WAV and enough talk time
        for c in s.scalars(select(Call).where(Call.recording_file.is_not(None))):
            rel = f"2026/11/01/{c.recording_file}"
            write_wav(tmp_path / rel, 120)
            c.recording_path, c.recording_status, c.talk_seconds = rel, "ok", 200
        s.commit()
        auth.create_user(s, "boss", PW, "admin")
        auth.create_user(s, "sup", PW, "supervisor", "114")
    return Session, tmp_path


def eligible(Session):
    with Session() as s:
        return s.scalars(select(Call).where(Call.status == "answered", Call.recording_status == "ok",
                                            Call.direction.in_(("inbound", "outbound")),
                                            Call.agent_ext.in_(AGENTS))).all()


# ---------- picking / cutting ----------

def test_cut_wav_middle(tmp_path):
    src = tmp_path / "a.wav"
    write_wav(src, 120)
    got = cut_wav(src, tmp_path / "out" / "b.wav", 30, 60)
    assert got == pytest.approx(60) and wav_seconds(tmp_path / "out" / "b.wav") == pytest.approx(60)
    with wave.open(str(tmp_path / "out" / "b.wav")) as w:
        assert w.getframerate() == 8000 and w.getnchannels() == 1


def test_cut_wav_shorter_than_asked(tmp_path):
    src = tmp_path / "a.wav"
    write_wav(src, 20)
    assert cut_wav(src, tmp_path / "b.wav", 10, 60) == pytest.approx(10)


def test_pick_is_balanced_repeatable_and_cut(env):
    Session, audio = env
    n = min(6, len(eligible(Session)))
    with Session() as s:
        clips = pick(s, audio, AGENTS, n=n, seconds=60, seed=7)
        ids1 = [c.call_id for c in clips]
        agents = [s.get(Call, c.call_id).agent_ext for c in clips]
    assert len(clips) == n
    present = {c.agent_ext for c in eligible(Session)}
    assert set(agents) == present or len(set(agents)) >= min(3, len(present))   # round-robin over agents
    for c in clips:
        assert (audio / c.file).is_file() and c.seconds == pytest.approx(60) and c.start_seconds == pytest.approx(30)
    # same seed -> same calls (after clearing)
    with Session() as s:
        s.execute(text("DELETE FROM gold_clip")); s.commit()
        assert [c.call_id for c in pick(s, audio, AGENTS, n=n, seconds=60, seed=7)] == ids1


def test_pick_refuses_to_overwrite(env):
    Session, audio = env
    with Session() as s:
        pick(s, audio, AGENTS, n=1, seconds=60)
        with pytest.raises(ValueError):
            pick(s, audio, AGENTS, n=1, seconds=60)


def test_pick_skips_short_calls(env):
    Session, audio = env
    with Session() as s:
        s.execute(update(Call).values(talk_seconds=30)); s.commit()
        with pytest.raises(ValueError):
            pick(s, audio, AGENTS, n=3, seconds=60)


def test_pick_skips_short_or_missing_files(env):
    Session, audio = env
    calls = eligible(Session)
    write_wav(audio / calls[0].recording_path, 40)             # talk time says 200 s, file is only 40 s
    (audio / calls[1].recording_path).unlink()                 # file gone
    with Session() as s:
        clips = pick(s, audio, AGENTS, n=30, seconds=60)
    picked = {c.call_id for c in clips}
    assert calls[0].id not in picked and calls[1].id not in picked
    assert all(c.seconds == 60 for c in clips)


def test_pick_one_clip_per_customer(env):
    Session, audio = env
    with Session() as s:
        s.execute(update(Call).values(customer_number="0500000999")); s.commit()
        assert len(pick(s, audio, AGENTS, n=5, seconds=60)) == 1


# ---------- API ----------

@pytest.fixture
def client(env):
    Session, audio = env
    with Session() as s:
        pick(s, audio, AGENTS, n=3, seconds=60)
    c = TestClient(create_app(Session, ApiConfig(secret="x" * 40, audio_dir=audio)))
    return c, Session


def as_user(c, name):
    assert c.post("/api/login", json={"username": name, "password": PW}).status_code == 200


def test_goldset_admin_only(client):
    c, _ = client
    assert c.get("/api/goldset").status_code == 401
    as_user(c, "sup")
    assert c.get("/api/goldset").status_code == 403
    assert c.put("/api/goldset/1", json={"text": "x"}).status_code == 403
    assert c.get("/api/goldset/1/audio").status_code == 403


def test_goldset_save_and_load(client):
    c, Session = client
    as_user(c, "boss")
    lst = c.get("/api/goldset").json()
    assert len(lst) == 3 and not any(x["has_text"] for x in lst) and "reference_text" not in lst[0]
    r = c.put(f"/api/goldset/{lst[0]['id']}", json={"text": "  السلام عليكم، كيف أقدر أساعدك؟  "})
    assert r.status_code == 200 and r.json()["reference_text"] == "السلام عليكم، كيف أقدر أساعدك؟"
    assert c.get(f"/api/goldset/{lst[0]['id']}").json()["has_text"]
    assert sum(x["has_text"] for x in c.get("/api/goldset").json()) == 1
    with Session() as s:
        g = s.get(GoldClip, lst[0]["id"])
        assert g.typed_by is not None and g.typed_at is not None
    c.put(f"/api/goldset/{lst[0]['id']}", json={"text": "   "})       # clearing works
    assert not c.get(f"/api/goldset/{lst[0]['id']}").json()["has_text"]


def test_goldset_audio_streams_and_is_audited(client):
    c, Session = client
    as_user(c, "boss")
    cid = c.get("/api/goldset").json()[0]["id"]
    r = c.get(f"/api/goldset/{cid}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    with Session() as s:
        assert s.scalar(select(RecordingPlay.id)) is not None


def test_goldset_bad_input(client):
    c, _ = client
    as_user(c, "boss")
    assert c.get("/api/goldset/999").status_code == 404
    assert c.put("/api/goldset/999", json={"text": "x"}).status_code == 404
    assert c.put("/api/goldset/1", json={"text": "x" * 10_001}).status_code == 422
