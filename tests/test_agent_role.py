"""Agent role (spec 2026-11-14-hardening, decision 1): own calls only, own KPIs only, staff-only routes 403.

The route walk is deny-by-default: every /api route must be listed below, so a new route fails this test until
someone decides whether agents may use it.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from starlette.websockets import WebSocketDisconnect

from callsight.api import auth
from callsight.api.calls import Scope, visible_scope
from callsight.api.live import LiveConfig, NoLiveSource
from callsight.api.main import ApiConfig, create_app
from callsight.config import RIYADH
from callsight.db.models import Agent, AppUser, Call, CallLeg, RecordingPlay
from test_api import SECRET, PASSWORD, TEAM, engine, wav_bytes  # noqa: F401  (engine is a fixture)

PUBLIC = {("POST", "/api/login"), ("POST", "/api/logout")}
AGENT_ALLOWED = {
    ("GET", "/api/me"), ("GET", "/api/agents"), ("GET", "/api/calls"), ("GET", "/api/calls/summary"),
    ("GET", "/api/calls/{call_id}"), ("GET", "/api/calls/{call_id}/recording"), ("GET", "/api/kpi"),
    ("GET", "/api/calls/{call_id}/transcript"), ("GET", "/api/calls/{call_id}/transcript.txt"),
    # agent-supervisor spec: own follow-up forms, calls shared with them, the summary of own/shared calls
    ("GET", "/api/calls/{call_id}/followup"), ("PUT", "/api/calls/{call_id}/followup"), ("GET", "/api/followups"),
    ("GET", "/api/shares"), ("GET", "/api/calls/{call_id}/shares"), ("POST", "/api/shares/{share_id}/seen"),
    ("GET", "/api/calls/{call_id}/summary"),
}
STAFF_ONLY = {
    ("GET", "/api/reports/pdf"), ("GET", "/api/reports"), ("GET", "/api/reports/{report_id}/pdf"),
    ("GET", "/api/goldset"), ("GET", "/api/goldset/{clip_id}"), ("PUT", "/api/goldset/{clip_id}"),
    ("GET", "/api/goldset/{clip_id}/audio"), ("GET", "/api/health"),
    # call analysis (spec 2026-11-14-call-analysis, decision 9): agents see nothing of it
    ("GET", "/api/calls/{call_id}/analysis"), ("GET", "/api/rating/queue"), ("GET", "/api/rating/calls/{call_id}"),
    ("PUT", "/api/rating/calls/{call_id}"),
    ("WS", "/api/live"), ("POST", "/api/live/{call_id}/listen"),       # live board + Listen stub (live-board spec)
    # agent-supervisor spec: sharing (staff), users / settings / log / delete (admin)
    ("POST", "/api/calls/{call_id}/shares"), ("DELETE", "/api/shares/{share_id}"),
    ("GET", "/api/admin/users"), ("POST", "/api/admin/users"), ("PATCH", "/api/admin/users/{user_id}"),
    ("POST", "/api/admin/users/{user_id}/password"), ("GET", "/api/admin/settings"), ("PUT", "/api/admin/settings"),
    ("GET", "/api/admin/log"), ("POST", "/api/calls/{call_id}/delete-content"),
}
CATCH_ALL = {("GET", "/api/{rest:path}")}       # plain 404 for unknown /api paths
ORIGIN = "http://testserver"                     # live WebSocket Origin allowlist in these tests


def _today_at(hour: int) -> datetime:
    return datetime.now(RIYADH).replace(hour=hour, minute=0, second=0, microsecond=0)


def _call(s, uid, hour, *, agent=None, missed=None, status="answered", direction="inbound", team="customer_service",
          rec=None) -> Call:
    t = _today_at(hour)
    c = Call(uid=uid, direction=direction, entry="queue", customer_number="0500000001", team=team, agent_ext=agent,
             missed_agent_ext=missed, start_time=t, end_time=t + timedelta(minutes=3), talk_seconds=100,
             total_seconds=180, status=status, recording_status="ok" if rec else "none", recording_path=rec)
    s.add(c)
    s.flush()
    return c


def _truncate(engine):  # noqa: F811
    with engine.begin() as c:
        # the route walk writes listen_audit rows (refused Listen); its TRUNCATE trigger needs the maintenance flag
        c.execute(text("SET LOCAL callsight.audit_maintenance = 'on'"))
        c.execute(text("TRUNCATE listen_audit, recording_play, app_user, call_leg, call, agent, queue, ingest_state, "
                       "reconcile_run RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def _no_audit_rows_left(request):
    """Leave no listen_audit rows behind: other test files TRUNCATE app_user ... CASCADE."""
    yield
    if "engine" in request.fixturenames:
        _truncate(request.getfixturevalue("engine"))


@pytest.fixture
def env(engine, tmp_path):  # noqa: F811
    _truncate(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    (tmp_path / "r").mkdir()
    for name in ("own.wav", "other.wav"):
        (tmp_path / "r" / name).write_bytes(wav_bytes())
    with Session() as s:
        s.add_all([Agent(ext="119", name="Agent 119", team="customer_service", in_team=True),
                   Agent(ext="104", name="Agent 104", team="customer_service", in_team=True),
                   Agent(ext="116", name="Agent 116", team="after_sales", in_team=True)])
        own = _call(s, "own", 9, agent="119", rec="r/own.wav")
        s.add_all([CallLeg(id=1, call_id=own.id, uid="own", kind="queue", start_time=own.start_time,
                           to_number="6401", disposition="ANSWERED", raw={}),
                   CallLeg(id=2, call_id=own.id, uid="own", kind="extension", start_time=own.start_time,
                           to_number="104", disposition="NO ANSWER", raw={}),
                   CallLeg(id=3, call_id=own.id, uid="own", kind="extension", start_time=own.start_time,
                           to_number="119", disposition="ANSWERED", raw={})])
        own_out = _call(s, "own-out", 10, agent="119", direction="outbound")
        other = _call(s, "other", 11, agent="104", rec="r/other.wav")
        missed = _call(s, "missed", 12, missed="119", status="abandoned_queue")   # rang 119, nobody answered
        sales = _call(s, "sales", 13, agent="116", team="after_sales")
        s.commit()
        ids = {"own": own.id, "own_out": own_out.id, "other": other.id, "missed": missed.id, "sales": sales.id}
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
        auth.create_user(s, "ali", PASSWORD, "agent", "119", TEAM.ext_team)
    app = create_app(Session, ApiConfig(secret=SECRET, audio_dir=tmp_path, teams=lambda: TEAM, reports_dir=tmp_path,
                                        live=LiveConfig(origins=frozenset({ORIGIN}), source=NoLiveSource())))
    return TestClient(app), Session, ids, app


def login(client, user="ali"):
    assert client.post("/api/login", json={"username": user, "password": PASSWORD}).status_code == 200


def today() -> dict:
    d = datetime.now(RIYADH).date().isoformat()
    return {"from": d, "to": d}


# ---------- route walk (deny by default) ----------

def _api_routes(app) -> set[tuple[str, str]]:
    """HTTP routes as (method, path); WebSocket routes as ("WS", path) so they must be classified too."""
    http = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api")
            for m in r.methods - {"HEAD", "OPTIONS"}}
    ws = {("WS", r.path) for r in app.routes if isinstance(r, APIWebSocketRoute) and r.path.startswith("/api")}
    return http | ws


def test_route_walk_sees_websockets():
    from fastapi import FastAPI
    a = FastAPI()

    @a.websocket("/api/live")
    async def live(ws):  # noqa: ANN001
        pass
    assert ("WS", "/api/live") in _api_routes(a)


def test_every_api_route_is_classified(env):
    *_, app = env
    unknown = _api_routes(app) - PUBLIC - AGENT_ALLOWED - STAFF_ONLY - CATCH_ALL
    assert not unknown, f"classify these routes for the agent role (AGENT_ALLOWED or STAFF_ONLY): {sorted(unknown)}"
    assert not (AGENT_ALLOWED & STAFF_ONLY)


def _url(path: str, ids: dict) -> str:
    """Fill every {param}: the call id of the agent's own call, 1 for the other ids."""
    import re
    return re.sub(r"\{[a-z_]+\}", "1", path.replace("{call_id}", str(ids["own"])))


