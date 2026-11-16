"""Spec 2026-11-16-priority-no-followup, "Part 4 change: the forward chain": any employee -> 24 h -> his branch
supervisor -> 24 h -> the 4 managers (all of them: the same ticket + a copy each, first close wins). Only branch
supervisors' calls are checked. The other system is faked: no real call."""
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from callsight import forward
from callsight.cs_api import CsAuthError, CsUnavailable
from callsight.db.models import CallFollowup, ForwardHop, ForwardPerson
from test_api import engine  # noqa: F401
from test_followups import at, mk, rows, s, start_on, user  # noqa: F401
from test_forward_d2 import sup_call

YAML = """people:
  - {branch: "فرع الزهور", name: "صالح الغامدي", ext: "101"}
managers:
  - {name: "مدير أول"}
  - {name: "مدير ثاني"}
  - {name: "مدير ثالث"}
  - {name: "مدير رابع"}
"""
TARGETS = {"groups": [{"key": "men", "branches": [
    {"name": "رجال الزهور", "employees": [
        {"guid": "G-SALEH", "name": "صالح محمد الغامدي", "isBranchSupervisor": True},
        {"guid": "G-STAFF", "name": "خالد أحمد", "isBranchSupervisor": False}]},
    {"name": "رجال بلا مشرف", "employees": [{"guid": "G-LONE", "name": "سعد علي", "isBranchSupervisor": False}]},
    {"name": "الإدارة", "employees": [
        {"guid": f"G-M{i}", "name": f"مدير {n}", "isBranchSupervisor": False}
        for i, n in enumerate(("أول", "ثاني", "ثالث", "رابع"), 1)]}]}]}


class Fake:
    """The customer-service system: tickets by guid, every write recorded."""

    def __init__(self):
        self.closed: dict[str, str | None] = {}
        self.done: list[tuple] = []
        self.n = 0
        self.fail: Exception | None = None

    def forward_targets(self):
        return TARGETS

    def get_ticket(self, guid):
        return {"ticket": {"closedAt": self.closed.get(guid)}, "timeline": []}

    def forward_ticket(self, guid, to, note):
        if self.fail:
            raise self.fail
        self.done.append(("forward", guid, to, note))
        self.closed[guid] = None
        return {}

    def reopen(self, guid, note):
        self.done.append(("reopen", guid))
        if not getattr(self, "reopen_fails", False):
            self.closed[guid] = None
        return {}

    def followup(self, guid, note, close=False):
        self.done.append(("followup", guid, close, note))
        return {}

    def customer_by_phone(self, phone):
        return "ACC"

    def create_ticket(self, account, notes, priority, to):
        self.n += 1
        self.done.append(("ticket", to))
        return f"COPY-{self.n}", 2000 + self.n


@pytest.fixture
def cs(s, tmp_path):  # noqa: F811
    s.execute(text("TRUNCATE forward_person, forward_event, forward_hop RESTART IDENTITY CASCADE"))
    s.flush()
    p = tmp_path / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    fake = Fake()
    forward.sync_people(s, fake, p)
    start_on(s)
    return s, fake, {r.guid: r for r in s.scalars(select(ForwardPerson))}


def form_to(s, person, when=None):
    """The agent forwarded to `person`; the send job made ticket T-1 (the chain starts at his level)."""
    u = user(s)
    c = mk(s, "a", at(10), number="0501234567")
    t = when or at(11)
    f = CallFollowup(call_id=c.id, owner_ext="119", what_happened="x", solved=False, priority="high",
                     responsible_kind="forward", forward_person_id=person.id, forward_status="sent",
                     forward_ticket_guid="T-1", forward_ticket_number=1631, forwarded_at=t, expected_at=t,
                     filled_by=u.id, first_filled_at=t, updated_by=u.id, updated_at=t)
    s.add(f)
    s.flush()
    s.add(ForwardHop(call_id=c.id, level=forward.level_of(person), person_id=person.id, ticket_guid="T-1",
                     ticket_number=1631, sent_at=t, result="open"))
    s.commit()
    return c, f


def hops(s, c):
    return [(h.level, h.person_id, h.result, h.ticket_guid) for h in
            s.scalars(select(ForwardHop).where(ForwardHop.call_id == c.id).order_by(ForwardHop.id))]


def tick(s, fake, now):
    """The 5-minute job: read the open tickets back, then move the chain."""
    forward.poll_tickets(s, fake, now=now)
    return forward.advance(s, fake, now=now)


def test_sync_marks_levels(cs):
    s, fake, p = cs
    assert forward.level_of(p["G-STAFF"]) == 1 and forward.level_of(p["G-SALEH"]) == 2
    assert all(forward.level_of(p[f"G-M{i}"]) == 3 for i in range(1, 5))
    assert p["G-SALEH"].ext == "101" and p["G-STAFF"].ext is None


