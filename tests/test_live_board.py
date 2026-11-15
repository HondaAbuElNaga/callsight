"""Live board without a PBX (spec 2026-11-14-live-board): parser + state on SYNTHETIC events, the live service with a
fake WebSocket / fake PBX HTTP / fake Redis, pbx:auth_blocked in every login path, compose defaults."""
import asyncio
import json
import logging
from pathlib import Path

import pytest
import yaml

from callsight import cli
from callsight.live import events, service as live_service
from callsight.live.bus import CHANNEL, LAST_SEEN_KEY, SNAPSHOT_KEY, Publisher
from callsight.live.events import CallEnd, CallState, mask_number, parse
from callsight.live.service import (Backoff, LiveService, LoginGate, LoginRateLimited, Timing, TokenStore, main,
                                    ws_url)
from callsight.live.state import LiveState
from callsight.ingest.classify import TeamConfig
from callsight.pbx.authblock import AUTH_BLOCKED_KEY, AuthGuard, unblock
from callsight.pbx.base import AuthBlocked, AuthError, PbxError, TokenLimit
from callsight.pbx.yeastar import YeastarAdapter
from live_fakes import FIXTURES, FakeConnect, FakePbxHttp, FakeRedis, FakeWs, WsClosed

ROOT = Path(__file__).resolve().parent.parent
RAW_CALLER = "0551234567"


def ev(name):
    return parse(FIXTURES[name])


# ---------- parser (synthetic payloads) ----------

def test_fixtures_are_labelled_synthetic():
    assert "SYNTHETIC" in FIXTURES["_note"]


def test_parse_call_state_with_msg_as_json_string():
    e = ev("ring")
    assert isinstance(e, CallState) and e.call_id == "1731574800.101"
    kinds = [(m.kind, m.ext, m.status) for m in e.members]
    assert kinds == [("inbound", None, "ANSWERED"), ("extension", "119", "RING"), ("extension", "104", "ALERT")]
    assert e.members[0].party == "••••4567"                      # masked at parse time
    assert all(m.queue == "6401" for m in e.members)


def test_parse_msg_as_object_and_outbound_party_masked():
    e = ev("outbound_dict_msg")
    assert e.call_id == "1731575000.202"
    assert [m.party for m in e.members] == [None, "••••0001"]


def test_parse_call_end():
    assert ev("end") == CallEnd("1731574800.101")


def test_parse_tolerates_unknown_and_missing_fields():
    e = ev("unknown_fields")
    # unknown member type kept as 'unknown'; member without channel_id dropped; junk entries ignored
    assert [(m.kind, m.ext, m.status) for m in e.members] == [("extension", "116", "RING"), ("unknown", None, "ANSWERED"),
                                                             ("inbound", None, "")]
    assert ev("missing_members") == CallState("1731575200.404", ())
    assert ev("missing_call_id") is None
    assert ev("extension_status_30008") is None                   # topic not used (agent status row = cut)
    for junk in ("heartbeat response", "{not json", "[]", '{"type":"x"}', '{"type":30011,"msg":"[1]"}', b"\xff", None,
                 {"type": 30011, "msg": {"call_id": "x" * 500, "members": "nope"}}):
        e = parse(junk)
        assert e is None or (isinstance(e, CallState) and len(e.call_id) == 64 and e.members == ())


def test_alternative_member_shape_with_type_field():
    e = parse({"type": 30011, "msg": {"call_id": "a1", "members": [
        {"type": "extension", "number": "119", "channel_id": "c1", "member_status": "answer"},
        {"type": "inbound", "from": "+966551234567", "channel_id": "c2", "member_status": "ANSWERED"}]}})
    assert [(m.kind, m.ext, m.party, m.status) for m in e.members] == [
        ("extension", "119", None, "ANSWER"), ("inbound", None, "••••4567", "ANSWERED")]


@pytest.mark.parametrize("raw,masked", [("0551234567", "••••4567"), ("+966 55 123 4567", "••••4567"),
                                        ("1234", "••••"), ("", None), (None, None), ("anonymous", None)])
def test_mask_number(raw, masked):
    assert mask_number(raw) == masked


# ---------- state transitions ----------

