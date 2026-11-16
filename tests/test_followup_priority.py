"""Spec 2026-11-16-priority-no-followup: priority on the first form (required, set once, 1-day limit when high),
the "no follow-up" counter / filter / KPI, and the "تحتاج حل" (unsolved) section. Real test Postgres."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from callsight import followups as fu
from callsight.db.models import CallFollowup
from test_agent_supervisor_api import _callback_call, boss_id, env, iso, login, not_solved, switch_on  # noqa: F401
from test_followups import at, form, mk, rows, s, start_on, user  # noqa: F401
from test_api import engine  # noqa: F401  (fixture)


# ---------- rules (requirements 2, 3, 4, 14) ----------

def test_priority_required_and_kept_in_the_form(s):  # noqa: F811
    start_on(s)
    c = mk(s, "a", at(10))
    now = at(11)
    ok = {"what_happened": "x", "solved": True, "priority": "high"}
    for bad in ({**ok, "priority": None}, {**ok, "priority": "urgent"},
                {k: v for k, v in ok.items() if k != "priority"},
                {**ok, "solved": False, "responsible_kind": "customer", "expected_at": at(16), "priority": ""}):
        with pytest.raises(fu.FormError, match="اختر الأولوية"):
            fu.validate(s, bad, c, now)
    for code in fu.PRIORITIES:
        assert fu.validate(s, {**ok, "priority": code}, c, now)["priority"] == code
    assert fu.validate(s, {"no_followup": True, "priority": "high"}, c, now)["priority"] is None
    assert list(fu.PRIORITIES) == ["high", "medium", "low"]


def test_high_priority_must_be_solved_within_one_day(s):  # noqa: F811
    start_on(s)
    c = mk(s, "a", at(10))
    now = at(11)
    base = {"what_happened": "x", "solved": False, "responsible_kind": "customer"}
    with pytest.raises(fu.FormError, match="يوم واحد"):
        fu.validate(s, {**base, "priority": "high", "expected_at": now + timedelta(hours=25)}, c, now)
    assert fu.validate(s, {**base, "priority": "high", "expected_at": now + timedelta(hours=23)}, c, now)
    assert fu.validate(s, {**base, "priority": "medium", "expected_at": now + timedelta(hours=40)}, c, now)
    with pytest.raises(fu.FormError, match="يومان"):
        fu.validate(s, {**base, "priority": "low", "expected_at": now + timedelta(hours=49)}, c, now)
    # a note follows the first form's priority and never returns one of its own
    note = fu.validate(s, {**base, "expected_at": now + timedelta(hours=20)}, c, now, priority="high")
    assert "priority" not in note
    with pytest.raises(fu.FormError, match="يوم واحد"):
        fu.validate(s, {**base, "expected_at": now + timedelta(hours=25)}, c, now, priority="high")
    assert fu.validate(s, {**base, "expected_at": now + timedelta(hours=40)}, c, now, priority=None)


def test_priority_in_form_dict_and_history(s):  # noqa: F811
    start_on(s)
    u = user(s)
    c = mk(s, "a", at(10))
    f = form(s, c, u, filled=at(10, 5), solved=True)
    f.priority = "medium"
    s.flush()
    d = rows(s)["a"]
    assert d["form"]["priority"] == "medium" and d["form"]["priority_ar"] == "متوسطة"
    assert d["history"][0]["priority"] == "medium" and d["history"][0]["priority_ar"] == "متوسطة"
    f.priority = None                             # an old form
    s.flush()
    d = rows(s)["a"]
    assert d["form"]["priority"] is None and d["form"]["priority_ar"] is None


# ---------- API (requirements 3-6, 13) ----------

def test_priority_rules_through_the_api(env):  # noqa: F811
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    url = f"/api/calls/{ids['own']}/followup"
    body = not_solved(boss_id(Session))
    r = client.put(url, json={k: v for k, v in body.items() if k != "priority"})
    assert r.status_code == 422 and "الأولوية" in r.json()["detail"]
    assert client.put(url, json={**body, "priority": "urgent"}).status_code == 422
    assert client.put(url, json={"what_happened": "تم", "solved": True}).status_code == 422   # solved needs it too
    for code, ar in (("low", "منخفضة"), ("high", "عالية"), ("medium", "متوسطة")):
        r = client.put(url, json={**body, "priority": code})
        assert r.status_code == 200, r.text
        assert r.json()["form"]["priority"] == code and r.json()["form"]["priority_ar"] == ar
        assert r.json()["history"][0]["priority_ar"] == ar
    far = iso(datetime.now(timezone.utc) + timedelta(hours=30))      # high = within 1 day
    r = client.put(url, json={**body, "priority": "high", "expected_at": far})
    assert r.status_code == 422 and "يوم واحد" in r.json()["detail"]
    assert client.put(url, json={**body, "priority": "medium", "expected_at": far}).status_code == 200
    lst = client.get("/api/followups", params={"state": "", "priority": "medium"}).json()
    assert [i["call_id"] for i in lst["items"]] == [ids["own"]]
    assert lst["items"][0]["form"]["priority_ar"] == "متوسطة"
    assert client.get("/api/followups", params={"state": "", "priority": "high"}).json()["items"] == []
    assert client.get("/api/followups", params={"priority": "urgent"}).status_code == 422
    login(client, "boss")                          # a supervisor may correct it
    assert client.put(url, json={**body, "priority": "low"}).json()["form"]["priority"] == "low"
    r = client.put(url, json={"no_followup": True, "priority": "high"})     # nothing to rank: dropped
    assert r.status_code == 200 and r.json()["form"]["priority"] is None
    with Session() as s_:
        assert s_.scalars(select(CallFollowup)).one().priority is None


def test_note_cannot_carry_a_priority(env):  # noqa: F811
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    assert client.put(f"/api/calls/{ids['own']}/followup",
                      json=not_solved(boss_id(Session), priority="high")).status_code == 200
    _callback_call(Session, ids)
    url = f"/api/calls/{ids['own']}/followup/notes"
    assert client.post(url, json={"round": 2, "what_happened": "x", "solved": True, "priority": "low"}
                       ).status_code == 422
    nxt = {k: v for k, v in not_solved(boss_id(Session)).items() if k not in ("what_happened", "priority")}
    nxt["expected_at"] = iso(datetime.now(timezone.utc) + timedelta(hours=30))   # high form: 1 day for notes too
    r = client.post(url, json={"round": 2, "what_happened": "لم يُحل", **nxt})
    assert r.status_code == 422 and "يوم واحد" in r.json()["detail"]
    nxt["expected_at"] = iso(datetime.now(timezone.utc) + timedelta(hours=20))
    r = client.post(url, json={"round": 2, "what_happened": "لم يُحل", **nxt})
    assert r.status_code == 200, r.text
    h = r.json()["history"]
    assert h[0]["priority"] == "high" and h[1]["priority"] is None