def _ws_close_code(client, path) -> int:
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(path, headers={"origin": ORIGIN}) as w:
            w.receive_text()
    return e.value.code


def test_staff_only_routes_refuse_agents(env):
    client, _, ids, app = env
    login(client)
    for method, path in sorted(_api_routes(app) & STAFF_ONLY):
        if method == "WS":                      # refused before accept: close code 4403 (= HTTP 403)
            assert _ws_close_code(client, path) == 4403, path
            continue
        url = _url(path, ids)
        r = client.request(method, url, json={"text": "x", "score": 5} if method == "PUT" else None)
        assert r.status_code == 403, (method, path, r.status_code)


def test_agent_allowed_routes_answer(env):
    client, _, ids, app = env
    login(client)
    for method, path in sorted(_api_routes(app) & AGENT_ALLOWED):
        if method != "GET":          # writes are tested with real bodies in test_work_api.py
            continue
        url = path.replace("{call_id}", str(ids["own"]))
        params = today() if path.startswith("/api/calls") else None
        r = client.request(method, url, params=params)
        if "/transcript" in path:   # own call, but no transcript row in this fixture: 404 "no transcript", not 403
            assert r.status_code == 404 and r.json()["detail"] == "no transcript", (method, path)
        else:
            assert r.status_code == 200, (method, path)


