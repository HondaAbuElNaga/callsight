"""User decision 2026-11-22: our supervisor / admin closes a forwarded problem and picks who in the chain solved it.
That person gets the KPI point (on time only when his own time was within 24 h), the others stay as they were
(late stays late), the chain stops, and the worker closes the open ticket(s) there. The other system is faked."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from callsight import forward
from callsight import forward_report as fr
from callsight.cs_api import CsError, CsUnavailable
from callsight.db.models import ForwardHop
from test_api import engine  # noqa: F401
from test_followups import DAY, at, rows, s, user  # noqa: F401
from test_forward_chain import Fake, cs, form_to, hops, tick  # noqa: F401
from test_forward import fwd_env  # noqa: F401
from test_agent_supervisor_api import env  # noqa: F401

NEXT = DAY + timedelta(days=4)


def hop_of(s, c, level):
    return s.scalars(select(ForwardHop).where(ForwardHop.call_id == c.id, ForwardHop.level == level)
                     .order_by(ForwardHop.id)).first()


def boss(s):
    from callsight.db.models import AppUser
    return s.scalar(select(AppUser).where(AppUser.username == "boss")) or user(s, "boss", "supervisor", None)


def person(d, level):
    return next(r for r in d["people"] if r["level"] == level)


def test_decide_the_employee_in_time_closes_there_and_counts_on_time(cs):  # noqa: F811
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    b = boss(s)
    h = forward.decide(s, c.id, hop_of(s, c, 1).id, b.id, at(15))
    s.commit()
    assert hops(s, c) == [(1, p["G-STAFF"].id, "solved", "T-1")]
    assert (h.decided_by, h.erp_close) == (b.id, "pending")
    assert rows(s, at(16))["a"]["step"] == "forward_solved"
    assert tick(s, fake, at(16))["solved"] == 0                    # the chain does nothing more
    assert forward.close_decided(s, fake, at(16)) == {"erp_closed": 1, "erp_close_failed": 0}
    assert fake.done == [("followup", "T-1", True, "أُغلقت بقرار مشرف خدمة العملاء: حلّها «خالد أحمد»")]
    s.refresh(h)
    assert h.erp_close == "done" and h.closed_at is None          # his own close never made up
    assert forward.close_decided(s, fake, at(17))["erp_closed"] == 0
    emp = person(fr.people_kpi(s, DAY, NEXT, now=at(18)), 1)
    assert (emp["solved"], emp["solved_in_24h"], emp["late"]) == (1, 1, 0)
    st = fr.story(s, c.id)
    assert st["state"] == "solved" and st["hops"][0]["decided_by"] == "boss"
    assert st["hops"][0]["can_decide"] is False


def test_decide_a_past_person_late_stays_late_and_the_holder_is_closed(cs):  # noqa: F811
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    t2 = at(11) + timedelta(hours=24, minutes=5)
    tick(s, fake, t2)                                              # -> branch supervisor
    assert [x[2] for x in hops(s, c)] == ["passed_on", "open"]
    st = fr.story(s, c.id)
    assert [x["can_decide"] for x in st["hops"]] == [True, True]
    forward.decide(s, c.id, hop_of(s, c, 1).id, boss(s).id, t2 + timedelta(hours=1))
    s.commit()
    assert [x[2] for x in hops(s, c)] == ["solved", "closed_by_other"]
    assert hop_of(s, c, 2).erp_close == "pending" and hop_of(s, c, 1).erp_close is None
    d = fr.people_kpi(s, DAY, NEXT, now=t2 + timedelta(hours=2))
    emp = person(d, 1)
    assert (emp["solved"], emp["solved_in_24h"], emp["late"]) == (1, 0, 1)   # credited, but after his 24 h
    assert person(d, 2)["late"] == 0


def test_decide_the_supervisor_who_closed_without_a_good_call(cs):  # noqa: F811
    """forward_unconfirmed: he closed it there, our call check said no; the supervisor's decision counts it."""
    s, fake, p = cs
    c, f = form_to(s, p["G-SALEH"])
    fake.closed["T-1"] = at(13).isoformat()
    tick(s, fake, at(14))
    assert rows(s, at(14))["a"]["step"] == "forward_unconfirmed"
    h = forward.decide(s, c.id, hop_of(s, c, 2).id, boss(s).id, at(15))
    s.commit()
    assert h.result == "solved" and h.erp_close is None           # already closed there: nothing to close
    sv = person(fr.people_kpi(s, DAY, NEXT, now=at(16)), 2)
    assert (sv["solved"], sv["solved_in_24h"]) == (1, 1)          # his own close time counts
    assert forward.close_decided(s, fake, at(16))["erp_closed"] == 0 and fake.done == []


