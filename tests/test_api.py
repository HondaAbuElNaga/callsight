"""Web API against the test Postgres (TEST_DATABASE_URL), with real anonymised calls loaded."""
import io
import json
import os
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from callsight.api import auth
from callsight.api.main import ApiConfig, create_app
from callsight.config import RIYADH
from callsight.db.models import Agent, AppUser, Base, Call, RecordingPlay
from callsight.ingest import service
from callsight.ingest.classify import TeamConfig

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
URL = os.getenv("TEST_DATABASE_URL")
FIX = json.loads((Path(__file__).parent / "fixtures" / "cdr_shapes.json").read_text(encoding="utf-8"))
TEAM = TeamConfig(ext_team={"104": "customer_service", "119": "customer_service", "116": "after_sales"},
                  queue_team={"6401": "customer_service"})
ROWS = [r for c in FIX for r in c["legs"]]
SECRET = "x" * 40
PASSWORD = "correct-horse-battery"


def wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"\x01\x00" * 8000)
    return buf.getvalue()


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
def env(engine, tmp_path):
    with engine.begin() as c:
        c.execute(text("TRUNCATE recording_play, app_user, call_leg, call, agent, queue, ingest_state, reconcile_run "
                       "RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as s:
        service.ingest_rows(s, ROWS, TEAM); s.commit()
        s.add_all([Agent(ext="119", name="Agent 119", team="customer_service", in_team=True),
                   Agent(ext="104", name="Agent 104", team="customer_service", in_team=True),
                   Agent(ext="116", name="Agent 116", team="after_sales", in_team=True),
                   Agent(ext="302", name="Sales 302", team=None, in_team=False)])
        s.commit()
        for c in s.scalars(select(Call).where(Call.recording_file.is_not(None))):   # pretend downloaded
            rel = f"2026/11/01/{c.recording_file}"
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(wav_bytes())
            c.recording_path, c.recording_status = rel, "ok"
        s.commit()
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
    app = create_app(Session, ApiConfig(secret=SECRET, audio_dir=tmp_path))
    return TestClient(app), Session, tmp_path


def login(client, password=PASSWORD, user="boss"):
    return client.post("/api/login", json={"username": user, "password": password})


def all_days():
    ts = [int(r["timestamp"]) for r in ROWS]
    lo = datetime.fromtimestamp(min(ts), tz=RIYADH).date()
    hi = datetime.fromtimestamp(max(ts), tz=RIYADH).date()
    return {"from": lo.isoformat(), "to": hi.isoformat()}


# ---------- auth ----------

def test_everything_needs_login(env):
    client, Session, _ = env
    with Session() as s:
        cid = s.scalar(select(Call.id).where(Call.recording_status == "ok"))
    for url in ("/api/me", "/api/agents", "/api/calls", "/api/calls/summary", f"/api/calls/{cid}",
                f"/api/calls/{cid}/recording"):
        assert client.get(url).status_code == 401, url


def test_login_sets_httponly_cookie_and_logout_clears_it(env):
    client, _, _ = env
    r = login(client)
    assert r.status_code == 200 and r.json()["role"] == "supervisor"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/me").json()["username"] == "boss"
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_wrong_password_and_unknown_user_same_answer(env):
    client, _, _ = env
    a, b = login(client, "nope-nope-nope"), login(client, user="ghost")
    assert a.status_code == b.status_code == 401 and a.json()["detail"] == b.json()["detail"]


def test_lockout_after_five_wrong_passwords(env):
    client, Session, _ = env
    for _ in range(5):
        assert login(client, "wrong-password-1").status_code == 401
    r = login(client)                          # right password, but locked
    assert r.status_code == 401
    assert r.json()["detail"] == login(client, user="ghost").json()["detail"]   # no hint which users exist
    with Session() as s:                       # after 15 minutes it works again
        u = s.scalars(select(AppUser)).one()
        u.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1); s.commit()
    assert login(client).status_code == 200


def test_password_change_ends_old_logins(env):
    client, Session, _ = env
    login(client)
    with Session() as s:
        auth.set_password(s, "boss", "another-long-password")
    assert client.get("/api/me").status_code == 401


def test_forged_or_expired_token_rejected(env):
    client, Session, _ = env
    with Session() as s:
        u = s.scalars(select(AppUser)).one()
        bad = auth.make_token(u, "y" * 40)
        old = auth.make_token(u, SECRET, now=datetime.now(timezone.utc) - timedelta(hours=9))
    for tok in (bad, old, "garbage"):
        client.cookies.set(auth.COOKIE_NAME, tok)
        assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("secret", ["short", "change-me", "change-me-to-a-long-random-text-of-48-characters"])
def test_weak_or_example_secret_refused(secret):
    with pytest.raises(RuntimeError):
        create_app(lambda: None, ApiConfig(secret=secret, audio_dir=Path(".")))


def test_parallel_wrong_passwords_still_lock(env):
    """Review fix: concurrent attempts must not lose counts (row lock)."""
    from concurrent.futures import ThreadPoolExecutor
    client, Session, _ = env
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: login(client, "wrong-password-x"), range(8)))
    with Session() as s:
        u = s.scalars(select(AppUser)).one()
        assert u.locked_until is not None and u.locked_until > datetime.now(timezone.utc)