def test_ring_talk_hold_end():
    st = LiveState()
    d = st.apply(ev("ring"), now=1000.0)
    c = d[0]["call"]
    assert d[0]["op"] == "upsert" and c["state"] == "ringing" and c["ringing"] == ["104", "119"]
    assert c["direction"] == "inbound" and c["caller"] == "••••4567" and c["queue"] == "6401"
    assert c["answered_at"] is None and c["started_at"] == 1000.0
    c = st.apply(ev("answer"), now=1005.0)[0]["call"]
    assert c["state"] == "talking" and c["agents"] == ["119"] and c["ringing"] == [] and c["answered_at"] == 1005.0
    assert st.apply(ev("hold"), now=1010.0)[0]["call"]["state"] == "holding"
    c = st.apply(ev("unhold"), now=1015.0)[0]["call"]
    assert c["state"] == "talking" and c["answered_at"] == 1005.0       # first answer time kept
    assert st.apply(ev("end"), now=1020.0) == [{"op": "remove", "call_id": "1731574800.101"}]
    assert st.calls == {}
    assert st.apply(ev("end"), now=1021.0) == []                        # second end: nothing


def test_transfer_moves_the_call_to_the_new_agent():
    st = LiveState()
    for name, t in (("ring", 1.0), ("answer", 2.0)):
        st.apply(ev(name), now=t)
    c = st.apply(ev("transfer_ring"), now=3.0)[0]["call"]
    assert c["agents"] == [] and c["ringing"] == ["116"] and c["state"] == "ringing"
    c = st.apply(ev("transfer_answer"), now=4.0)[0]["call"]
    assert c["agents"] == ["116"] and c["state"] == "talking" and c["caller"] == "••••4567"


def test_queue_waiting_until_an_agent_rings():
    st = LiveState()
    waiting = CallState("q1", (events.Member("inbound", "t1", "ANSWERED", party="••••1111", queue="6401"),))
    c = st.apply(waiting, now=1.0)[0]["call"]
    assert c["state"] == "waiting" and c["queue"] == "6401"        # queue join (best effort from call_path)
    leave = CallState("q1", (events.Member("inbound", "t1", "BYE"),))
    assert st.apply(leave, now=2.0) == [{"op": "remove", "call_id": "q1"}]   # caller gave up: queue leave


def test_outbound_and_internal_directions():
    st = LiveState()
    assert st.apply(ev("outbound_dict_msg"), 1.0)[0]["call"]["direction"] == "outbound"
    c = st.apply(ev("internal"), 1.0)[0]["call"]
    assert c["direction"] == "internal" and c["agents"] == ["104", "119"] and c["caller"] is None


def test_bye_for_unknown_call_is_ignored():
    st = LiveState()
    assert st.apply(CallState("zz", (events.Member("extension", "c", "BYE", ext="119"),)), now=1.0) == []


def test_prune_after_4_hours_and_reset():
    st = LiveState()
    st.apply(ev("ring"), now=0.0)
    st.apply(ev("outbound_dict_msg"), now=3 * 3600.0)
    assert st.prune(now=4 * 3600.0 + 1) == [{"op": "remove", "call_id": "1731574800.101"}]
    assert list(st.calls) == ["1731575000.202"]
    assert st.reset() == [{"op": "reset"}] and st.calls == {}


# ---------- team only (user, 2026-11-16) ----------

TEAM_ONLY = TeamConfig(ext_team={"104": "customer_service", "119": "customer_service", "116": "after_sales"},
                       queue_team={"6401": "customer_service"})


def test_other_company_calls_are_never_published():
    st = LiveState(team=TEAM_ONLY)
    other = CallState("o1", (events.Member("extension", "a", "ANSWERED", ext="302"),
                             events.Member("outbound", "b", "ANSWERED", party="••••9999")))
    assert st.apply(other, now=1.0) == [] and st.snapshot() == []
    assert st.apply(CallEnd("o1"), now=2.0) == [] and st.calls == {}     # its end is not published either


