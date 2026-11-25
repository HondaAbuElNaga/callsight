"""Shared calls as a chat (spec 2026-11-25-share-chat): messages both ways, unread marks, staff archive / reopen /
close, the agent sees only open chats, the 24 h reply rule and its KPI."""
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


def _say(client, sid, text):
    return client.post(f"/api/chats/{sid}/messages", json={"text": text})


def test_chat_both_ways_with_unread(env):  # noqa: F811
    client, _, ids, *_ = env
    sid = _share(client, ids)
    assert client.get("/api/chats").json()["unread"] == 0          # the sender has read their own note
    login(client, "ali")
    d = client.get("/api/chats").json()
    assert d["open"] == 1 and d["unread"] == 1 and d["items"][0]["last"]["text"] == "استمع"
    chat = client.get(f"/api/chats/{sid}").json()
    assert chat["seen_at"] and chat["messages"][0]["agent"] is False and chat["messages"][0]["mine"] is False
    assert client.get("/api/chats").json()["unread"] == 0          # read now
    r = _say(client, sid, " سأراجع المكالمة ")
    m = r.json()["messages"][-1]
    assert r.status_code == 200 and (m["text"], m["agent"], m["mine"]) == ("سأراجع المكالمة", True, True)
    assert _say(client, sid, "   ").status_code == 422
    login(client, "boss")
    d = client.get("/api/chats").json()
    assert d["unread"] == 1 and d["items"][0]["last"]["agent"] is True
    client.get(f"/api/chats/{sid}")
    assert client.get("/api/chats").json()["unread"] == 0
    assert _say(client, sid, "شكراً").status_code == 200
    login(client, "ali")
    assert client.get("/api/chats").json()["unread"] == 1
    login(client, "sara")
    assert client.get(f"/api/chats/{sid}").status_code == 404 and _say(client, sid, "x").status_code == 404


def test_archive_reopen_and_close(env):  # noqa: F811
    client, _, ids, *_ = env
    sid = _share(client, ids)
    login(client, "ali")
    assert client.post(f"/api/chats/{sid}/archive").status_code == 403          # agents can't
    login(client, "boss")
    assert client.post(f"/api/chats/{sid}/archive").json()["status"] == "archived"
    assert client.get("/api/chats").json()["items"] == []
    assert [i["id"] for i in client.get("/api/chats", params={"box": "archive"}).json()["items"]] == [sid]
    login(client, "ali")                                                          # gone for the agent too
    assert client.get("/api/chats").json()["items"] == [] and client.get(f"/api/chats/{sid}").status_code == 404
    assert client.get(f"/api/calls/{ids['other']}").status_code == 404           # ... and so is the call
    assert client.get(f"/api/calls/{ids['other']}/recording").status_code == 404
    assert client.get("/api/chats", params={"box": "archive"}).json()["items"] == []
    assert _say(client, sid, "رسالة بعد الأرشفة").status_code == 404
    login(client, "boss")
    assert client.post(f"/api/chats/{sid}/reopen").json()["status"] == "open"
    login(client, "ali")                                                          # pops up for the agent again
    assert client.get("/api/chats").json()["unread"] == 1
    login(client, "boss")
    assert client.post(f"/api/chats/{sid}/reopen").status_code == 409
    client.post(f"/api/chats/{sid}/archive")
    assert _say(client, sid, "عدنا للموضوع").json()["status"] == "open"        # writing reopens it
    login(client, "ali")
    assert client.get("/api/chats").json()["unread"] == 1
    login(client, "root")                                                         # admin: same chats
    assert client.post(f"/api/chats/{sid}/close").json()["status"] == "closed"
    for who in ("boss", "root", "ali"):                                           # gone everywhere
        login(client, who)
        assert client.get("/api/chats").json()["items"] == []
        assert client.get("/api/chats", params={"box": "archive"}).json()["items"] == []
        assert client.get(f"/api/chats/{sid}").status_code == 404
        r = client.get(f"/api/calls/{ids['other']}/shares")
        assert r.status_code == 404 if who == "ali" else r.json()["items"] == []   # agent: the call is gone too
    login(client, "boss")
    assert client.post(f"/api/chats/{sid}/reopen").status_code == 404