def test_logout_ends_copied_cookie(env):
    client, _, _ = env
    login(client)
    copied = client.cookies.get(auth.COOKIE_NAME)
    client.post("/api/logout")
    client.cookies.set(auth.COOKIE_NAME, copied)
    assert client.get("/api/me").status_code == 401


def test_weak_password_refused(env):
    _, Session, _ = env
    with Session() as s, pytest.raises(ValueError):
        auth.create_user(s, "x", "short", "admin")


# ---------- call list ----------

def test_list_matches_db_and_summary(env):
    client, Session, _ = env
    login(client)
    r = client.get("/api/calls", params=all_days()).json()
    summ = client.get("/api/calls/summary", params=all_days()).json()
    with Session() as s:
        n = s.scalar(select(func.count()).select_from(Call))
    assert r["total"] == summ["total"] == n
    assert sum(summ["by_status"].values()) == n
    times = [i["start_time"] for i in r["items"]]
    assert times == sorted(times, reverse=True)


@pytest.mark.parametrize("params", [
    {"agent": "119"}, {"status": "answered"}, {"status": "abandoned_queue"}, {"direction": "outbound"},
    {"agent": "119", "status": "answered"}, {"q": "0500000"},
])
def test_filters_match_db(env, params):
    client, Session, _ = env
    login(client)
    p = {**all_days(), **params}
    r = client.get("/api/calls", params=p).json()
    summ = client.get("/api/calls/summary", params=p).json()
    with Session() as s:
        q = select(func.count()).select_from(Call)
        if "agent" in params:
            q = q.where((Call.agent_ext == params["agent"]) | (Call.missed_agent_ext == params["agent"]))
        if "status" in params:
            q = q.where(Call.status == params["status"])
        if "direction" in params:
            q = q.where(Call.direction == params["direction"])
        if "q" in params:
            q = q.where(Call.customer_number.contains(params["q"]))
        expected = s.scalar(q)
    assert r["total"] == summ["total"] == expected
    for item in r["items"]:
        if "agent" in params:
            assert item["agent_ext"] == params["agent"]
        if "status" in params:
            assert item["status"] == params["status"]


def test_date_filter_uses_riyadh_days(env):
    client, Session, _ = env
    login(client)
    with Session() as s:
        first = s.scalars(select(Call).order_by(Call.start_time)).first()
    day = first.start_time.astimezone(RIYADH).date().isoformat()
    items = client.get("/api/calls", params={"from": day, "to": day}).json()["items"]
    assert first.id in [i["id"] for i in items]
    assert all(datetime.fromisoformat(i["start_time"]).astimezone(RIYADH).date().isoformat() == day for i in items)