def test_ivr_call_appears_when_a_team_agent_rings_and_stays_after_transfer_out():
    st = LiveState(team=TEAM_ONLY)
    assert st.apply(CallState("c1", (events.Member("inbound", "t", "ANSWERED", party="••••1111"),)), now=1.0) == []
    d = st.apply(CallState("c1", (events.Member("inbound", "t", "ANSWERED", party="••••1111"),
                                  events.Member("extension", "e", "RING", ext="116"))), now=2.0)
    assert d[0]["op"] == "upsert" and d[0]["call"]["ringing"] == ["116"]
    d = st.apply(CallState("c1", (events.Member("extension", "e", "BYE", ext="116"),
                                  events.Member("extension", "x", "ANSWERED", ext="302"))), now=3.0)
    assert d[0]["op"] == "upsert" and d[0]["call"]["state"] == "talking" and d[0]["call"]["agents"] == []   # kept, 302 not named
    assert [c["call_id"] for c in st.snapshot()] == ["c1"]
    assert st.apply(CallEnd("c1"), now=4.0) == [{"op": "remove", "call_id": "c1"}]


def test_non_team_calls_stay_silent_when_pruned_or_evicted():
    st = LiveState(max_calls=2, team=TEAM_ONLY)
    st.apply(CallState("o1", (events.Member("extension", "a", "RING", ext="302"),)), now=0.0)
    st.apply(CallState("o2", (events.Member("extension", "b", "RING", ext="303"),)), now=1.0)
    assert st.apply(CallState("o3", (events.Member("extension", "c", "RING", ext="304"),)), now=2.0) == []   # cap
    assert st.prune(now=5 * 3600.0) == [] and st.calls == {}


def test_transferred_team_call_does_not_name_other_departments_extensions():
    st = LiveState(team=TEAM_ONLY)
    st.apply(CallState("c", (events.Member("extension", "e", "ANSWERED", ext="104"),
                             events.Member("extension", "x", "RING", ext="200"))), now=1.0)
    c = st.snapshot()[0]
    assert c["agents"] == ["104"] and c["ringing"] == []


def test_live_service_needs_an_explicit_team_choice():
    with pytest.raises(TypeError):
        LiveService(None, FakeRedis(), None, guard=None, base_url="https://pbx.invalid")
    svc = LiveService(None, FakeRedis(), None, guard=None, base_url="https://pbx.invalid", team=TEAM_ONLY)
    assert svc.state.team is TEAM_ONLY


def test_team_queue_waiting_call_is_shown():
    st = LiveState(team=TEAM_ONLY)
    d = st.apply(CallState("q", (events.Member("inbound", "t", "ANSWERED", party="••••1", queue="6401"),)), now=1.0)
    assert d[0]["call"]["state"] == "waiting"


def test_memory_cap_drops_the_stalest_call():
    st = LiveState(max_calls=3)
    for i in range(3):
        st.apply(CallState(f"c{i}", (events.Member("extension", f"ch{i}", "RING", ext="119"),)), now=float(i))
    d = st.apply(CallState("c3", (events.Member("extension", "ch3", "RING", ext="119"),)), now=10.0)
    assert d[0] == {"op": "remove", "call_id": "c0"} and len(st.calls) == 3


def test_publisher_writes_snapshot_then_delta_with_growing_seq():
    r = FakeRedis()
    pub = Publisher(r, clock=lambda: 1000.0)
    s1 = pub.publish([{"call_id": "a"}], [{"op": "upsert"}], "connected")
    s2 = pub.publish([], [{"op": "remove", "call_id": "a"}], "connected")
    assert s2 == s1 + 1 and s1 == 1000 * 1000 + 1
    snap = json.loads(r.data[SNAPSHOT_KEY])
    assert snap["seq"] == s2 and snap["calls"] == [] and r.ttls[SNAPSHOT_KEY] == 90 and r.data[LAST_SEEN_KEY]
    assert [m["seq"] for m in r.messages()] == [s1, s2] and r.published[0][0] == CHANNEL
    assert Publisher(r, clock=lambda: 1001.0).seq > s2           # a restarted service never reuses numbers


# ---------- back-off, login gate, token store ----------

def test_backoff_5s_to_5min_with_jitter():
    b = Backoff(rand=lambda: 0.5)
    assert [round(b.next()) for _ in range(8)] == [5, 10, 20, 40, 80, 160, 300, 300]
    b.reset()
    assert b.next() == 5
    lo, hi = Backoff(rand=lambda: 0.0), Backoff(rand=lambda: 1.0)
    assert lo.next() == pytest.approx(4.0) and hi.next() == pytest.approx(6.0)


