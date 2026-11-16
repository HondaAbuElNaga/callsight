"""Spec 2026-11-16-priority-no-followup, part 4 D4 (req 20 with the forward chain): each person's late hops (the 24 h
ran out without a solve), one line per case with the reason, and the agents' answers to the ticket's questions."""
from datetime import timedelta

from sqlalchemy import select

from callsight import forward_report as fr
from callsight.db.models import CallFollowup, ForwardEvent, ForwardHop, ForwardMessage, ForwardPerson
from test_agent_supervisor_api import env, login, switch_on  # noqa: F401
from test_api import engine  # noqa: F401
from test_followups import DAY, at, mk, s, start_on, user  # noqa: F401
from test_forward import fwd_env  # noqa: F401
from test_forward_d2 import fs, hop, sent_form, sup_call  # noqa: F401

NEXT = DAY + timedelta(days=1)


def kpi(s, now, agent=None):
    return fr.people_kpi(s, DAY, NEXT, now=now, agent=agent)


def person(d, level):
    return next(r for r in d["people"] if r["level"] == level)


def test_on_time_solve_is_not_a_mistake(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    h.result, h.closed_at, h.ended_at = "solved", at(15), at(15, 5)
    s.commit()
    r = person(kpi(s, at(20)), 2)
    assert (r["received"], r["solved"], r["solved_in_24h"], r["in_24h_rate"], r["late"]) == (1, 1, 1, 1.0, 0)
    assert r["avg_seconds_to_solve"] == 4 * 3600          # from when he got it (11:00) to his close (15:00)


def test_passed_on_is_late_with_the_reason_of_the_bad_call(fs):
    s, path = fs
    c, f = sent_form(s, path)
    sup_call(s, "sup-1", at(13), talk=30, number=c.customer_number, score=9)      # only 30 s
    h = hop(s, f)
    h.closed_at, h.result, h.ended_at = at(14), "passed_on", at(11, day=NEXT)
    m1 = s.scalar(select(ForwardPerson.id).where(ForwardPerson.id != h.person_id).limit(1))
    s.add(ForwardHop(call_id=c.id, level=3, person_id=m1, ticket_guid="T-1", sent_at=h.ended_at, result="open"))
    s.commit()
    d = kpi(s, at(12, day=NEXT))
    sup = person(d, 2)
    assert sup["late"] == 1 and sup["solved"] == 0
    assert sup["mistakes"][0]["call_id"] == c.id
    assert sup["mistakes"][0]["reason"] == (fr.LATE_REASON["passed_on"]
                                            + " · أغلقها دون مكالمة جيدة: المكالمة 60 ثانية أو أقل")
    assert person(d, 3)["late"] == 0 and person(d, 3)["open"] == 1           # the manager's 24 h still run


def test_still_open_after_24h_and_solved_late_count(fs):
    s, path = fs
    c, f = sent_form(s, path)
    assert person(kpi(s, at(10, day=NEXT)), 2)["late"] == 0                  # 23 h: not yet
    d = kpi(s, at(12, day=NEXT))
    assert person(d, 2)["late"] == 1 and person(d, 2)["mistakes"][0]["reason"] == fr.LATE_REASON["open"]
    h = hop(s, f)
    h.result, h.closed_at, h.ended_at = "solved", at(13, day=NEXT), at(13, day=NEXT)
    s.commit()
    r = person(kpi(s, at(14, day=NEXT)), 2)
    assert r["late"] == 1 and r["solved_in_24h"] == 0 and r["mistakes"][0]["reason"] == fr.LATE_REASON["solved"]


def test_period_is_the_day_he_got_it_and_erased_calls_drop(fs):
    s, path = fs
    c, f = sent_form(s, path)
    assert fr.people_kpi(s, NEXT, NEXT, now=at(12))["people"] == []
    c.content_deleted_at = at(12)
    s.commit()
    assert kpi(s, at(12))["people"] == []


def _question(s, c, h, seen):
    s.add(ForwardEvent(call_id=c.id, ext_key="c", kind="event", type="created", actor="PBX", at=at(11), seen_at=at(11),
                       hop_id=h.id))
    s.add(ForwardEvent(call_id=c.id, ext_key=f"q{seen}", kind="followup", type="followup", note="سؤال؟",
                       actor="صالح", at=seen, seen_at=seen, hop_id=h.id))
    s.commit()


def test_agent_answer_on_time_late_and_waiting(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    _question(s, c, h, at(12))
    a = kpi(s, at(13))["agents"][0]
    assert (a["ext"], a["questions"], a["on_time"], a["late"], a["waiting"]) == ("119", 1, 0, 0, 1)
    late = kpi(s, at(0, day=NEXT))["agents"][0]                              # 23:00 passed with no answer
    assert late["late"] == 1 and late["mistakes"][0]["reason"] == "لم يرد حتى انتهى الموعد"
    s.add(ForwardMessage(call_id=c.id, kind="answer", text="نعم", created_at=at(22), status="sent"))
    s.commit()
    assert kpi(s, at(0, day=NEXT))["agents"][0]["on_time"] == 1
    assert kpi(s, at(0, day=NEXT), agent="104")["agents"] == []             # the agent filter


def test_a_late_question_gets_two_hours(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    _question(s, c, h, at(22))                                              # read at 22:00 -> due 00:00
    s.add(ForwardMessage(call_id=c.id, kind="seen", created_at=at(23, 30), status="done"))
    s.commit()
    a = kpi(s, at(3, day=NEXT))["agents"][0]
    assert (a["on_time"], a["late"]) == (1, 0)


def test_api_staff_only_and_range(fwd_env):
    client, Session, ids, *_ = fwd_env
    switch_on(client)
    login(client, "ali")
    assert client.get("/api/kpi/forwards").status_code == 403
    login(client, "root")
    assert client.get("/api/kpi/forwards", params={"from": "2026-01-01", "to": "2027-06-01"}).status_code == 422
    r = client.get("/api/kpi/forwards", params={"from": "2026-11-01", "to": "2026-11-02"})
    assert r.status_code == 200 and r.json()["people"] == [] and r.json()["agents"] == []
    assert client.get("/api/kpi/forwards", params={"agent": "x1"}).status_code == 422


# ---------- review D4: fair time split, solved questions, rate, our score delay ----------

def test_time_waiting_for_our_agent_is_not_on_his_clock(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    _question(s, c, h, at(12))                                              # waits 5 h for our agent
    s.add(ForwardMessage(call_id=c.id, kind="answer", text="نعم", created_at=at(17), status="sent"))
    h.result, h.ended_at = "passed_on", at(12, day=NEXT)                   # 25 h on the clock, 20 h his own
    s.commit()
    r = person(kpi(s, at(13, day=NEXT)), 2)
    assert (r["late"], r["excused"], r["decided"]) == (0, 1, 1)


def test_a_failed_answer_does_not_stop_the_wait(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    _question(s, c, h, at(12))
    s.add(ForwardMessage(call_id=c.id, kind="answer", text="نعم", created_at=at(13), status="failed"))
    s.commit()
    a = kpi(s, at(0, day=NEXT))["agents"][0]
    assert (a["on_time"], a["late"]) == (0, 1)


def test_a_question_solved_before_its_deadline_is_not_counted(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    _question(s, c, h, at(12))
    h.result, h.closed_at, h.ended_at = "solved", at(12, 30), at(12, 35)
    s.commit()
    assert kpi(s, at(0, day=NEXT))["agents"] == []


def test_rate_counts_only_decided_hops(fs):
    s, path = fs
    c, f = sent_form(s, path)                                               # open, inside its 24 h
    r = person(kpi(s, at(15)), 2)
    assert (r["received"], r["decided"], r["in_24h_rate"]) == (1, 0, None)


def test_waiting_for_our_score_is_not_his_mistake(fs):
    s, path = fs
    c, f = sent_form(s, path)
    sup_call(s, "sup-1", at(13), talk=90, number=c.customer_number)         # good call, no score yet
    h = hop(s, f)
    h.closed_at, h.result, h.ended_at = at(14), "passed_on", at(11, day=NEXT + timedelta(days=1))
    s.commit()
    r = person(kpi(s, at(12, day=NEXT + timedelta(days=1))), 2)
    assert (r["late"], r["excused"]) == (0, 1)


def test_mistake_line_shows_customer_due_and_end(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    h.result, h.ended_at = "passed_on", at(11, 5, day=NEXT)
    s.commit()
    m = person(kpi(s, at(12, day=NEXT)), 2)["mistakes"][0]
    assert m["customer_number"] == c.customer_number and m["agent_wait_seconds"] == 0
    assert m["due"] and m["ended_at"]