def test_archive_filters(env):  # noqa: F811
    client, Session, ids, *_ = env
    old, new = _share(client, ids, "قديمة"), _share(client, ids, "جديدة")
    _age(Session, old, 24 * 10)
    login(client, "boss")
    client.post(f"/api/chats/{old}/archive")
    client.post(f"/api/chats/{new}/archive")
    today = datetime.now(RIYADH).date()

    def arch(**p):
        return [i["id"] for i in client.get("/api/chats", params={"box": "archive", **p}).json()["items"]]
    assert set(arch()) == {old, new}
    assert arch(**{"from": (today - timedelta(days=1)).isoformat()}) == [new]
    assert arch(to=(today - timedelta(days=5)).isoformat()) == [old]
    assert arch(agent="119") and arch(agent="104") == []
    assert client.get("/api/chats", params={"box": "archive", "from": today.isoformat(),
                                              "to": (today - timedelta(days=3)).isoformat()}).status_code == 422


def test_reply_states_and_kpi_closed_still_counts(env):  # noqa: F811
    client, Session, ids, *_ = env
    on_time, late, missing, pending = (_share(client, ids, n) for n in ("a", "b", "c", "d"))
    login(client, "ali")
    assert _say(client, on_time, "تم").json()["first_reply_at"] is None          # too short: not the 24 h reply
    for sid in (on_time, late):
        _say(client, sid, "سأتصل بالعميل اليوم")
    first = client.get(f"/api/chats/{on_time}").json()["first_reply_at"]
    _say(client, on_time, "رسالة ثانية بعد ذلك")
    assert client.get(f"/api/chats/{on_time}").json()["first_reply_at"] == first   # later messages keep it
    with Session() as s:        # `late`: sent 30 h ago, replied now; `missing`: sent 25 h ago, no reply
        now = datetime.now(timezone.utc)
        s.execute(update(CallShare).where(CallShare.id == late).values(created_at=now - timedelta(hours=30)))
        s.execute(update(CallShare).where(CallShare.id == missing).values(created_at=now - timedelta(hours=25)))
        s.commit()
    states = {i["id"]: i["reply_state"] for i in client.get("/api/chats").json()["items"]}
    assert states == {on_time: "on_time", late: "late", missing: "missing", pending: "pending"}
    login(client, "boss")
    for sid in (on_time, late, missing):          # closed chats still count in the KPI
        client.post(f"/api/chats/{sid}/close")
    week = {"date_from": (now - timedelta(days=3)).astimezone(RIYADH).date().isoformat(),
            "date_to": now.astimezone(RIYADH).date().isoformat()}
    k = client.get("/api/kpi", params={**week, "agent": "119"}).json()["shares"]
    assert k["agents"]["119"] == {"due": 3, "on_time": 1, "late": 1, "missing": 1, "rate": 1 / 3}
    with Session() as s:        # shares sent before the rule existed never count
        s.add(AppSetting(key="share_kpi_since", value=now.astimezone(RIYADH).date().isoformat()))
        s.commit()
    k = client.get("/api/kpi", params={**week, "agent": "119"}).json()["shares"]
    assert k["agents"]["119"]["due"] <= 1 and k["since"] == now.astimezone(RIYADH).date().isoformat()


def test_notes_sent_before_the_rule_are_old_not_red(env):  # noqa: F811
    client, Session, ids, *_ = env
    sid = _share(client, ids)
    _age(Session, sid, 48)
    with Session() as s:
        s.add(AppSetting(key="share_kpi_since",
                         value=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")))
        s.commit()
    login(client, "ali")
    assert client.get("/api/chats").json()["items"][0]["reply_state"] == "old"
