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


def not_solved(boss_id, minutes=60, priority="medium") -> dict:
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return {"what_happened": "طلب استرجاع", "solved": False, "priority": priority, "responsible_kind": "user",
            "responsible_user_id": boss_id, "expected_at": iso(t + timedelta(hours=1)), "callback_at": iso(t)}


def old_times(Session, call_id, minutes=60) -> None:
    """Give the saved form a callback + expected time, as forms saved before 2026-11-23 have (the form asks none)."""
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    with Session() as s:
        f = s.scalars(select(CallFollowup).where(CallFollowup.call_id == call_id)).one()
        f.callback_at, f.expected_at = t, t + timedelta(hours=1)
        s.commit()


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
    assert d["needed"] and d["form"]["solved"] is False and d["callback"] is None and d["step"] == "waiting_solution"
    assert d["form"]["expected_at"] is None and d["form"]["callback_at"] is None     # times sent: ignored
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
    no_who = {k: v for k, v in not_solved(boss_id(Session)).items() if k != "responsible_kind"}
    assert client.put(f"/api/calls/{ids['own']}/followup", json=no_who).status_code == 422
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
    login(client, "boss")
    k = client.get("/api/kpi", params={"agent": "119"}).json()["followup"]
    assert k["enabled"] is True and k["active"] is False and set(k["agents"]) == {"119"}
    login(client, "ali")
    assert client.get("/api/kpi").status_code == 403


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
    assert client.get("/api/calls").status_code == 403                      # agents have no call list at all
    assert client.get(f"/api/calls/{ids['other']}/followup").json()["needed"] is False
    assert client.put(f"/api/calls/{ids['other']}/followup", json={"no_followup": True}).status_code in (404, 409)
    lst = client.get("/api/chats").json()
    assert lst["unread"] == 1 and lst["items"][0]["seen_at"] is None
    got = client.get(f"/api/chats/{share_id}").json()
    assert got["seen_at"] and [m["text"] for m in got["messages"]] == ["استمع للترحيب"]
    login(client, "sara")
    assert client.get("/api/chats").json()["items"] == []
    assert client.get(f"/api/chats/{share_id}").status_code == 404
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


def test_delete_drops_the_gold_draft_text(env):
    import json
    client, _, ids, _, tmp = env
    (tmp / "goldset" / "drafts.json").write_text(json.dumps({"own.wav": {"text": "نص"}, "x.wav": {"text": "y"}}),
                                                 encoding="utf-8")
    login(client, "root")
    assert client.post(f"/api/calls/{ids['own']}/delete-content",
                       json={"reason": "طلب العميل", "confirm": ids["own"]}).status_code == 200
    assert json.loads((tmp / "goldset" / "drafts.json").read_text(encoding="utf-8")) == {"x.wav": {"text": "y"}}


def test_writes_from_another_origin_are_refused(env):
    client, _, ids, *_ = env
    login(client, "root")
    evil = {"origin": "https://evil.example.com"}
    assert client.post(f"/api/admin/users/{ids['ali']}/password", headers=evil).status_code == 403
    assert client.post("/api/login", json={"username": "root", "password": "x"}, headers=evil).status_code == 403
    ok = {"origin": "http://testserver"}
    assert client.post(f"/api/admin/users/{ids['ali']}/password", headers=ok).status_code == 200


# ---------- follow-up stages (spec 2026-11-15-followup-stages) ----------

def _callback_call(Session, ids, uid="cb", agent="104", minutes_ago=-1, number="0501234567"):
    """Default: rings 1 min after now, i.e. after the form the test just saved (a call before the form does not count)."""
    with Session() as s:
        t = _now_riyadh() - timedelta(minutes=minutes_ago)
        s.add(Call(uid=uid, direction="outbound", entry="direct", customer_number=number, team="customer_service",
                   agent_ext=agent, start_time=t, end_time=t + timedelta(seconds=40), talk_seconds=30,
                   total_seconds=40, status="answered", recording_status="none"))
        s.commit()