def test_agent_routes_need_login(env):
    client, _, ids, app = env
    for method, path in sorted(_api_routes(app) & (AGENT_ALLOWED | STAFF_ONLY)):
        if method == "WS":                      # close code 4401 (= HTTP 401)
            assert _ws_close_code(client, path) == 4401, path
            continue
        assert client.request(method, _url(path, ids)).status_code == 401, (method, path)


# ---------- own calls only ----------

def test_list_shows_only_own_answered_calls(env):
    client, _, ids, _ = env
    login(client)
    items = client.get("/api/calls", params=today()).json()["items"]
    assert {i["id"] for i in items} == {ids["own"], ids["own_out"]}     # not the missed ring (decision 1)
    assert client.get("/api/calls/summary", params=today()).json()["total"] == 2


@pytest.mark.parametrize("agent", ["104", "116", "119"])
def test_agent_parameter_is_overridden(env, agent):
    client, _, ids, _ = env
    login(client)
    items = client.get("/api/calls", params={**today(), "agent": agent}).json()["items"]
    assert {i["id"] for i in items} == {ids["own"], ids["own_out"]}
    assert client.get("/api/calls/summary", params={**today(), "agent": agent}).json()["total"] == 2


def test_search_and_status_filters_stay_inside_scope(env):
    client, _, ids, _ = env
    login(client)
    r = client.get("/api/calls", params={**today(), "q": "0500000001"}).json()
    assert {i["id"] for i in r["items"]} == {ids["own"], ids["own_out"]}
    r = client.get("/api/calls", params={**today(), "status": "abandoned_queue"}).json()
    assert r["total"] == 0


def test_others_calls_are_404(env):
    client, Session, ids, _ = env
    login(client)
    for key in ("other", "missed", "sales"):
        assert client.get(f"/api/calls/{ids[key]}").status_code == 404, key
    assert client.get(f"/api/calls/{ids['other']}/recording").status_code == 404
    assert client.get("/api/calls/999999").status_code == 404
    with Session() as s:   # a refused recording is not audited as played
        assert s.scalar(select(RecordingPlay.id).where(RecordingPlay.call_id == ids["other"])) is None


def test_own_recording_plays_and_is_audited(env):
    client, Session, ids, _ = env
    login(client)
    r = client.get(f"/api/calls/{ids['own']}/recording")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with Session() as s:
        user = s.scalars(select(AppUser).where(AppUser.username == "ali")).one()
        assert s.scalar(select(RecordingPlay.user_id).where(RecordingPlay.call_id == ids["own"])) == user.id


def test_detail_hides_other_agents_legs(env):
    client, _, ids, _ = env
    login(client)
    legs = client.get(f"/api/calls/{ids['own']}").json()["legs"]
    assert [(leg["kind"], leg["to_number"]) for leg in legs] == [("queue", "6401"), ("extension", "119")]
    client.post("/api/logout")
    login(client, "boss")    # staff still see every leg
    assert len(client.get(f"/api/calls/{ids['own']}").json()["legs"]) == 3