def test_paging(env, monkeypatch):
    import callsight.api.calls as calls_mod
    monkeypatch.setattr(calls_mod, "PAGE_SIZE", 5)
    client, _, _ = env
    login(client)
    p1 = client.get("/api/calls", params={**all_days(), "page": 1}).json()
    p2 = client.get("/api/calls", params={**all_days(), "page": 2}).json()
    assert len(p1["items"]) == 5 and not {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}


@pytest.mark.parametrize("params", [
    {"status": "deleted"}, {"direction": "sideways"}, {"agent": "1; drop table call"}, {"q": "%"},
    {"from": "2026-11-10", "to": "2026-11-01"}, {"page": 0},
])
def test_bad_filters_rejected(env, params):
    client, _, _ = env
    login(client)
    assert client.get("/api/calls", params=params).status_code == 422


def test_agents_only_team(env):
    client, _, _ = env
    login(client)
    assert [a["ext"] for a in client.get("/api/agents").json()] == ["104", "116", "119"]


def test_detail_has_legs_in_order(env):
    client, Session, _ = env
    login(client)
    with Session() as s:
        cid = s.scalar(select(Call.id).where(Call.status == "answered", Call.queue_number == "6401"))
    d = client.get(f"/api/calls/{cid}").json()
    assert [leg["kind"] for leg in d["legs"]][:1] == ["ivr"] and d["agent_name"]
    assert client.get("/api/calls/999999").status_code == 404


# ---------- recordings ----------

def test_recording_plays_with_range_and_is_audited(env):
    client, Session, _ = env
    login(client)
    with Session() as s:
        cid = s.scalar(select(Call.id).where(Call.recording_status == "ok"))
    full = client.get(f"/api/calls/{cid}/recording")
    assert full.status_code == 200 and full.headers["content-type"] == "audio/wav"
    part = client.get(f"/api/calls/{cid}/recording", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and len(part.content) == 100
    start = client.get(f"/api/calls/{cid}/recording", headers={"Range": "bytes=0-"})
    assert start.status_code == 206
    with Session() as s:   # play + seeks within 10 minutes = one audit row
        assert s.scalar(select(func.count()).select_from(RecordingPlay)) == 1


def test_any_range_request_is_audited(env):
    """Review fix: 'bytes=1-' must not skip the audit."""
    client, Session, _ = env
    login(client)
    with Session() as s:
        cid = s.scalar(select(Call.id).where(Call.recording_status == "ok"))
    assert client.get(f"/api/calls/{cid}/recording", headers={"Range": "bytes=1-"}).status_code == 206
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(RecordingPlay).where(RecordingPlay.call_id == cid)) == 1


def test_new_play_after_audit_window_is_logged_again(env):
    client, Session, _ = env
    login(client)
    with Session() as s:
        cid = s.scalar(select(Call.id).where(Call.recording_status == "ok"))
    client.get(f"/api/calls/{cid}/recording")
    with Session() as s:
        row = s.scalars(select(RecordingPlay)).one()
        row.played_at = datetime.now(timezone.utc) - timedelta(minutes=11); s.commit()
    client.get(f"/api/calls/{cid}/recording")
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(RecordingPlay)) == 2


def test_recording_path_outside_audio_dir_refused(env):
    client, Session, tmp_path = env
    login(client)
    secret = tmp_path.parent / "secret.wav"
    secret.write_bytes(wav_bytes())
    with Session() as s:
        c = s.scalars(select(Call).where(Call.recording_status == "ok")).first()
        c.recording_path = "../secret.wav"; s.commit()
        cid = c.id
    assert client.get(f"/api/calls/{cid}/recording").status_code == 404


def test_no_recording_404(env):
    client, Session, _ = env
    login(client)
    with Session() as s:
        cid = s.scalar(select(Call.id).where(Call.recording_status == "none"))
    assert client.get(f"/api/calls/{cid}/recording").status_code == 404


def test_security_headers(env):
    client, _, _ = env
    r = client.get("/api/me")
    assert r.headers["x-content-type-options"] == "nosniff" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
