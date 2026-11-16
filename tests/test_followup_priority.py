"""Spec 2026-11-16-priority-no-followup: priority on the first form (required, set once, 1-day limit when high),
the "no follow-up" counter / filter / KPI, and the "تحتاج حل" (unsolved) section. Real test Postgres."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from callsight import followups as fu
from callsight.db.models import CallFollowup
from test_agent_supervisor_api import _callback_call, boss_id, env, iso, login, not_solved, switch_on  # noqa: F401
from test_followups import DAY, at, form, mk, rows, s, start_on, user  # noqa: F401
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


# ---------- "no follow-up" visible (requirements 8, 9) ----------

def test_kpi_counts_no_followup_and_its_share(s):  # noqa: F811
    start_on(s)
    u = user(s)
    nf = mk(s, "nf", at(9))
    form(s, nf, u, filled=at(9, 5), no_followup=True)
    for n in range(3):
        form(s, mk(s, f"p{n}", at(10 + n)), u, filled=at(10 + n, 5), solved=True)
    mk(s, "empty", at(14), agent="104")                     # 104: a needed call, no form at all
    k = fu.kpi(s, at(0), at(0, day=DAY + timedelta(days=1)), ["104", "116", "119"], callers=["104", "116", "119"],
               now=at(12, day=DAY + timedelta(days=1)))
    a = k["agents"]["119"]
    assert (a["no_followup"], a["answered_forms"], a["no_followup_rate"]) == (1, 3, 0.25)
    assert k["summary"]["no_followup"] == 1 and k["summary"]["no_followup_rate"] == 0.25
    assert k["agents"]["104"]["no_followup"] == 0 and k["agents"]["104"]["no_followup_rate"] is None   # zero forms
    assert k["agents"]["116"]["no_followup_rate"] is None
    # nothing else moved: the old numbers of the same data
    assert (a["forms_due"], a["solved_first"], a["first_rate"]) == (4, 3, 1.0)


def test_pdf_stage_table_has_the_no_followup_columns(s):  # noqa: F811
    from callsight.reports import pdf
    start_on(s)
    u = user(s)
    form(s, mk(s, "nf", at(9)), u, filled=at(9, 5), no_followup=True)
    form(s, mk(s, "p", at(10)), u, filled=at(10, 5), solved=True)
    k = fu.kpi(s, at(0), at(0, day=DAY + timedelta(days=1)), ["119"], callers=["119"],
               now=at(12, day=DAY + timedelta(days=1)))
    html = pdf.followup_table(k, [{"ext": "119", "name": "علي"}])
    assert "<th class=\"n\">لا تحتاج متابعة</th><th class=\"n\">نسبتها</th>" in html
    assert "50.0%" in html


def test_no_followup_counter_and_filter(env):  # noqa: F811
    client, Session, ids, *_ = env
    switch_on(client)
    login(client, "ali")
    assert client.put(f"/api/calls/{ids['own']}/followup", json={"no_followup": True}).status_code == 200
    login(client, "sara")
    assert client.put(f"/api/calls/{ids['other']}/followup", json=not_solved(boss_id(Session))).status_code == 200
    login(client, "ali")
    d = client.get("/api/followups", params={"state": "no_followup"}).json()
    assert [i["call_id"] for i in d["items"]] == [ids["own"]] and d["counts"]["no_followup"] == 1
    login(client, "sara")                          # an agent sees only their own
    d = client.get("/api/followups", params={"state": "no_followup"}).json()
    assert d["items"] == [] and d["counts"]["no_followup"] == 0
    login(client, "boss")
    d = client.get("/api/followups", params={"state": "no_followup"}).json()
    assert [i["call_id"] for i in d["items"]] == [ids["own"]] and d["counts"]["no_followup"] == 1
    assert client.get("/api/followups", params={"state": "no_followup", "agent": "104"}).json()["items"] == []
    assert client.get("/api/followups", params={"state": ""}).json()["counts"]["no_followup"] == 1
    # a priority filter does not empty this view (no-follow-up forms have no priority; review 2026-11-16)
    d = client.get("/api/followups", params={"state": "no_followup", "priority": "high"}).json()
    assert [i["call_id"] for i in d["items"]] == [ids["own"]]


def test_unchanged_expected_time_on_edit(s):  # noqa: F811   (review 2026-11-16)
    start_on(s)
    u = user(s)
    c = mk(s, "a", at(10))
    t0 = at(11)
    base = {"what_happened": "x", "solved": False, "responsible_kind": "customer", "callback_at": None}
    fu.save(s, c, {**base, "priority": "medium", "expected_at": t0 + timedelta(hours=40)}, u, t0)
    with pytest.raises(fu.FormError, match="يوم واحد"):        # edited to high, same time 40 h away: refused
        fu.save(s, c, {**base, "priority": "high", "expected_at": t0 + timedelta(hours=40)}, u, t0 + timedelta(hours=1))
    s.rollback()
    # same time, later and after it passed: still accepted (only a CHANGED time must be in the future)
    later = t0 + timedelta(hours=41)
    assert fu.save(s, c, {**base, "priority": "low", "expected_at": t0 + timedelta(hours=40)}, u, later).priority == "low"
    # an old form from before the 2-day rule (time 10 days away) can still be re-saved without moving it
    f = s.scalars(select(CallFollowup)).one()
    f.expected_at = later + timedelta(days=10)
    s.commit()
    assert fu.save(s, c, {**base, "priority": "medium", "expected_at": f.expected_at}, u, later).priority == "medium"


# ---------- "تحتاج حل" (requirement 11) ----------

def test_unsolved_ignores_dates_and_leaves_when_solved(s):  # noqa: F811
    from test_followups import _note
    start_on(s)
    u = user(s)
    later = at(12, day=DAY + timedelta(days=30))
    def prob(uid, h, prio, *, number, agent="119", solved=False, nofu=False, expected=None):
        c = mk(s, uid, at(h), number=number, agent=agent)
        f = form(s, c, u, filled=at(h, 5), solved=solved, no_followup=nofu)
        f.priority = None if nofu else prio
        f.expected_at = expected
        return c
    prob("old_low", 9, "low", number="0501000001")
    prob("high_wait", 10, "high", number="0501000002", expected=at(12, day=DAY + timedelta(days=40)))
    prob("high_late", 11, "high", number="0501000003", expected=at(12))      # solution_overdue: more urgent
    prob("none", 12, None, number="0501000004")                                 # old form: last
    prob("solved", 13, "medium", number="0501000005", solved=True)
    prob("nofu", 14, None, number="0501000006", nofu=True)
    prob("theirs", 15, "high", number="0501000007", agent="104")
    noted = prob("noted", 16, "medium", number="0501000008")
    noted_form = s.scalars(select(CallFollowup).where(CallFollowup.call_id == noted.id)).one()
    noted_form.callback_at = noted_form.expected_at = at(17)
    mk(s, "cb", at(17, 5), direction="outbound", agent="104", number="0501000008")
    s.commit()
    got = [r.call.uid for r in fu.unsolved(s, ["119"], DAY, later, callers=["104", "116", "119"])[0]]
    assert got == ["high_late", "high_wait", "noted", "old_low", "none"]
    _note(s, noted, u, at(18), solved=True)                                      # a note marks it solved: gone
    s.commit()
    got = [r.call.uid for r in fu.unsolved(s, ["119"], DAY, later, callers=["104", "116", "119"])[0]]
    assert got == ["high_late", "high_wait", "old_low", "none"]
    assert [r.call.uid for r in fu.unsolved(s, ["104"], DAY, later, callers=["104", "116", "119"])[0]] == ["theirs"]
    assert fu.unsolved(s, [], DAY, later, callers=["119"]) == ([], False)


def test_unsolved_in_the_api_whatever_the_dates(env):  # noqa: F811
    client, Session, ids, *_ = env
    from callsight.config import RIYADH
    from callsight.db.models import Call
    old = datetime.now(RIYADH) - timedelta(days=30)
    with Session() as s_:
        s_.add(Call(uid="old", direction="inbound", entry="queue", customer_number="0508888888",
                    team="customer_service", agent_ext="119", start_time=old, end_time=old + timedelta(minutes=3),
                    talk_seconds=100, total_seconds=180, status="answered", recording_status="none"))
        s_.commit()
        old_id = s_.scalars(select(Call.id).where(Call.uid == "old")).one()
    login(client, "root")
    assert client.put("/api/admin/settings", json={"followup_start": (old - timedelta(days=1)).date().isoformat()}
                      ).status_code == 200
    login(client, "ali")
    body = {"what_happened": "مشكلة قديمة", "solved": False, "priority": "low", "responsible_kind": "customer",
            "expected_at": iso(datetime.now(timezone.utc) + timedelta(hours=5))}
    assert client.put(f"/api/calls/{old_id}/followup", json=body).status_code == 200      # late form, allowed
    assert client.put(f"/api/calls/{ids['own']}/followup", json={**body, "priority": "high"}).status_code == 200
    login(client, "sara")
    assert client.put(f"/api/calls/{ids['other']}/followup", json=not_solved(boss_id(Session))).status_code == 200
    today = datetime.now(RIYADH).date()
    login(client, "ali")
    for params in ({"from": today.isoformat(), "to": today.isoformat()},
                   {"from": (today - timedelta(days=3)).isoformat(), "to": (today - timedelta(days=2)).isoformat()},
                   {"state": "solved"}, {"state": "no_followup"}):
        d = client.get("/api/followups", params=params).json()
        assert [i["call_id"] for i in d["unsolved"]] == [ids["own"], old_id], params      # high first
    d = client.get("/api/followups", params={"priority": "low"}).json()
    assert [i["call_id"] for i in d["unsolved"]] == [old_id]
    assert d["counts"]["forms"] == 0                                 # counters unchanged by the section
    login(client, "boss")
    assert {i["call_id"] for i in client.get("/api/followups").json()["unsolved"]} == {ids["own"], old_id, ids["other"]}
    assert [i["call_id"] for i in client.get("/api/followups", params={"agent": "104"}).json()["unsolved"]] == [ids["other"]]
    login(client, "ali")                                             # solved: leaves the section
    assert client.put(f"/api/calls/{ids['own']}/followup", json={"what_happened": "تم", "solved": True,
                                                                  "priority": "high"}).status_code == 200
    assert [i["call_id"] for i in client.get("/api/followups").json()["unsolved"]] == [old_id]


def test_unsolved_is_bounded_and_filters_priority_in_sql(s):  # noqa: F811   (review 2026-11-16)
    start_on(s)
    u = user(s)
    for n, prio in enumerate(("low", "high", "medium", "high")):
        f = form(s, mk(s, f"p{n}", at(9 + n), number=f"050100010{n}"), u, filled=at(9 + n, 5))
        f.priority = prio
    s.commit()
    later = at(12, day=DAY + timedelta(days=3))
    got, cut = fu.unsolved(s, ["119"], DAY, later, callers=["119"], limit=2)
    # the two high ones, same step: the older (10:00) before the newer (12:00) = the "oldest first" tie-break
    assert [r.call.uid for r in got] == ["p1", "p3"] and cut is True
    got, cut = fu.unsolved(s, ["119"], DAY, later, callers=["119"], priority="medium")
    assert [r.call.uid for r in got] == ["p2"] and cut is False
    got, cut = fu.unsolved(s, ["119"], DAY, later, callers=["119"], limit=4)
    assert [r.call.uid for r in got] == ["p1", "p3", "p2", "p0"] and cut is False