def test_agents_list_has_only_own_entry(env):
    client, *_ = env
    login(client)
    assert client.get("/api/agents").json() == [{"ext": "119", "name": "Agent 119", "team": "customer_service"}]
    assert client.get("/api/me").json() == {"username": "ali", "role": "agent", "ext": "119"}


def test_staff_still_see_everything(env):
    client, _, ids, _ = env
    login(client, "boss")
    assert client.get("/api/calls", params=today()).json()["total"] == 5
    assert client.get(f"/api/calls/{ids['other']}").status_code == 200
    assert len(client.get("/api/agents").json()) == 3


# ---------- own KPIs only ----------

def test_kpi_is_forced_to_own_extension(env):
    client, *_ = env
    login(client)
    for params in ({}, {"team": "after_sales"}, {"agent": "104"}, {"team": "customer_service", "agent": "116"}):
        r = client.get("/api/kpi", params={"period": "week", **params})
        assert r.status_code == 200, params
        d = r.json()
        assert d["scope"] == {"team": None, "agent": "119"}
        assert [a["ext"] for a in d["agents"]] == ["119"] and d["by_status"] is None


def test_agent_whose_ext_left_the_team_gets_no_kpis(env):
    client, Session, *_ = env
    with Session() as s:
        s.add(AppUser(username="old", password_hash=auth.hash_password(PASSWORD), role="agent", ext="555",
                      active=True, token_version=0, failed_logins=0))
        s.commit()
    login(client, "old")
    assert client.get("/api/kpi").status_code == 403
    assert client.get("/api/calls", params=today()).json()["total"] == 0


# ---------- scope helper + accounts ----------

def test_visible_scope_denies_by_default():
    def u(role, ext=None):
        return AppUser(username="x", role=role, ext=ext)
    assert visible_scope(u("admin")) == Scope(everything=True)
    assert visible_scope(u("supervisor", "114")) == Scope(everything=True)
    assert visible_scope(u("agent", "119")) == Scope(agent_ext="119")
    for nobody in (u("agent"), u("agent", ""), u("guest", "119"), u("", "119")):
        s = visible_scope(nobody)
        assert not s.everything and not s.agent_ext and not s.allows(Call(agent_ext=None))
        assert not s.allows(Call(agent_ext="119"))


def test_agent_without_ext_sees_nothing(env):
    client, Session, *_ = env
    with Session() as s:
        s.add(AppUser(username="noext", password_hash=auth.hash_password(PASSWORD), role="agent", ext=None,
                      active=True, token_version=0, failed_logins=0))
        s.commit()
    login(client, "noext")
    assert client.get("/api/calls", params=today()).json()["total"] == 0
    assert client.get("/api/agents").json() == []


def test_create_agent_needs_team_extension(env):
    _, Session, *_ = env
    with Session() as s:
        with pytest.raises(ValueError, match="--ext"):
            auth.create_user(s, "a1", PASSWORD, "agent", None, TEAM.ext_team)
        with pytest.raises(ValueError, match="teams.yaml"):
            auth.create_user(s, "a2", PASSWORD, "agent", "302", TEAM.ext_team)
        with pytest.raises(ValueError, match="teams.yaml"):
            auth.create_user(s, "a3", PASSWORD, "agent", "119")          # no teams given -> refused
        assert auth.create_user(s, "a4", PASSWORD, "agent", "104", TEAM.ext_team).ext == "104"


def test_changing_ext_ends_open_logins(env):
    client, Session, ids, _ = env
    login(client)
    assert client.get("/api/me").status_code == 200
    with Session() as s:
        with pytest.raises(ValueError):
            auth.set_ext(s, "ali", "302", TEAM.ext_team)
        auth.set_ext(s, "ali", "104", TEAM.ext_team)
    assert client.get("/api/me").status_code == 401
    login(client)
    assert {i["id"] for i in client.get("/api/calls", params=today()).json()["items"]} == {ids["other"]}
