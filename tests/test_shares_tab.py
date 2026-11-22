"""Shared calls tab (spec 2026-11-22-shares-tab): open / archive per side, agent closes only after replying, the
reply locks once closed, 24 h reply rule and its KPI."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from callsight.config import RIYADH
from callsight.db.models import AppSetting, CallShare
from test_agent_supervisor_api import env, login  # noqa: F401  (env is a fixture)
from test_api import engine  # noqa: F401


def _share(client, ids, note="استمع"):
    login(client, "boss")
    return client.post(f"/api/calls/{ids['other']}/shares", json={"to_user_id": ids["ali"], "note": note}).json()["id"]


def _age(Session, sid, hours):
    """Pretend the share was sent `hours` ago."""
    with Session() as s:
        s.execute(update(CallShare).where(CallShare.id == sid)
                  .values(created_at=datetime.now(timezone.utc) - timedelta(hours=hours)))
        s.commit()


def test_agent_closes_only_after_reply_then_reply_is_locked(env):  # noqa: F811
    client, _, ids, *_ = env
    sid = _share(client, ids)
    login(client, "ali")
    d = client.get("/api/shares").json()
    assert d["open"] == 1 and d["items"][0]["reply_state"] == "pending" and d["items"][0]["closed"] is False
    assert client.post(f"/api/shares/{sid}/close").status_code == 409          # write the reply first
    client.post(f"/api/shares/{sid}/seen")
    assert client.put(f"/api/shares/{sid}/reply", json={"text": "تم"}).json()["reply_state"] == "on_time"
    r = client.post(f"/api/shares/{sid}/close")
    assert r.status_code == 200 and r.json()["closed"] is True and r.json()["agent_closed_at"]
    assert client.get("/api/shares").json()["items"] == []                    # gone from the open list
    arch = client.get("/api/shares", params={"box": "archive"}).json()["items"]
    assert [a["id"] for a in arch] == [sid]
    assert client.put(f"/api/shares/{sid}/reply", json={"text": "تعديل"}).status_code == 409   # locked
    assert client.get(f"/api/calls/{ids['other']}").status_code == 200         # the call still opens from the archive
    login(client, "boss")       # the supervisor's list is separate: still open for them
    d = client.get("/api/shares").json()
    assert d["open"] == 1 and d["items"][0]["agent_closed_at"] and d["items"][0]["closed"] is False


def test_staff_close_and_archive_filters(env):  # noqa: F811
    client, Session, ids, *_ = env
    old, new = _share(client, ids, "قديمة"), _share(client, ids, "جديدة")
    _age(Session, old, 24 * 10)
    login(client, "boss")
    assert client.post(f"/api/shares/{old}/close").status_code == 200          # staff: any time, no reply needed
    assert client.post(f"/api/shares/{new}/close").status_code == 200
    assert client.get("/api/shares").json()["open"] == 0
    today = datetime.now(RIYADH).date()
    def arch(**p):
        return [i["id"] for i in client.get("/api/shares", params={"box": "archive", **p}).json()["items"]]
    assert set(arch()) == {old, new}
    assert arch(**{"from": (today - timedelta(days=1)).isoformat()}) == [new]
    assert arch(to=(today - timedelta(days=5)).isoformat()) == [old]
    assert arch(agent="119") and arch(agent="104") == []                         # one agent / all agents
    assert client.get("/api/shares", params={"box": "archive", "from": today.isoformat(),
                                               "to": (today - timedelta(days=3)).isoformat()}).status_code == 422
    login(client, "ali")        # the agent's list is untouched by the supervisor's close
    assert client.get("/api/shares").json()["open"] == 2
    login(client, "sara")
    assert client.post(f"/api/shares/{new}/close").status_code == 404           # not hers


def test_reply_states_and_kpi(env):  # noqa: F811
    client, Session, ids, *_ = env
    on_time, late, missing, pending = (_share(client, ids, n) for n in ("a", "b", "c", "d"))
    login(client, "ali")
    for sid in (on_time, late):
        client.post(f"/api/shares/{sid}/seen")
        client.put(f"/api/shares/{sid}/reply", json={"text": "رد"})
    with Session() as s:        # `late`: sent 30 h ago, replied now; `missing`: sent 25 h ago, no reply
        now = datetime.now(timezone.utc)
        s.execute(update(CallShare).where(CallShare.id == late).values(created_at=now - timedelta(hours=30)))
        s.execute(update(CallShare).where(CallShare.id == missing).values(created_at=now - timedelta(hours=25)))
        s.commit()
    states = {i["id"]: i["reply_state"] for i in client.get("/api/shares").json()["items"]}
    assert states == {on_time: "on_time", late: "late", missing: "missing", pending: "pending"}
    client.put(f"/api/shares/{on_time}/reply", json={"text": "رد معدّل"})       # an edit keeps the first time
    assert {i["id"]: i["reply_state"] for i in client.get("/api/shares").json()["items"]}[on_time] == "on_time"
    login(client, "boss")
    week = {"date_from": (now - timedelta(days=3)).astimezone(RIYADH).date().isoformat(),
            "date_to": now.astimezone(RIYADH).date().isoformat()}
    k = client.get("/api/kpi", params={**week, "agent": "119"}).json()["shares"]
    assert k["agents"]["119"] == {"due": 3, "on_time": 1, "late": 1, "missing": 1, "rate": 1 / 3}
    with Session() as s:        # shares sent before the rule existed never count
        s.add(AppSetting(key="share_kpi_since", value=now.astimezone(RIYADH).date().isoformat()))
        s.commit()
    k = client.get("/api/kpi", params={**week, "agent": "119"}).json()["shares"]
    assert k["agents"]["119"]["due"] <= 1 and k["since"] == now.astimezone(RIYADH).date().isoformat()


def test_removing_the_reply_restarts_the_first_reply_time(env):  # noqa: F811
    client, Session, ids, *_ = env
    sid = _share(client, ids)
    login(client, "ali")
    client.post(f"/api/shares/{sid}/seen")
    client.put(f"/api/shares/{sid}/reply", json={"text": "رد"})
    assert client.put(f"/api/shares/{sid}/reply", json={"text": ""}).json()["first_reply_at"] is None
    assert client.post(f"/api/shares/{sid}/close").status_code == 409