def test_login_gate_one_login_per_10_minutes():
    r = FakeRedis()
    gate = LoginGate(r)
    gate()
    assert r.ttls["live:login_lock"] == 600
    r.ttls["live:login_lock"] = 420
    with pytest.raises(LoginRateLimited) as e:
        gate()
    assert e.value.wait == 420
    with pytest.raises(LoginRateLimited):
        LoginGate(FakeRedis(down=True))()                      # Redis down = no login


def test_token_store_reuses_tokens_younger_than_25_min(tmp_path):
    now = [1000.0]
    ts = TokenStore(tmp_path / "pbx_token.json", clock=lambda: now[0])
    ts.save({"access_token": "a", "refresh_token": "b", "age": 60.0})
    now[0] += 600
    got = ts.load()
    assert got["access_token"] == "a" and got["age"] == pytest.approx(660.0)
    now[0] += 25 * 60
    assert ts.load() is None
    assert TokenStore(None).load() is None and TokenStore(tmp_path / "missing.json").load() is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert TokenStore(tmp_path / "bad.json").load() is None


def test_token_store_is_a_private_file_not_redis(tmp_path):
    """Every container can read Redis (asr workers too): the PBX token lives in a 0600 file of the live service."""
    import os
    import stat
    f = tmp_path / "pbx_token.json"
    TokenStore(f).save({"access_token": "a", "refresh_token": "b", "age": 0.0})
    assert json.loads(f.read_text(encoding="utf-8"))["access_token"] == "a"
    if os.name == "posix":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    TokenStore(f).clear()
    assert not f.exists()


def test_ws_url_uses_https_port_and_is_never_logged(caplog):
    assert ws_url("https://pbx.invalid:8088/", "T0K") == "wss://pbx.invalid:8088/openapi/v1.0/subscribe?access_token=T0K"


# ---------- pbx:auth_blocked in every login path ----------

def adapter(http: FakePbxHttp, redis: FakeRedis, who="cli", **kw) -> YeastarAdapter:
    return YeastarAdapter("https://pbx.invalid", "id", "secret", http=http.client(), sleep=lambda s: None,
                          guard=AuthGuard(redis, who, fail_closed=kw.pop("fail_closed", False)), **kw)


def test_refused_login_sets_auth_blocked_and_stops_every_process():
    r = FakeRedis()
    http = FakePbxHttp(login_codes=[20004])
    with pytest.raises(AuthError):
        adapter(http, r, "worker").token()
    info = json.loads(r.data[AUTH_BLOCKED_KEY])
    assert info["by"] == "worker" and "20004" in info["reason"]
    for who in ("worker", "cli", "live"):                       # nobody logs in any more
        with pytest.raises(AuthBlocked, match="pbx-unblock"):
            adapter(http, r, who).token()
    assert http.logins == 1


def test_token_limit_sets_auth_blocked():
    r = FakeRedis()
    with pytest.raises(TokenLimit):
        adapter(FakePbxHttp(login_codes=[60002]), r).token()
    assert "60002" in json.loads(r.data[AUTH_BLOCKED_KEY])["reason"]


def test_first_reason_is_kept():
    r = FakeRedis()
    AuthGuard(r, "worker").block("first")
    AuthGuard(r, "cli").block("second")
    assert json.loads(r.data[AUTH_BLOCKED_KEY])["reason"] == "first"


def test_redis_down_fail_open_for_ingest_fail_closed_for_live():
    http = FakePbxHttp()
    assert adapter(http, FakeRedis(down=True), fail_closed=False).token() == "tok1"
    with pytest.raises(AuthBlocked):
        adapter(http, FakeRedis(down=True), fail_closed=True).token()
    assert http.logins == 1


def test_ingest_wiring_passes_a_guard(monkeypatch):
    from callsight import app as wiring
    from callsight.config import Settings
    monkeypatch.setattr(wiring, "settings", lambda: Settings(
        database_url="x", redis_url="redis://127.0.0.1:1/0", audio_dir=Path("."), teams_file=Path("."),
        yeastar_base_url="https://pbx.invalid", yeastar_client_id="i", yeastar_client_secret="s",
        yeastar_verify_ssl=True))
    a = wiring.pbx_adapter("worker")
    assert isinstance(a.guard, AuthGuard) and a.guard.who == "worker" and not a.guard.fail_closed