def test_note_after_callback_flow(env):
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    assert client.put(f"/api/calls/{ids['own']}/followup", json=not_solved(boss_id(Session))).status_code == 200
    old_times(Session, ids["own"])
    v = client.get(f"/api/calls/{ids['own']}/followup").json()
    assert v["step"] == "waiting_callback" and v["round"] == 1
    assert client.post(f"/api/calls/{ids['own']}/followup/notes",
                       json={"round": 2, "what_happened": "x", "solved": True}).status_code == 409   # no callback yet
    _callback_call(Session, ids)
    v = client.get(f"/api/calls/{ids['own']}/followup").json()
    assert v["step"] == "note_needed" and v["callback"]["by_ext"] == "104" and v["note_deadline"]
    login(client, "sara")
    assert client.post(f"/api/calls/{ids['own']}/followup/notes",
                       json={"round": 2, "what_happened": "x", "solved": True}).status_code == 404
    login(client, "ali")
    r = client.post(f"/api/calls/{ids['own']}/followup/notes", json={"round": 2, "what_happened": "تم", "solved": True})
    assert r.status_code == 200, r.text
    assert r.json()["step"] == "solved_after" and [h["round"] for h in r.json()["history"]] == [1, 2]
    assert r.json()["history"][1]["by"] == "ali"
    again = client.post(f"/api/calls/{ids['own']}/followup/notes", json={"round": 2, "what_happened": "تم", "solved": True})
    assert again.status_code == 409
    bad = client.post(f"/api/calls/{ids['own']}/followup/notes", json={"round": "2", "what_happened": "x"})
    assert bad.status_code == 422


def test_note_bad_text_is_422(env):
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    client.put(f"/api/calls/{ids['own']}/followup", json=not_solved(boss_id(Session)))
    _callback_call(Session, ids)
    r = client.post(f"/api/calls/{ids['own']}/followup/notes", json={"round": 2, "what_happened": " ", "solved": True})
    assert r.status_code == 422


def test_needs_action_shows_old_overdue_forms_and_counts_match(env):
    client, Session, ids, *_ = env
    old = _now_riyadh() - timedelta(days=30)
    with Session() as s:
        s.add(Call(uid="old", direction="inbound", entry="queue", customer_number="0508888888", team="customer_service",
                   agent_ext="119", start_time=old, end_time=old + timedelta(minutes=3), talk_seconds=100,
                   total_seconds=180, status="answered", recording_status="none"))
        s.commit()
        old_id = s.scalars(select(Call.id).where(Call.uid == "old")).one()
    login(client, "root")
    assert client.put("/api/admin/settings", json={"followup_start": (old - timedelta(days=1)).date().isoformat()}
                      ).status_code == 200
    login(client, "ali")
    today = _now_riyadh().date()
    d = client.get("/api/followups", params={"from": (today - timedelta(days=1)).isoformat(),
                                             "to": today.isoformat()}).json()      # dates follow من/إلى
    assert [i["call_id"] for i in d["items"]] == [ids["own"]]
    assert d["counts"] == {"forms": 1, "notes": 0, "callbacks_today": 0, "missed": 0, "no_followup": 0,
                           "forward_solved_today": 0, "questions": 0} and d["linked"] is True
    assert d["older_action"] == 1                                    # the 30-day-old form is not forgotten
    wide = client.get("/api/followups", params={"from": (today - timedelta(days=40)).isoformat(),
                                                "to": today.isoformat()}).json()
    steps = {i["call_id"]: i["step"] for i in wide["items"]}
    assert steps == {old_id: "overdue", ids["own"]: "required"}
    assert [i["call_id"] for i in wide["items"]] == [old_id, ids["own"]]          # overdue before required
    assert wide["counts"]["forms"] == 2 and wide["older_action"] == 0
    forms = client.get("/api/followups", params={"from": (today - timedelta(days=40)).isoformat(), "state": "forms"})
    assert {i["call_id"] for i in forms.json()["items"]} == {old_id, ids["own"]}
    assert client.get("/api/followups", params={"state": "notes"}).json()["items"] == []


def test_agent_without_team_extension_is_told(env):
    client, Session, ids, *_ = env
    switch_on(client)
    with Session() as s:
        s.execute(text("UPDATE app_user SET ext = '999' WHERE username = 'sara'"))
        s.commit()
    login(client, "sara")
    d = client.get("/api/followups").json()
    assert d["linked"] is False and d["items"] == []


