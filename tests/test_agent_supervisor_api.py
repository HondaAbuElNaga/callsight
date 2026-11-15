"""API of the agent-supervisor spec: follow-up form + lock rules, follow-ups list scope, shares, agent summary,
admin users / settings / log / delete call content."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from callsight.api import auth
from callsight.api.main import ApiConfig, create_app
from callsight.config import RIYADH
from callsight.db.models import (AdminLog, AppUser, Call, CallAnalysis, CallFollowup, CallShare, GoldClip,
                                 TranscriptAccess, Transcript)
from test_api import PASSWORD, SECRET, TEAM, engine, wav_bytes  # noqa: F401  (engine is a fixture)


def _now_riyadh() -> datetime:
    return datetime.now(RIYADH)


@pytest.fixture
def env(engine, tmp_path):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("SET LOCAL callsight.audit_maintenance = 'on'"))
        c.execute(text("TRUNCATE call_followup, call_share, app_setting, admin_log, transcript_access, call_analysis, "
                       "transcript, gold_clip, listen_audit, recording_play, app_user, call_leg, call, agent "
                       "RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "own.wav").write_bytes(wav_bytes())
    (tmp_path / "goldset").mkdir()
    (tmp_path / "goldset" / "own.wav").write_bytes(wav_bytes())
    sent: list = []
    start = _now_riyadh() - timedelta(minutes=30)
    with Session() as s:
        def call(uid, agent, team="customer_service", rec=None, number="0501234567"):
            c = Call(uid=uid, direction="inbound", entry="queue", customer_number=number, team=team, agent_ext=agent,
                     start_time=start, end_time=start + timedelta(minutes=3), talk_seconds=100, total_seconds=180,
                     status="answered", recording_status="ok" if rec else "none", recording_path=rec)
            s.add(c)
            s.flush()
            return c
        own, other, sales = call("own", "119", rec="r/own.wav"), call("other", "104"), call("sales", "116", "after_sales")
        s.add(Transcript(call_id=own.id, model="whisper-large-v3", status="done", text="t"))
        s.add(CallAnalysis(call_id=own.id, model="qwen2.5:7b", prompt_version="x", status="done",
                           summary="العميل يسأل", topic=None, action_items=["اتصل"], llm_score=3, reason="سبب"))
        s.add(GoldClip(call_id=own.id, start_seconds=0, seconds=60, file="goldset/own.wav"))
        s.commit()
        ids = {"own": own.id, "other": other.id, "sales": sales.id}
        auth.create_user(s, "root", PASSWORD, "admin")
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
        ali = auth.create_user(s, "ali", PASSWORD, "agent", "119", TEAM.ext_team)
        auth.create_user(s, "sara", PASSWORD, "agent", "104", TEAM.ext_team)
        auth.create_user(s, "omar", PASSWORD, "agent", "116", TEAM.ext_team)
        ids["ali"] = ali.id
    app = create_app(Session, ApiConfig(secret=SECRET, audio_dir=tmp_path, teams=lambda: TEAM, reports_dir=tmp_path,
                                        topics=lambda: {}, delete_backup_copy=lambda cid, rel: sent.append((cid, rel))))
    return TestClient(app), Session, ids, sent, tmp_path


def login(client, user):
    client.post("/api/logout")
    assert client.post("/api/login", json={"username": user, "password": PASSWORD}).status_code == 200


def switch_on(client):
    login(client, "root")
    day = (_now_riyadh() - timedelta(minutes=30)).date().isoformat()     # the fixture calls' day (also at 00:10)
    assert client.put("/api/admin/settings", json={"followup_start": day}).status_code == 200


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def not_solved(boss_id, minutes=60) -> dict:
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return {"what_happened": "طلب استرجاع", "solved": False, "responsible_kind": "user",
            "responsible_user_id": boss_id, "expected_at": iso(t + timedelta(hours=1)), "callback_at": iso(t)}


def boss_id(Session) -> int:
    with Session() as s:
        return s.scalars(select(AppUser.id).where(AppUser.username == "boss")).one()


# ---------- follow-up form ----------

def test_form_off_until_admin_sets_start(env):
    client, _, ids, *_ = env
    login(client, "ali")
    assert client.get(f"/api/calls/{ids['own']}/followup").json()["needed"] is False
    assert client.put(f"/api/calls/{ids['own']}/followup", json={"no_followup": True}).status_code == 409


def test_agent_fills_own_form(env):
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    r = client.put(f"/api/calls/{ids['own']}/followup", json=not_solved(boss_id(Session)))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needed"] and d["form"]["solved"] is False and d["callback"]["status"] == "waiting"
    assert {o["username"] for o in d["options"]} == {"boss", "ali", "sara", "omar"}     # admin is not a choice
    with Session() as s:
        f = s.scalars(select(CallFollowup)).one()
        assert f.owner_ext == "119" and f.filled_by == ids["ali"]


def test_agent_cannot_fill_colleagues_form(env):
    client, _, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    assert client.put(f"/api/calls/{ids['other']}/followup", json={"no_followup": True}).status_code == 404
    assert client.get(f"/api/calls/{ids['other']}/followup").status_code == 404


def test_bad_form_is_422(env):
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    bad = {**not_solved(boss_id(Session)), "callback_at": iso(datetime.now(timezone.utc) - timedelta(minutes=5))}
    assert client.put(f"/api/calls/{ids['own']}/followup", json=bad).status_code == 422
    assert client.put(f"/api/calls/{ids['own']}/followup", json={"solved": "yes"}).status_code == 422
    assert client.put(f"/api/calls/{ids['own']}/followup", json={"x": 1}).status_code == 422


def test_agent_locked_after_callback_time_supervisor_not(env):
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    assert client.put(f"/api/calls/{ids['own']}/followup", json=not_solved(boss_id(Session))).status_code == 200
    with Session() as s:                          # the callback time has passed
        f = s.scalars(select(CallFollowup)).one()
        f.callback_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.commit()
    r = client.put(f"/api/calls/{ids['own']}/followup", json=not_solved(boss_id(Session), 120))
    assert r.status_code == 403
    assert client.get(f"/api/calls/{ids['own']}/followup").json()["can_edit"] is False
    login(client, "boss")
    assert client.put(f"/api/calls/{ids['own']}/followup", json=not_solved(boss_id(Session), 120)).status_code == 200


def test_staff_may_keep_a_past_callback_time_when_editing(env):
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "boss")
    body = not_solved(boss_id(Session))
    assert client.put(f"/api/calls/{ids['own']}/followup", json=body).status_code == 200
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    with Session() as s:
        f = s.scalars(select(CallFollowup)).one()
        f.callback_at = past
        s.commit()
    r = client.put(f"/api/calls/{ids['own']}/followup", json={**body, "what_happened": "تعديل", "callback_at": iso(past)})
    assert r.status_code == 200, r.text


def test_followups_list_scope(env):
    client, _, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    items = client.get("/api/followups").json()["items"]
    assert [i["call_id"] for i in items] == [ids["own"]]
    assert client.get("/api/followups", params={"agent": "104"}).json()["items"][0]["call_id"] == ids["own"]
    login(client, "boss")
    assert {i["call_id"] for i in client.get("/api/followups").json()["items"]} == set(
        [ids["own"], ids["other"], ids["sales"]])
    assert [i["call_id"] for i in client.get("/api/followups", params={"agent": "104"}).json()["items"]] == [ids["other"]]
    assert client.get("/api/followups", params={"state": "bad"}).status_code == 422


def test_kpi_has_followup_block(env):
    client, _, _, *_ = env
    switch_on(client)
    login(client, "ali")
    k = client.get("/api/kpi").json()["followup"]
    assert k["enabled"] is True and k["active"] is False and set(k["agents"]) == {"119"}


# ---------- agent summary (decision 1) ----------

def test_agent_summary_only_and_audited(env):
    client, Session, ids, *_ = env
    login(client, "ali")
    d = client.get(f"/api/calls/{ids['own']}/summary").json()["analysis"]
    assert d["summary"] == "العميل يسأل" and d["action_items"] == ["اتصل"]
    assert "llm_score" not in d and "reason" not in d and "estimate" not in d
    assert client.get(f"/api/calls/{ids['own']}/analysis").status_code == 403
    assert client.get(f"/api/calls/{ids['other']}/summary").status_code == 404
    with Session() as s:
        assert s.scalars(select(TranscriptAccess.kind).where(TranscriptAccess.user_id == ids["ali"])).all() == [
            "analysis"]


# ---------- shares (decision 10) ----------

def test_share_flow(env):
    client, Session, ids, *_ = env
    login(client, "boss")
    targets = client.get(f"/api/calls/{ids['other']}/shares").json()["targets"]
    assert {t["username"] for t in targets} == {"ali", "sara"}             # same team only, no after-sales agent
    login(client, "ali")
    assert client.get(f"/api/calls/{ids['other']}").status_code == 404     # not shared yet
    login(client, "boss")
    r = client.post(f"/api/calls/{ids['other']}/shares", json={"to_user_id": ids["ali"], "note": "استمع للترحيب"})
    assert r.status_code == 200
    share_id = r.json()["id"]
    login(client, "ali")
    assert client.get(f"/api/calls/{ids['other']}").status_code == 200
    assert client.get(f"/api/calls/{ids['other']}/summary").status_code == 200
    calls = client.get("/api/calls").json()["items"]
    assert ids["other"] not in {c["id"] for c in calls}                    # never in the agent's call list
    assert client.get(f"/api/calls/{ids['other']}/followup").json()["needed"] is False
    assert client.put(f"/api/calls/{ids['other']}/followup", json={"no_followup": True}).status_code in (404, 409)
    lst = client.get("/api/shares").json()
    assert lst["unseen"] == 1 and lst["items"][0]["note"] == "استمع للترحيب"
    assert client.post(f"/api/shares/{share_id}/seen").json()["seen_at"]
    login(client, "sara")
    assert client.get("/api/shares").json()["items"] == []
    assert client.post(f"/api/shares/{share_id}/seen").status_code == 404
    assert client.get(f"/api/calls/{ids['other']}/shares").json()["items"] == []
    login(client, "boss")
    assert client.delete(f"/api/shares/{share_id}").status_code == 200
    login(client, "ali")
    assert client.get(f"/api/calls/{ids['other']}").status_code == 404


def test_share_refuses_other_team_and_empty_note(env):
    client, Session, ids, *_ = env
    login(client, "boss")
    with Session() as s:
        omar = s.scalars(select(AppUser.id).where(AppUser.username == "omar")).one()
    assert client.post(f"/api/calls/{ids['other']}/shares", json={"to_user_id": omar, "note": "x"}).status_code == 422
    assert client.post(f"/api/calls/{ids['other']}/shares",
                       json={"to_user_id": ids["ali"], "note": "   "}).status_code == 422


# ---------- admin: users ----------

def test_admin_creates_agent_password_once(env):
    client, Session, *_ = env
    login(client, "root")
    r = client.post("/api/admin/users", json={"username": "Nora", "role": "agent", "ext": "116"})
    assert r.status_code == 409                                               # 116 already has agent omar
    r = client.post("/api/admin/users", json={"username": "Nora", "role": "supervisor"})
    assert r.status_code == 200 and len(r.json()["password"]) >= 10
    pw = r.json()["password"]
    client.post("/api/logout")
    assert client.post("/api/login", json={"username": "nora", "password": pw}).status_code == 200
    assert "password" not in client.get("/api/me").json()
    login(client, "root")
    assert all("password" not in u for u in client.get("/api/admin/users").json())
    assert client.post("/api/admin/users", json={"username": "x", "role": "admin"}).status_code == 422
    assert client.post("/api/admin/users", json={"username": "y", "role": "agent", "ext": "999"}).status_code == 422
    with Session() as s:
        log = s.scalars(select(AdminLog.target).where(AdminLog.action == "create_user")).all()
        assert log == ["nora (supervisor)"] and pw not in " ".join(log)


def test_disable_ends_sessions_and_admin_is_protected(env):
    client, Session, ids, *_ = env
    agent_client = TestClient(client.app)
    login(agent_client, "ali")
    login(client, "root")
    assert client.patch(f"/api/admin/users/{ids['ali']}", json={"active": False}).status_code == 200
    assert agent_client.get("/api/me").status_code == 401
    with Session() as s:
        root = s.scalars(select(AppUser.id).where(AppUser.username == "root")).one()
    assert client.patch(f"/api/admin/users/{root}", json={"active": False}).status_code == 403
    assert client.post(f"/api/admin/users/{root}/password").status_code == 403
    r = client.post(f"/api/admin/users/{ids['ali']}/password")
    assert r.status_code == 200 and r.json()["password"]


def test_supervisor_cannot_use_admin_pages(env):
    client, _, ids, *_ = env
    login(client, "boss")
    assert client.get("/api/admin/users").status_code == 403
    assert client.put("/api/admin/settings", json={"followup_start": "2026-11-15"}).status_code == 403
    assert client.post(f"/api/calls/{ids['own']}/delete-content", json={"reason": "طلب العميل",
                                                                         "confirm": ids["own"]}).status_code == 403


# ---------- admin: delete call content (decision 7) ----------

def test_delete_call_content(env):
    client, Session, ids, sent, tmp = env
    switch_on(client)
    login(client, "boss")
    client.post(f"/api/calls/{ids['own']}/shares", json={"to_user_id": ids["ali"], "note": "x"})
    client.put(f"/api/calls/{ids['own']}/followup", json={"no_followup": True})
    login(client, "root")
    url = f"/api/calls/{ids['own']}/delete-content"
    assert client.post(url, json={"reason": "طلب العميل", "confirm": ids["own"] + 1}).status_code == 422
    assert client.post(url, json={"reason": "abc", "confirm": ids["own"]}).status_code == 422
    r = client.post(url, json={"reason": "طلب العميل", "confirm": ids["own"]})
    assert r.status_code == 200, r.text
    assert not (tmp / "r" / "own.wav").exists() and not (tmp / "goldset" / "own.wav").exists()
    assert sent == [(ids["own"], "r/own.wav")]
    with Session() as s:
        c = s.get(Call, ids["own"])
        assert c is not None and c.content_deleted_at and c.recording_status == "deleted" and not c.recording_path
        for model in (Transcript, CallAnalysis, CallFollowup, CallShare, GoldClip):
            assert s.scalars(select(model)).first() is None, model.__name__
        row = s.scalars(select(AdminLog).where(AdminLog.action == "delete_call_content")).one()
        assert row.reason == "طلب العميل" and f"call:{ids['own']}" in row.target
    assert client.post(url, json={"reason": "طلب العميل", "confirm": ids["own"]}).status_code == 409
    assert client.get(f"/api/calls/{ids['own']}/followup").json()["needed"] is False
    log = client.get("/api/admin/log").json()
    assert log[0]["action"] == "delete_call_content"