def test_pbx_unblock_cli(capsys):
    r = FakeRedis()
    assert cli._pbx_unblock(r) == 0 and "were not blocked" in capsys.readouterr().out
    AuthGuard(r, "live").block("get_token refused: errcode=20004")
    assert cli._pbx_unblock(r) == 0
    assert "allowed again" in capsys.readouterr().out and AUTH_BLOCKED_KEY not in r.data
    assert unblock(r) is None
    assert cli._pbx_unblock(FakeRedis(down=True)) == 1


def test_adapter_listen_refuses_second_gate():
    with pytest.raises(PbxError, match="switched off"):
        adapter(FakePbxHttp(), FakeRedis()).listen("114", "1731574800.101")


# ---------- live service with a fake PBX ----------

FAST = Timing(heartbeat=0.05, idle_timeout=0.3, rotate_after=25 * 60, sync_grace=10.0, prune_every=10.0,
              blocked_poll=0.05, subscribe_timeout=1.0)


def make_service(sockets, http=None, redis=None, timing=FAST, stop_after_sockets=True, **kw):
    redis = redis or FakeRedis()
    http = http or FakePbxHttp()
    guard = AuthGuard(redis, "live", fail_closed=True)
    ad = YeastarAdapter("https://pbx.invalid:8088", "id", "secret", http=http.client(), sleep=lambda s: None,
                        guard=guard, login_gate=LoginGate(redis), max_login_failures=1)
    holder = {}
    conn = FakeConnect(sockets, on_empty=(lambda: holder["svc"].stop.set()) if stop_after_sockets else None)
    sleeps = []
    kw.setdefault("team", None)        # explicit: these tests check the PBX link, not the team filter
    svc = LiveService(ad, redis, conn, guard=guard, base_url="https://pbx.invalid:8088", timing=timing,
                      backoff=Backoff(base=0.01, cap=0.02, rand=lambda: 0.5), **kw)
    holder["svc"] = svc
    return svc, conn, http, redis, sleeps


def run(svc, timeout=5.0):
    async def go():
        try:
            await asyncio.wait_for(svc.run(), timeout)
        finally:
            await svc.shutdown()
    asyncio.run(go())


def test_service_publishes_masked_state_from_events(caplog):
    caplog.set_level(logging.DEBUG)
    ws = FakeWs([json.dumps(FIXTURES["ring"]), json.dumps(FIXTURES["answer"]), "heartbeat response",
                 json.dumps(FIXTURES["end"])])
    svc, conn, http, r, _ = make_service([ws])
    run(svc)
    assert json.loads(ws.sent[0]) == {"topic_list": [30011, 30012]}
    assert conn.urls[0] == "wss://pbx.invalid:8088/openapi/v1.0/subscribe?access_token=tok1"
    assert conn.kwargs[0]["user_agent_header"] == "OpenAPI"
    msgs = r.messages()
    statuses = [m["status"] for m in msgs]
    assert statuses[0] == "disconnected" and "syncing" in statuses
    ups = [d["call"] for m in msgs for d in m["deltas"] if d["op"] == "upsert"]
    assert [c["state"] for c in ups] == ["ringing", "talking"]
    assert any(d == {"op": "remove", "call_id": "1731574800.101"} for m in msgs for d in m["deltas"])
    seqs = [m["seq"] for m in msgs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    ours = "\n".join(rec.getMessage() for rec in caplog.records if rec.name.startswith("callsight"))
    everything = json.dumps(r.published) + json.dumps(r.data, default=str) + caplog.text
    assert RAW_CALLER not in everything and "551234567" not in everything      # masked before Redis and logs
    assert "access_token=" not in ours and "tok1" not in ours                  # URL / token never logged by us
    assert http.calls.count("del_token") == 1                                  # clean stop frees the token
    assert SNAPSHOT_KEY not in r.data and not any("token" in k for k in r.data)   # no PBX token in Redis


def test_heartbeat_sent_and_dead_connection_detected():
    ws = FakeWs([0.12, "heartbeat response", 0.5])        # stalls: heartbeats go out; then > idle_timeout silence
    svc, conn, http, r, _ = make_service([ws])
    run(svc)
    assert ws.sent.count("heartbeat") >= 2
    assert len(conn.urls) == 2                              # reconnected (second attempt hits on_empty -> stop)
    assert http.logins == 1                                 # reconnect reuses the token: no new login


def test_reconnect_reuses_token_and_clears_state():
    ws1 = FakeWs([json.dumps(FIXTURES["ring"]), WsClosed("boom")])
    ws2 = FakeWs([])
    svc, conn, http, r, _ = make_service([ws1, ws2])
    run(svc)
    assert http.logins == 1 and len(conn.urls) == 3
    resets = [m for m in r.messages() if {"op": "reset"} in m["deltas"]]
    assert len(resets) >= 2                                 # every (re)connect starts empty ("syncing")


def test_pbx_unreachable_backs_off_without_new_logins():
    svc, conn, http, r, _ = make_service([OSError("refused"), OSError("refused"), OSError("refused")])
    run(svc)
    assert http.logins == 1 and len(conn.urls) == 4 and svc.backoff.attempt >= 3
    assert "disconnected" in [m["status"] for m in r.messages()]


def test_failed_login_is_never_retried():
    r = FakeRedis()
    http = FakePbxHttp(login_codes=[20004, 0, 0])
    svc, conn, *_ = make_service([], http=http, redis=r, stop_after_sockets=False)

    async def go():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.4)                        # several blocked_poll rounds
        svc.stop.set()
        await task
    asyncio.run(go())
    assert http.logins == 1 and conn.urls == []
    assert AUTH_BLOCKED_KEY in r.data and r.messages()[-1]["status"] == "auth_blocked"