def test_decide_refused(cs):  # noqa: F811
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    b = boss(s)
    with pytest.raises(forward.DecideError):
        forward.decide(s, 999999, 1, b.id)
    with pytest.raises(forward.DecideError):
        forward.decide(s, c.id, 999999, b.id)
    s.rollback()
    forward.decide(s, c.id, hop_of(s, c, 1).id, b.id, at(15))
    s.commit()
    with pytest.raises(forward.DecideError, match="من قبل"):
        forward.decide(s, c.id, hop_of(s, c, 1).id, b.id, at(16))
    s.rollback()


def test_pending_copy_cannot_be_picked(cs):  # noqa: F811
    s, fake, p = cs
    c, f = form_to(s, p["G-SALEH"])
    s.add(ForwardHop(call_id=c.id, level=3, person_id=p["G-M1"].id, result="pending"))
    s.commit()
    with pytest.raises(forward.DecideError):
        forward.decide(s, c.id, hop_of(s, c, 3).id, boss(s).id, at(15))
    s.rollback()
    forward.decide(s, c.id, hop_of(s, c, 2).id, boss(s).id, at(15))
    s.commit()
    assert [x[2] for x in hops(s, c)] == ["solved", "closed_by_other"]


class Refuse(Fake):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def followup(self, guid, note, close=False):
        raise self.exc


def test_close_there_refused_or_busy(cs):  # noqa: F811
    s, _, p = cs
    c, f = form_to(s, p["G-STAFF"])
    h = forward.decide(s, c.id, hop_of(s, c, 1).id, boss(s).id, at(15))
    s.commit()
    busy = Refuse(CsUnavailable("busy", 429))
    assert forward.close_decided(s, busy, at(16))["erp_closed"] == 0
    s.refresh(h)
    assert h.erp_close == "pending" and h.tries == 1               # tried again next run
    assert forward.close_decided(s, busy, at(15) + timedelta(days=4))["erp_close_failed"] == 1
    s.refresh(h)
    assert h.erp_close == "failed"
    h.erp_close = "pending"
    s.commit()
    assert forward.close_decided(s, Refuse(CsError(404, "not found")), at(16))["erp_close_failed"] == 1
    s.refresh(h)
    assert (h.erp_close, h.error) == ("failed", "not found")
    assert fr.story(s, c.id)["hops"][0]["erp_close"] == "failed"


def test_api_decide_staff_only(fwd_env):  # noqa: F811
    client, *_ = fwd_env
    from test_agent_supervisor_api import login, switch_on
    switch_on(client)
    login(client, "ali")
    assert client.post("/api/forwards/123456/decide", json={"hop_id": 1}).status_code == 403
    login(client, "root")
    r = client.post("/api/forwards/123456/decide", json={"hop_id": 1})
    assert r.status_code == 409 and "محوّلة" in r.json()["detail"]


def test_holder_already_late_at_the_decision_stays_late(cs):  # noqa: F811
    """Review fix: crediting a past person never wipes the late mark of the one who held it for 30 h."""
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    t2 = at(11) + timedelta(hours=24, minutes=5)
    tick(s, fake, t2)                                              # -> branch supervisor (no next: no managers move yet)
    t_dec = t2 + timedelta(hours=30)
    forward.decide(s, c.id, hop_of(s, c, 1).id, boss(s).id, t_dec)
    s.commit()
    d = fr.people_kpi(s, DAY, NEXT, now=t_dec + timedelta(hours=1))
    assert person(d, 2)["late"] == 1
    tl = rows(s, t_dec + timedelta(hours=1))["a"]
    assert tl["step"] == "forward_solved"


def test_busy_lock_is_a_try_again(cs, engine):  # noqa: F811
    from sqlalchemy.orm import Session
    from callsight.db.models import CallFollowup
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    b = boss(s)
    s.commit()
    with Session(engine) as other:
        other.scalars(select(CallFollowup).where(CallFollowup.call_id == c.id).with_for_update()).first()
        with pytest.raises(forward.DecideError, match="حاول"):
            forward.decide(s, c.id, hop_of(s, c, 1).id, b.id, at(15))
        other.rollback()


def test_poll_running_during_a_decision_never_writes_our_close_on_the_hop(cs, engine):  # noqa: F811
    """Review fix: the poll read the ticket while our supervisor closed it: the close there is ours, not his."""
    from sqlalchemy.orm import Session
    s, fake, p = cs
    c, f = form_to(s, p["G-STAFF"])
    hid, bid = hop_of(s, c, 1).id, boss(s).id
    s.commit()

    class Racing(Fake):
        def get_ticket(self, guid):
            with Session(engine) as other:          # the supervisor's click, between the poll's read and its save
                forward.decide(other, c.id, hid, bid, at(15))
                other.commit()
            return {"ticket": {"closedAt": at(15, 1).isoformat()}, "timeline": []}

    forward.poll_tickets(s, Racing(), now=at(15, 2))
    h = s.get(ForwardHop, hid)
    s.refresh(h)
    assert h.result == "solved" and h.closed_at is None