def test_agent_hears_only_shared_calls(env):
    client, _, ids, *_ = env
    login(client, "ali")
    assert client.get(f"/api/calls/{ids['own']}").json()["has_recording"] is False
    assert client.get(f"/api/calls/{ids['own']}/recording").status_code == 404     # own call, not shared
    login(client, "boss")
    assert client.get(f"/api/calls/{ids['own']}/recording").status_code == 200     # staff: always
    client.post(f"/api/calls/{ids['own']}/shares", json={"to_user_id": ids["ali"], "note": "استمع"})
    login(client, "ali")
    assert client.get(f"/api/calls/{ids['own']}").json()["has_recording"] is True
    assert client.get(f"/api/calls/{ids['own']}/recording").status_code == 200


def test_form_is_frozen_once_a_note_exists_and_wrong_round_is_409(env):
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    client.put(f"/api/calls/{ids['own']}/followup", json=not_solved(boss_id(Session)))
    old_times(Session, ids["own"])
    _callback_call(Session, ids)
    wrong = client.post(f"/api/calls/{ids['own']}/followup/notes", json={"round": 3, "what_happened": "x", "solved": True})
    assert wrong.status_code == 409
    nxt = not_solved(boss_id(Session), minutes=24 * 60)
    nxt.pop("what_happened")
    nxt.pop("priority")                       # set once, in the first form: a note never carries it
    r = client.post(f"/api/calls/{ids['own']}/followup/notes", json={"round": 2, "what_happened": "لم يُحل", **nxt})
    assert r.status_code == 200 and r.json()["step"] == "waiting_solution" and r.json()["round"] == 2
    login(client, "boss")
    assert client.put(f"/api/calls/{ids['own']}/followup", json={"no_followup": True}).status_code == 409


# ---------- private note per call (spec 2026-11-15-private-note) ----------

def test_private_note_only_the_writer_sees_it(env):
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    r = client.put(f"/api/calls/{ids['own']}/my-note", json={"text": " اتصل بعد الظهر "})
    assert r.json()["text"] == "اتصل بعد الظهر"
    assert client.put(f"/api/calls/{ids['own']}/my-note", json={"text": "تغيير"}).status_code == 200
    item = client.get("/api/followups").json()["items"][0]
    assert item["call_id"] == ids["own"] and item["my_note"] == "تغيير"
    assert client.put(f"/api/calls/{ids['other']}/my-note", json={"text": "x"}).status_code == 404   # not his call
    assert client.put(f"/api/calls/{ids['own']}/my-note", json={"text": "x" * 301}).status_code == 422
    login(client, "boss")
    seen = {i["call_id"]: i["my_note"] for i in client.get("/api/followups").json()["items"]}
    assert seen[ids["own"]] is None                                   # the supervisor never sees it
    login(client, "sara")
    assert client.put(f"/api/calls/{ids['own']}/my-note", json={"text": "x"}).status_code == 404
    login(client, "ali")
    assert client.put(f"/api/calls/{ids['own']}/my-note", json={"text": "  "}).json()["text"] is None
    assert client.get("/api/followups").json()["items"][0]["my_note"] is None


def test_private_note_erased_with_the_content(env):
    from callsight.db.models import CallPrivateNote
    client, Session, ids, *_ = env
    login(client, "ali")
    client.put(f"/api/calls/{ids['own']}/my-note", json={"text": "تذكير"})
    login(client, "root")
    r = client.post(f"/api/calls/{ids['own']}/delete-content", json={"reason": "طلب العميل", "confirm": ids["own"]})
    assert r.status_code == 200, r.text
    with Session() as s:
        assert s.query(CallPrivateNote).count() == 0


def test_staff_share_list_follows_the_agent_filter(env):
    client, _, ids, *_ = env
    login(client, "boss")
    client.post(f"/api/calls/{ids['other']}/shares", json={"to_user_id": ids["ali"], "note": "لعلي"})
    assert len(client.get("/api/chats").json()["items"]) == 1
    assert len(client.get("/api/chats", params={"agent": "119"}).json()["items"]) == 1
    assert client.get("/api/chats", params={"agent": "104"}).json()["items"] == []
    login(client, "sara")
    assert client.get("/api/chats", params={"agent": "119"}).json()["items"] == []     # agents: own shares only