def test_blocked_key_stops_the_live_login_until_pbx_unblock():
    r = FakeRedis()
    AuthGuard(r, "worker").block("get_token refused: errcode=20004")
    http = FakePbxHttp()
    svc, conn, *_ = make_service([FakeWs([])], http=http, redis=r)

    async def go():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.2)
        assert http.logins == 0 and conn.urls == []
        unblock(r)                                     # a person ran pbx-unblock
        await asyncio.wait_for(task, 3)
        await svc.shutdown()
    asyncio.run(go())
    assert http.logins == 1


def test_login_rate_limit_one_per_10_minutes():
    r = FakeRedis()
    r.set("live:login_lock", "1", ex=600)              # another start logged in 3 min ago
    r.ttls["live:login_lock"] = 0.05
    http = FakePbxHttp()
    svc, conn, *_ = make_service([], http=http, redis=r, stop_after_sockets=False)

    async def go():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.3)
        svc.stop.set()
        await task
    asyncio.run(go())
    assert http.logins == 0 and conn.urls == []


def test_restart_reuses_saved_token_without_login(tmp_path):
    r = FakeRedis()
    TokenStore(tmp_path / "t.json").save({"access_token": "saved", "refresh_token": "ref", "age": 120.0})
    http = FakePbxHttp()
    svc, conn, *_ = make_service([FakeWs([])], http=http, redis=r, token_file=tmp_path / "t.json")
    run(svc)
    assert http.logins == 0 and conn.urls[0].endswith("access_token=saved")


def test_token_rotation_refreshes_without_new_login():
    timing = Timing(heartbeat=0.05, idle_timeout=5, rotate_after=0.15, sync_grace=10, prune_every=10,
                    blocked_poll=0.05)
    svc, conn, http, r, _ = make_service([FakeWs([3.0]), FakeWs([1.0])], timing=timing)   # rotation waits >= 1 s
    run(svc)
    assert http.logins == 1 and http.calls.count("refresh_token") >= 1
    assert conn.urls[0].endswith("tok1") and conn.urls[1].endswith("tok2")


def test_subscribe_refused_token_drops_it_and_next_login_waits_for_the_gate(tmp_path):
    bad = FakeWs([], subscribe_reply='{"errcode":10004,"errmsg":"INVALID TOKEN"}')
    r = FakeRedis()
    svc, conn, http, *_ = make_service([bad, FakeWs([])], redis=r, stop_after_sockets=False,
                                       token_file=tmp_path / "t.json")

    async def go():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.3)
        svc.stop.set()
        await task
    asyncio.run(go())
    assert http.logins == 1 and len(conn.urls) == 1           # new login only after the 10-minute gate
    assert svc.adapter.access_token is None and not (tmp_path / "t.json").exists()


