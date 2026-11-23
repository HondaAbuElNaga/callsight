"""API WebSocket /api/live + the Listen stub (spec 2026-11-14-live-board) against the test Postgres; Redis and the
PBX are faked (no PBX contact)."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from callsight.api import auth
from callsight.api.live import CLOSE_FORBIDDEN, CLOSE_LOGIN, CLOSE_POLICY, CLOSE_TRY_AGAIN, LiveConfig
from callsight.api.main import ApiConfig, create_app, live_origins
from callsight.db.models import ApiClient, ListenAudit
from callsight.pbx.base import PbxError
from live_fakes import FakeSource
from test_api import PASSWORD, SECRET, TEAM, engine  # noqa: F401  (engine is a fixture)

ORIGIN = "http://127.0.0.1:8080"
H = {"origin": ORIGIN}           # the Listen POST must come from the CallSight page
CALL = {"call_id": "1731574800.101", "state": "talking", "direction": "inbound", "agents": ["119"], "ringing": [],
        "caller": "••••4567", "queue": "6401", "started_at": 1000.0, "answered_at": 1005.0}
CHAN = "PJSIP/119-00000114"     # the agent's channel (private key live:channels, never in the board)
SNAP = {"seq": 41, "status": "connected", "calls": [CALL], "published_at": 1010.0}


class FakeListenPbx:
    def __init__(self, ok=False):
        self.ok, self.calls, self.unknown = ok, [], False

    def listen(self, monitor_ext, channel_id):
        self.calls.append((monitor_ext, channel_id))
        if self.unknown:
            from callsight.pbx.yeastar import ListenUnknown
            raise ListenUnknown("no answer")
        if not self.ok:
            raise PbxError("listen is switched off in the PBX adapter")
        return {"errcode": 0}


@pytest.fixture
def env(engine):  # noqa: F811
    clean(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as s:
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
        auth.create_user(s, "admin1", PASSWORD, "admin")                      # no extension
        auth.create_user(s, "ali", PASSWORD, "agent", "119", TEAM.ext_team)
    state = {"listen": False, "pbx": FakeListenPbx(), "source": FakeSource(SNAP, last_seen=1010.0),
             "channels": {CALL["call_id"]: {"119": CHAN}}}

    def build(**live_kw):
        live = LiveConfig(origins=frozenset({ORIGIN}), source=state["source"], recheck_seconds=0.1, **live_kw)
        app = create_app(Session, ApiConfig(
            secret=SECRET, audio_dir=__import__("pathlib").Path("."), teams=lambda: TEAM, live=live,
            listen_enabled=lambda: state["listen"], listen_client=lambda: state["pbx"],
            live_lookup=lambda cid: next(({**c, "channels": state["channels"].get(cid, {})}
                                          for c in state["source"].snap["calls"] if c["call_id"] == cid), None)
            if state["source"].snap else None))
        return TestClient(app)
    yield build, Session, state
    clean(engine)


def clean(engine):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("SET LOCAL callsight.audit_maintenance = 'on'"))
        c.execute(text("TRUNCATE listen_audit, recording_play, app_user RESTART IDENTITY CASCADE"))


def login(client, user="boss"):
    assert client.post("/api/login", json={"username": user, "password": PASSWORD}).status_code == 200


def ws(client, origin=ORIGIN):
    return client.websocket_connect("/api/live", headers={"origin": origin} if origin else {})


def refused(client, origin=ORIGIN) -> int:
    with pytest.raises(WebSocketDisconnect) as e:
        with ws(client, origin) as w:
            w.receive_text()
    return e.value.code


# ---------- WebSocket: Origin before accept; login / role / caps right after accept ----------

def test_ws_needs_login(env):
    build, *_ = env
    assert refused(build()) == CLOSE_LOGIN


@pytest.mark.parametrize("origin", ["http://evil.example", "http://127.0.0.1:5173", "null", None,
                                    "http://127.0.0.1:8080/"])
def test_ws_origin_allowlist_is_exact(env, origin):
    build, *_ = env
    c = build()
    login(c)
    assert refused(c, origin) == CLOSE_POLICY


def test_ws_agent_refused(env):
    build, *_ = env
    c = build()
    login(c, "ali")
    assert refused(c) == CLOSE_FORBIDDEN


def test_live_origins_default_and_override():
    assert live_origins({"WEB_PORT": "8090"}) == {"http://127.0.0.1:8090", "http://localhost:8090"}
    assert live_origins({"LIVE_ORIGINS": "https://board.lan/, http://10.0.0.5:8080"}) == {
        "https://board.lan", "http://10.0.0.5:8080"}


# ---------- snapshot + deltas ----------

def test_snapshot_then_deltas_with_seq_and_resync(env):
    build, _, state = env
    with build() as c:
        login(c)
        with ws(c) as w:
            snap = w.receive_json()
            assert snap["type"] == "snapshot" and snap["available"] and snap["seq"] == 41
            assert snap["calls"] == [CALL] and snap["status"] == "connected" and snap["server_time"] > 0
            state["source"].q.put({"seq": 42, "status": "connected", "deltas": [{"op": "remove", "call_id": "x"}],
                                   "published_at": 1011.0})
            d = w.receive_json()
            assert d["type"] == "delta" and d["seq"] == 42 and d["deltas"] == [{"op": "remove", "call_id": "x"}]
            w.send_text("snapshot")                              # the browser saw a gap
            assert w.receive_json()["type"] == "snapshot"


def test_no_live_data_when_service_off(env):
    build, _, state = env
    state["source"] = FakeSource(None, last_seen=999.0)
    with build() as c:
        login(c)
        with ws(c) as w:
            snap = w.receive_json()
    assert snap["available"] is False and snap["status"] == "off" and snap["calls"] == [] and snap["last_seen"] == 999.0


def test_socket_closed_after_logout(env):
    build, *_ = env
    with build() as c:
        login(c)
        with ws(c) as w:
            w.receive_json()
            c.post("/api/logout")
            with pytest.raises(WebSocketDisconnect) as e:
                for _ in range(50):
                    w.receive_json()
            assert e.value.code == CLOSE_LOGIN


def test_socket_closed_when_user_disabled(env):
    build, Session, _ = env
    with build() as c:
        login(c)
        with ws(c) as w:
            w.receive_json()
            with Session() as s:
                auth.disable_user(s, "boss")
            with pytest.raises(WebSocketDisconnect) as e:
                for _ in range(50):
                    w.receive_json()
            assert e.value.code == CLOSE_LOGIN


def test_max_sockets_per_user(env):
    build, *_ = env
    with build() as c:
        login(c)
        with ws(c) as a, ws(c) as b, ws(c) as d:
            for x in (a, b, d):
                x.receive_json()
            assert refused(c) == CLOSE_TRY_AGAIN


def test_max_sockets_total(env):
    build, *_ = env
    with build(max_total=1) as c:
        login(c)
        with ws(c) as a:
            a.receive_json()
            other = TestClient(c.app)
            login(other, "admin1")
            assert refused(other) == CLOSE_TRY_AGAIN


def test_slow_client_is_dropped(env):
    build, _, state = env
    with build(queue_size=2) as c:
        login(c)
        with ws(c) as w:
            w.receive_json()
            for i in range(200):                 # nobody reads: the bounded queue fills up
                state["source"].q.put({"seq": 100 + i, "status": "connected", "deltas": [], "published_at": 1.0})
            time.sleep(0.5)
            with pytest.raises(WebSocketDisconnect) as e:
                for _ in range(500):
                    w.receive_json()
            assert e.value.code == CLOSE_TRY_AGAIN


# ---------- Listen: 403 while off, both gates, audit ----------

def audit_rows(Session):
    with Session() as s:
        return [(r.kind, r.result, r.live_call_id, r.agent_ext, r.user_ext, r.attempt_id)
                for r in s.scalars(select(ListenAudit).order_by(ListenAudit.id))]


def test_listen_403_while_off_and_audited(env):
    build, Session, state = env
    c = build()
    login(c)
    r = c.post(f"/api/live/{CALL['call_id']}/listen", headers=H)
    assert r.status_code == 403 and r.json()["detail"] == "feature off"
    assert state["pbx"].calls == []                                   # no PBX call at all
    assert audit_rows(Session) == [("attempt", "denied_off", CALL["call_id"], None, "114", None)]


def test_listen_flag_is_read_on_every_request(env):
    build, _, state = env
    c = build()
    login(c)
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 403
    state["listen"] = True
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 503      # gate 2: the adapter refuses
    state["listen"] = False
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 403


def test_listen_on_writes_pending_then_result(env):
    build, Session, state = env
    state["listen"] = True
    c = build()
    login(c)
    r = c.post(f"/api/live/{CALL['call_id']}/listen", headers=H)
    assert r.status_code == 503                                       # PBX adapter's own gate refuses
    assert state["pbx"].calls == [("114", CHAN)]                     # rings 114, listens on 119's channel
    rows = audit_rows(Session)
    assert rows[0] == ("attempt", "pending", CALL["call_id"], "119", "114", None)
    assert rows[1][:2] == ("result", "refused") and rows[1][5] is not None
    state["pbx"].ok = True
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).json() == {"ok": True}
    assert audit_rows(Session)[-1][:2] == ("result", "ok")


def test_listen_needs_staff_extension_and_a_talking_call(env):
    build, Session, state = env
    state["listen"] = True
    c = build()
    login(c, "admin1")
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 403         # admin without ext
    c2 = build()
    login(c2, "ali")
    assert c2.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 403        # agents never
    c3 = build()
    login(c3)
    assert c3.post("/api/live/unknown.1/listen", headers=H).status_code == 404
    assert c3.post("/api/live/bad%20id/listen", headers=H).status_code == 422
    assert [r[1] for r in audit_rows(Session)] == ["denied_no_ext", "denied_role", "not_found"]
    assert state["pbx"].calls == []


def test_listen_needs_login(env):
    build, Session, _ = env
    assert build().post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 401
    assert audit_rows(Session) == []


def test_listen_audit_is_append_only(env):
    build, Session, _ = env
    c = build()
    login(c)
    c.post(f"/api/live/{CALL['call_id']}/listen", headers=H)
    for sql in ("UPDATE listen_audit SET result = 'ok'", "DELETE FROM listen_audit"):
        with Session() as s:
            with pytest.raises(DBAPIError, match="append-only"):
                s.execute(text(sql))
                s.commit()
    assert len(audit_rows(Session)) == 1


def test_listen_button_only_on_the_live_page_and_only_when_switched_on():
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "web" / "src"
    for f in [*src.glob("*.tsx"), *src.glob("*.ts")]:
        body = f.read_text(encoding="utf-8")
        if f.name != "api.ts":
            assert "/listen" not in body, f.name                   # one place calls the route: api.liveListen
        if f.name not in ("LivePage.tsx",):
            allowed = body.replace("تحتاج استماع", "")              # S5 flag: listen to the RECORDING
            assert "liveListen" not in body or f.name == "api.ts", f.name
            assert "استماع" not in allowed or f.name in ("PrivacyPage.tsx", "api.ts"), f.name
    live = (src / "LivePage.tsx").read_text(encoding="utf-8")
    assert "const canListen = !!me.listen && !!me.ext" in live and "{canListen && " in live


def test_me_says_whether_the_listen_button_shows(env):
    build, _, state = env
    c = build()
    login(c)
    assert c.get("/api/me").json()["listen"] is False
    state["listen"] = True
    assert c.get("/api/me").json()["listen"] is True
    c2 = build()
    login(c2, "ali")
    assert c2.get("/api/me").json()["listen"] is False             # agents never


def test_listen_refuses_an_erp_key_even_acting_as_a_supervisor(env):
    build, Session, state = env
    state["listen"] = True
    key, prefix, digest = auth.new_api_key()
    with Session() as s:
        s.add(ApiClient(name="ERP", key_prefix=prefix, key_hash=digest))
        s.commit()
    c = build()
    r = c.post(f"/api/live/{CALL['call_id']}/listen",
               headers={**H, auth.API_KEY_HEADER: key, auth.ACT_AS_HEADER: "boss"})
    assert r.status_code == 403 and state["pbx"].calls == []
    assert [r[1] for r in audit_rows(Session)] == ["denied_key"]


def test_listen_only_while_talking_and_never_on_the_own_phone(env):
    build, Session, state = env
    state["listen"] = True
    c = build()
    login(c)
    state["source"].snap["calls"][0] = {**CALL, "state": "holding"}
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 404
    state["source"].snap["calls"][0] = {**CALL, "agents": ["114", "119"]}       # the supervisor is on this call
    state["channels"] = {CALL["call_id"]: {"114": "PJSIP/114-1", "119": CHAN}}
    state["pbx"].ok = True
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 200
    assert state["pbx"].calls == [("114", CHAN)]                        # listens to 119, not to the own phone
    state["source"].snap["calls"][0] = CALL


def test_listen_no_answer_is_kept_apart_from_refused(env):
    build, Session, state = env
    state["listen"] = True
    state["pbx"].unknown = True
    c = build()
    login(c)
    r = c.post(f"/api/live/{CALL['call_id']}/listen", headers=H)
    assert r.status_code == 504 and r.json()["detail"].startswith("no answer from the PBX")
    assert audit_rows(Session)[-1][:2] == ("result", "unknown")


def test_listen_without_the_agents_channel_is_not_found_and_rings_nobody(env):
    build, Session, state = env
    state["listen"] = True
    state["channels"] = {}                                         # live service older than the channels key
    c = build()
    login(c)
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 404
    assert state["pbx"].calls == [] and [r[1] for r in audit_rows(Session)] == ["not_found"]


def test_ws_login_code_reaches_the_client_after_accept(env):
    """A close before accept is an HTTP 403 for a real browser (code 1006): login/role/caps close after accept."""
    build, *_ = env
    c = build()
    with ws(c) as w:                                      # the handshake is accepted ...
        with pytest.raises(WebSocketDisconnect) as e:
            w.receive_text()
    assert e.value.code == CLOSE_LOGIN                    # ... then closed with 4401, which the page acts on


def test_listen_needs_the_page_origin_and_is_audited(env):
    build, Session, state = env
    state["listen"] = True
    c = build()
    login(c)
    for headers in ({}, {"origin": "http://127.0.0.1:5173"}, {"origin": "null"}):
        assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=headers).status_code == 403
    assert [r[1] for r in audit_rows(Session)] == ["denied_origin"] * 3
    assert state["pbx"].calls == []


def test_listen_denials_are_rate_limited(env):
    build, Session, _ = env
    c = build()
    login(c)
    codes = [c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code for _ in range(13)]
    assert codes[:10] == [403] * 10 and codes[10:] == [429] * 3
    assert len(audit_rows(Session)) == 10                 # the table can't be flooded


def test_listen_lookup_error_is_audited(env):
    build, Session, state = env
    state["listen"] = True

    class Broken:
        snap = property(lambda self: (_ for _ in ()).throw(ConnectionError("redis down")))
    state["source"] = Broken()
    c = build()
    login(c)
    assert c.post(f"/api/live/{CALL['call_id']}/listen", headers=H).status_code == 503
    assert [r[1] for r in audit_rows(Session)] == ["error"]
    assert state["pbx"].calls == []


def test_listen_audit_truncate_refused_unless_maintenance(env):
    build, Session, _ = env
    c = build()
    login(c)
    c.post(f"/api/live/{CALL['call_id']}/listen", headers=H)
    with Session() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("TRUNCATE listen_audit"))
            s.commit()
    assert len(audit_rows(Session)) == 1
