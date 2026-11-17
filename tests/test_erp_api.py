"""Spec 2026-11-18-erp-api: another system calls the API with a key + the CallSight user it acts for (X-Act-As).
The answer must be exactly what that user would get when logged in, and audits must name that user."""
from sqlalchemy import select

from callsight.api import auth
from callsight.db.models import AdminLog, ApiClient, AppUser, RecordingPlay
from test_agent_supervisor_api import env, login  # noqa: F401  (env is a fixture)
from test_api import engine  # noqa: F401  (fixture)


def make_key(client) -> str:
    login(client, "root")
    r = client.post("/api/admin/api-clients", json={"name": "ERP"})
    assert r.status_code == 200
    key = r.json()["key"]
    client.post("/api/logout")
    client.cookies.clear()
    return key


def h(key: str, user: str) -> dict:
    return {auth.API_KEY_HEADER: key, auth.ACT_AS_HEADER: user}


def test_key_is_shown_once_and_only_its_hash_is_kept(env):
    client, Session, *_ = env
    key = make_key(client)
    assert key.startswith("cs_") and len(key.split("_")) == 3
    login(client, "root")
    listed = client.get("/api/admin/api-clients").json()
    assert [c["name"] for c in listed] == ["ERP"] and "key" not in listed[0] and "key_hash" not in listed[0]
    assert key.split("_")[1] == listed[0]["prefix"]
    with Session() as s:
        row = s.scalars(select(ApiClient)).one()
        assert key not in (row.key_hash, row.key_prefix)
        assert s.scalar(select(AdminLog.action).where(AdminLog.action == "create_api_key")) == "create_api_key"


def test_acts_with_the_named_users_rights(env):
    client, _, ids, *_ = env
    key = make_key(client)
    assert client.get("/api/me", headers=h(key, "ali")).json() == {"username": "ali", "role": "agent", "ext": "119"}
    agent_calls = client.get("/api/calls", headers=h(key, "ali")).json()
    assert {c["id"] for c in agent_calls["items"]} == {ids["own"]}          # an agent: own calls only
    boss_calls = client.get("/api/calls", headers=h(key, "boss")).json()
    assert {c["id"] for c in boss_calls["items"]} == {ids["own"], ids["other"], ids["sales"]}
    assert client.get("/api/admin/users", headers=h(key, "boss")).status_code == 403   # supervisor is no admin
    assert client.get("/api/admin/users", headers=h(key, "root")).status_code == 200
    assert client.get("/api/me", headers=h(key, "ALI ")).json()["username"] == "ali"   # same rule as the login


def test_refused_without_a_good_key_and_an_active_user(env):
    client, Session, *_ = env
    key = make_key(client)
    bad = key[:-1] + ("0" if key[-1] != "0" else "1")
    for headers in (h(bad, "ali"), h(key, ""), {auth.API_KEY_HEADER: key}, h(key, "nobody"), h("cs_x_y", "ali"),
                    h("", "ali")):
        assert client.get("/api/me", headers=headers).status_code == 401
    with Session() as s:
        auth.disable_user(s, "ali")
    assert client.get("/api/me", headers=h(key, "ali")).status_code == 401


def test_key_never_mixes_with_a_cookie(env):
    client, *_ = env
    key = make_key(client)
    login(client, "root")                                        # cookie of the admin ...
    assert client.get("/api/me", headers=h(key, "ali")).json()["username"] == "ali"   # ... the key decides
    assert client.get("/api/me", headers=h("cs_bad", "ali")).status_code == 401       # no fallback to the cookie


def test_disabled_key_stops_at_once_and_keys_cannot_manage_keys(env):
    client, Session, *_ = env
    key = make_key(client)
    assert client.post("/api/admin/api-clients", json={"name": "x"}, headers=h(key, "root")).status_code == 403
    with Session() as s:
        cid = s.scalars(select(ApiClient.id)).one()
    assert client.post(f"/api/admin/api-clients/{cid}/disable", headers=h(key, "root")).status_code == 403
    login(client, "root")
    assert client.post(f"/api/admin/api-clients/{cid}/disable").json()["active"] is False
    client.post("/api/logout")
    client.cookies.clear()
    assert client.get("/api/me", headers=h(key, "root")).status_code == 401


def test_audit_rows_name_the_acting_user(env):
    client, Session, ids, *_ = env
    key = make_key(client)
    assert client.get(f"/api/calls/{ids['own']}/recording", headers=h(key, "boss")).status_code == 200
    with Session() as s:
        boss = s.scalars(select(AppUser.id).where(AppUser.username == "boss")).one()
        assert s.scalar(select(RecordingPlay.user_id).where(RecordingPlay.call_id == ids["own"])) == boss
        assert s.scalars(select(ApiClient)).one().last_used_at is not None


def test_writes_work_from_a_server_without_origin(env):
    client, _, ids, *_ = env
    key = make_key(client)
    r = client.put(f"/api/calls/{ids['own']}/my-note", json={"text": "ملاحظة"}, headers=h(key, "boss"))
    assert r.status_code in (200, 204), r.text
    # a browser page of another site is still refused (Origin rule unchanged)
    r = client.put(f"/api/calls/{ids['own']}/my-note", json={"text": "x"},
                   headers={**h(key, "boss"), "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_live_snapshot_for_staff_only(env):
    client, *_ = env
    key = make_key(client)
    r = client.get("/api/live/snapshot", headers=h(key, "boss"))
    assert r.status_code == 200 and r.json()["available"] is False and r.json()["calls"] == []
    assert client.get("/api/live/snapshot", headers=h(key, "ali")).status_code == 403
    assert client.get("/api/live/snapshot").status_code == 401