def test_main_does_nothing_unless_enabled(capsys, monkeypatch):
    def boom():
        raise AssertionError("must not build the service (no PBX contact)")
    assert main(enabled=lambda: False, build=boom) == 0
    assert "off" in capsys.readouterr().out
    for noisy in ("httpx", "httpcore", "websockets"):     # their INFO/DEBUG lines carry ?access_token= URLs
        assert logging.getLogger(noisy).level == logging.WARNING
    monkeypatch.delenv("LIVE_ENABLED", raising=False)
    assert live_service.env_true("LIVE_ENABLED") is False
    monkeypatch.setenv("LIVE_ENABLED", "true")
    assert live_service.env_true("LIVE_ENABLED") is True


def test_cli_live_exits_0_when_off(monkeypatch):
    monkeypatch.delenv("LIVE_ENABLED", raising=False)
    monkeypatch.setattr(live_service, "build_service", lambda: (_ for _ in ()).throw(AssertionError("PBX!")))
    assert cli.main(["live"]) == 0


# ---------- compose: default `up -d` starts no live service ----------

def test_compose_live_service_is_off_by_default():
    spec = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    live = spec["services"]["live"]
    assert live["profiles"] == ["live"]
    assert live["restart"].startswith("on-failure:")
    assert "${LIVE_ENABLED:-false}" in str(live["environment"]["LIVE_ENABLED"])
    assert "live" in live["command"]
    defaults = [n for n, s in spec["services"].items() if not s.get("profiles")]
    assert "live" not in defaults
    # the API gets the Origin allowlist + the Listen switch (default off)
    api_env = spec["services"]["api"]["environment"]
    assert "${LISTEN_ENABLED:-false}" in str(api_env["LISTEN_ENABLED"])


# ---------- review fixes ----------

def test_stop_during_an_open_session_runs_shutdown_at_once():
    """SIGTERM sets stop; a recv() waiting up to the heartbeat must not delay del_token past Docker's grace."""
    timing = Timing(heartbeat=30, idle_timeout=60, rotate_after=25 * 60, sync_grace=10, prune_every=30,
                    blocked_poll=0.05)
    svc, conn, http, r, _ = make_service([FakeWs([20.0])], timing=timing, stop_after_sockets=False)

    async def go():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.2)                       # connected, waiting in recv()
        assert len(conn.urls) == 1
        t0 = asyncio.get_running_loop().time()
        svc.stop.set()
        await asyncio.wait_for(task, 2)
        await svc.shutdown()
        return asyncio.get_running_loop().time() - t0
    took = asyncio.run(go())
    assert took < 1.0 and http.calls.count("del_token") == 1


def test_waits_republish_so_the_snapshot_does_not_expire():
    timing = Timing(heartbeat=0.05, idle_timeout=0.3, rotate_after=25 * 60, sync_grace=10, prune_every=10,
                    blocked_poll=0.05, republish_every=0.05)
    r = FakeRedis()
    r.set("live:login_lock", "1", ex=600)
    r.ttls["live:login_lock"] = 1                      # a 1 s login wait (real: up to 10 min > the 90 s TTL)
    svc, conn, *_ = make_service([], redis=r, timing=timing, stop_after_sockets=False)

    async def go():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.4)
        svc.stop.set()
        await task
    asyncio.run(go())
    assert len(r.messages()) >= 4                      # first publish + re-publishes during the wait


def test_refused_login_sets_the_block_key_again_if_redis_missed_it():
    """A refused login while Redis hiccuped: the key was never written. Setting it later keeps pbx-unblock useful."""
    r = FakeRedis()
    http = FakePbxHttp(login_codes=[20004])
    svc, conn, *_ = make_service([], http=http, redis=r, stop_after_sockets=False)
    real_block = svc.guard.block
    calls = []

    def flaky_block(reason):
        calls.append(reason)
        if len(calls) > 1:
            real_block(reason)                         # the first SET is lost (Redis hiccup)
    svc.guard.block = flaky_block
    svc.adapter.guard = svc.guard

    async def go():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.3)
        svc.stop.set()
        await task
    asyncio.run(go())
    assert http.logins == 1 and AUTH_BLOCKED_KEY in r.data and len(calls) >= 2


def test_queue_from_path_never_takes_part_of_a_long_number():
    assert events._queue_from_path("queue from 0551234567 to 6401") is None
    assert events._queue_from_path("queue 6401") == "6401"