def test_employee_closes_in_time_is_solved_without_a_call_check(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    assert rows(s, at(12))["a"]["forward"]["holders"] == ["خالد أحمد"]
    fake.closed["T-1"] = "2026-11-10T20:00:00"
    tick(s, fake, at(20, 30))
    assert hops(s, c) == [(1, p["G-STAFF"].id, "solved", "T-1")]
    d = rows(s, at(21))["a"]
    assert d["step"] == "forward_solved" and d["forward"]["level"] is None and fake.done == []


def test_24h_employee_to_his_branch_supervisor_then_to_all_managers(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    tick(s, fake, at(11) + timedelta(hours=23))
    assert hops(s, c)[-1][2] == "open" and fake.done == []                 # 24 h not over
    assert rows(s, at(11) + timedelta(hours=23))["a"]["step"] == "forwarded"
    assert rows(s, at(11) + timedelta(hours=24, minutes=1))["a"]["step"] == "forward_overdue"
    t2 = at(11) + timedelta(hours=24, minutes=5)
    tick(s, fake, t2)
    assert fake.done == [("forward", "T-1", "G-SALEH", "لم تُحل خلال 24 ساعة عند «خالد أحمد»")]
    assert hops(s, c) == [(1, p["G-STAFF"].id, "passed_on", "T-1"), (2, p["G-SALEH"].id, "open", "T-1")]
    d = rows(s, t2)["a"]
    assert d["step"] == "forwarded" and d["forward"]["level"] == 2 and d["forward"]["holders"] == ["صالح الغامدي"]
    # the supervisor closes WITHOUT a call: not solved, after his 24 h it goes to the managers
    fake.closed["T-1"] = (t2 + timedelta(hours=2)).isoformat()
    tick(s, fake, t2 + timedelta(hours=3))
    assert rows(s, t2 + timedelta(hours=3))["a"]["step"] == "forward_unconfirmed"
    fake.done.clear()
    t3 = t2 + timedelta(hours=24, minutes=5)
    tick(s, fake, t3)
    assert fake.done[0] == ("reopen", "T-1") and fake.done[1][:3] == ("forward", "T-1", "G-M1")
    tick(s, fake, t3 + timedelta(minutes=5))                               # the copies for the 3 others
    assert [x for x in fake.done if x[0] == "ticket"] == [("ticket", "G-M2"), ("ticket", "G-M3"), ("ticket", "G-M4")]
    got = hops(s, c)
    assert [h[0] for h in got] == [1, 2, 3, 3, 3, 3] and [h[2] for h in got[2:]] == ["open"] * 4
    assert [h[3] for h in got[2:]] == ["T-1", "COPY-1", "COPY-2", "COPY-3"]
    d = rows(s, t3 + timedelta(minutes=6))["a"]
    assert d["forward"]["level"] == 3 and len(d["forward"]["holders"]) == 4
    # manager 3 closes his copy: solved; the 3 other managers' tickets are closed there (manager 1 holds T-1)
    fake.done.clear()
    fake.closed["COPY-2"] = (t3 + timedelta(hours=1)).isoformat()
    tick(s, fake, t3 + timedelta(hours=2))
    got = hops(s, c)
    assert [h[2] for h in got[2:]] == ["closed_by_other", "closed_by_other", "solved", "closed_by_other"]
    assert sorted(x[1] for x in fake.done if x[0] == "followup") == ["COPY-1", "COPY-3", "T-1"]
    assert all(x[2] is True for x in fake.done if x[0] == "followup")
    assert rows(s, t3 + timedelta(hours=2))["a"]["step"] == "forward_solved"
    # no further level: nothing happens later
    fake.done.clear()
    tick(s, fake, t3 + timedelta(days=3))
    assert fake.done == []


def test_supervisor_with_a_good_call_is_solved(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-SALEH"])                                       # chosen directly: level 2
    sup_call(s, "good", at(13), score=9)
    fake.closed["T-1"] = at(14).isoformat()
    tick(s, fake, at(14, 5))
    assert hops(s, c)[-1][2] == "solved"


def test_waiting_for_the_score_does_not_pass_it_on(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-SALEH"])
    sup_call(s, "good", at(13), score=None)                               # no score yet (GPU on our side)
    fake.closed["T-1"] = at(14).isoformat()
    tick(s, fake, at(11) + timedelta(hours=30))
    assert hops(s, c)[-1][2] == "open" and fake.done == []


def test_no_supervisor_in_the_branch_goes_straight_to_the_managers(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-LONE"])
    tick(s, fake, at(11) + timedelta(hours=25))
    assert fake.done[0][:3] == ("forward", "T-1", "G-M1")
    assert [h[0] for h in hops(s, c)] == [1, 3, 3, 3, 3]


def test_a_close_from_before_he_got_it_does_not_count(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    fake.closed["T-1"] = at(9).isoformat()                                # stale close (e.g. reopen failed)
    tick(s, fake, at(12))
    assert hops(s, c)[-1][2] == "open"


def test_errors_keep_the_hop_and_retry(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    fake.fail = CsUnavailable("x")
    tick(s, fake, at(11) + timedelta(hours=25))
    h = s.scalars(select(ForwardHop).where(ForwardHop.call_id == c.id)).one()
    assert h.result == "open" and h.error and h.tries == 1
    fake.fail = CsAuthError(401, "x")
    with pytest.raises(CsAuthError):
        tick(s, fake, at(11) + timedelta(hours=26))
    fake.fail = None
    tick(s, fake, at(11) + timedelta(hours=27))
    assert [h[2] for h in hops(s, c)] == ["passed_on", "open"]


def test_no_managers_linked_waits_with_a_message(cs):
    s, fake, p = cs
    for i in range(1, 5):
        p[f"G-M{i}"].is_manager = False
    s.commit()
    c, f = form_to(s, p["G-SALEH"])
    tick(s, fake, at(11) + timedelta(hours=25))
    h = s.scalars(select(ForwardHop).where(ForwardHop.call_id == c.id)).one()
    assert h.result == "open" and h.error == forward.ERR_NO_NEXT


def test_watch_stops_when_closed_there_or_too_long(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-SALEH"])
    assert forward.watch_list(s, now=at(12))["101"]
    fake.closed["T-1"] = at(12).isoformat()                               # closed without a good call: still open
    tick(s, fake, at(12, 5))
    assert forward.watch_list(s, now=at(20))["101"]                        # 1 day for the closing call
    assert forward.watch_list(s, now=at(12) + timedelta(days=1, minutes=10)) == {}
    fake.closed["T-1"] = None
    tick(s, fake, at(13))
    assert forward.watch_list(s, now=at(11) + forward.WATCH_MAX + timedelta(minutes=1)) == {}   # hard limit


def test_erase_takes_only_the_calls_of_this_problem(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-SALEH"])
    mine = sup_call(s, "mine", at(13), score=9)
    fake.closed["T-1"] = at(14).isoformat()
    tick(s, fake, at(14, 5))                                               # solved
    sup_call(s, "later", at(14) + timedelta(days=5))                       # a later problem of the same customer
    assert [x.id for x in forward.linked_calls(s, c)] == [mine.id]


def test_a_manager_is_never_the_branch_supervisor(cs):
    s, fake, p = cs
    p["G-SALEH"].is_manager = True
    s.commit()
    c, f = form_to(s, p["G-STAFF"])
    tick(s, fake, at(11) + timedelta(hours=25))
    assert fake.done[0][:3] == ("forward", "T-1", "G-SALEH") and hops(s, c)[-1][0] == 3   # straight to managers


def test_a_close_just_before_the_pass_on_is_not_lost(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    t = at(11) + timedelta(hours=24, minutes=5)
    forward.poll_tickets(s, fake, now=t)                                   # read: still open
    fake.closed["T-1"] = (t + timedelta(seconds=30)).isoformat()           # he closes right after the read
    forward.advance(s, fake, now=t + timedelta(minutes=1))
    assert not [x for x in fake.done if x[0] == "forward"]                 # re-read first: not passed on
    tick(s, fake, t + timedelta(minutes=5))
    assert hops(s, c)[-1][2] == "solved"


def test_a_failed_reopen_stops_the_move(cs):
    s, fake, p = cs
    c, f = form_to(s, p["G-SALEH"])
    fake.closed["T-1"] = at(12).isoformat()                                # closed without a call
    fake.reopen_fails = True
    tick(s, fake, at(11) + timedelta(hours=25))
    assert not [x for x in fake.done if x[0] == "forward"]
    h = s.scalars(select(ForwardHop).where(ForwardHop.call_id == c.id)).one()
    assert h.result == "open" and h.error


def test_sync_never_wipes_people_on_an_empty_answer(cs, tmp_path):
    s, fake, p = cs

    class Empty(Fake):
        def forward_targets(self):
            return {"groups": []}
    y = tmp_path / "p.yaml"
    y.write_text(YAML, encoding="utf-8")
    before = len(forward.options(s))
    lines = forward.sync_people(s, Empty(), y)
    assert lines[0].startswith("SKIPPED") and len(forward.options(s)) == before == 7
    assert s.scalars(select(ForwardPerson).where(ForwardPerson.guid == "G-SALEH")).one().ext == "101"
