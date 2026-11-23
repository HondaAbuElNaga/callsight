"""Forwards page, "needs action now" (2026-11-23): what a supervisor must chase whatever the dates: late / due soon,
a send that failed or stuck, a ticket close that failed there (and the "closed there by hand" mark), open + high,
a question waiting for our agent, plus the time left and the repeat-customer mark on each line."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from callsight import forward
from callsight import forward_report as fr
from callsight.config import RIYADH
from callsight.db.models import AppUser, CallFollowup, ForwardEvent, ForwardHop, ForwardMessage
from test_api import engine  # noqa: F401
from test_followups import at, mk, s, user  # noqa: F401
from test_forward_chain import cs  # noqa: F401
from test_forward import fwd_env  # noqa: F401
from test_agent_supervisor_api import env  # noqa: F401

H = timedelta(hours=1)


def day(t):
    return t.astimezone(RIYADH).date()


def fwd(s, person, uid, *, t=None, status="sent", priority="medium", number="0501234567", err=None):
    """A forward form (and its first hop when sent) on a new call."""
    u = s.scalar(select(forward.AppUser).where(forward.AppUser.username == "ali")) if hasattr(forward, "AppUser") \
        else None
    if u is None:
        from callsight.db.models import AppUser
        u = s.scalar(select(AppUser).where(AppUser.username == "ali")) or user(s)
    t = t or at(11)
    c = mk(s, uid, t - H, number=number)
    f = CallFollowup(call_id=c.id, owner_ext="119", what_happened="x", solved=False, priority=priority,
                     responsible_kind="forward", forward_person_id=person.id, forward_status=status,
                     forward_error=err, forward_ticket_guid=f"T-{uid}" if status == "sent" else None,
                     forwarded_at=t if status == "sent" else None, expected_at=t + 24 * H,
                     filled_by=u.id, first_filled_at=t, updated_by=u.id, updated_at=t)
    s.add(f)
    s.flush()
    if status == "sent":
        s.add(ForwardHop(call_id=c.id, level=forward.level_of(person), person_id=person.id, ticket_guid=f"T-{uid}",
                         ticket_number=1, sent_at=t, result="open"))
    s.commit()
    return c, f


def hop(s, c):
    return s.scalars(select(ForwardHop).where(ForwardHop.call_id == c.id).order_by(ForwardHop.id)).first()


def page(s, now, need=None, frm=None):
    frm = frm or day(at(11))
    return fr.listing(s, frm, frm, need=need, now=now)


def test_soon_then_late_whatever_the_dates(cs):  # noqa: F811
    s, _, p = cs
    c, _ = fwd(s, p["G-STAFF"], "a")
    out = page(s, at(11) + 2 * H)
    line = out["items"][0]
    assert datetime.fromisoformat(line["due"]) == at(11) + 24 * H and line["needs"] == []
    assert out["attention"]["soon"] == 0 and out["attention"]["late"] == 0
    out = page(s, at(11) + 21 * H)
    assert out["attention"]["soon"] == 1 and "soon" in out["items"][0]["needs"]
    # late: counted and listed even when the chosen dates do not hold it
    later = day(at(11)) + timedelta(days=10)
    out = page(s, at(11) + 25 * H, need="late", frm=later)
    assert out["attention"]["late"] == 1 and out["attention"]["soon"] == 0
    assert [i["call_id"] for i in out["items"]] == [c.id]
    assert page(s, at(11) + 25 * H, frm=later)["items"] == []       # the normal list keeps its dates
    # solved: no longer needs anything
    h = hop(s, c)
    h.result, h.ended_at = "solved", at(11) + 26 * H
    s.commit()
    out = page(s, at(11) + 27 * H)
    assert all(v == 0 for v in out["attention"].values()) and out["items"][0]["due"] is None


def test_high_priority_open(cs):  # noqa: F811
    s, _, p = cs
    fwd(s, p["G-STAFF"], "a", priority="high")
    fwd(s, p["G-STAFF"], "b", number="0559999999")
    out = page(s, at(12), need="high")
    assert out["attention"]["high"] == 1 and len(out["items"]) == 1 and out["items"][0]["priority"] == "high"


def test_send_failed_or_stuck(cs):  # noqa: F811
    s, _, p = cs
    c1, _ = fwd(s, p["G-STAFF"], "a", status="failed", err="رفض النظام")
    fwd(s, p["G-STAFF"], "b", status="pending", t=at(11, 55))             # just saved: still sending, not stuck
    c3, _ = fwd(s, p["G-STAFF"], "c", status="pending", t=at(11, 30))     # 30 min: stuck
    out = page(s, at(12), need="send")
    assert out["attention"]["send"] == 2
    got = {i["call_id"]: i for i in out["items"]}
    assert set(got) == {c1.id, c3.id}
    assert got[c1.id]["state"] == "send_failed" and got[c1.id]["send_error"] == "رفض النظام"
    assert got[c3.id]["state"] == "sending" and got[c3.id]["first"]["name"] == "خالد أحمد"


def test_chain_stopped_counts_as_send(cs):  # noqa: F811
    s, _, p = cs
    c, _ = fwd(s, p["G-STAFF"], "a")
    h = hop(s, c)
    h.result, h.ended_at = "passed_on", at(11) + 24 * H
    s.add(ForwardHop(call_id=c.id, level=2, person_id=p["G-SALEH"].id, result="failed", error="x"))
    s.commit()
    out = page(s, at(11) + 25 * H, need="send")
    assert [i["state"] for i in out["items"]] == ["unsolved"]


def test_erp_close_failed_then_closed_by_hand(cs):  # noqa: F811
    s, _, p = cs
    c, _ = fwd(s, p["G-STAFF"], "a")
    h = hop(s, c)
    h.result, h.ended_at, h.erp_close = "solved", at(12), "failed"
    s.commit()
    out = page(s, at(13), need="erp_close")
    assert out["attention"]["erp_close"] == 1 and [i["call_id"] for i in out["items"]] == [c.id]
    with pytest.raises(forward.DecideError):
        forward.closed_by_hand(s, c.id, h.id + 999)
    forward.closed_by_hand(s, c.id, h.id)
    s.commit()
    assert hop(s, c).erp_close == "manual" and page(s, at(13))["attention"]["erp_close"] == 0
    with pytest.raises(forward.DecideError):                       # only a failed close can be marked
        forward.closed_by_hand(s, c.id, h.id)


def test_question_waiting_for_our_agent(cs):  # noqa: F811
    s, _, p = cs
    c, _ = fwd(s, p["G-STAFF"], "a")
    h = hop(s, c)
    s.add(ForwardEvent(call_id=c.id, ext_key="c", kind="event", type="created", actor="PBX", at=at(11),
                       seen_at=at(11), hop_id=h.id))
    s.add(ForwardEvent(call_id=c.id, ext_key="q", kind="followup", type="followup", note="سؤال؟", actor="خالد",
                       at=at(13), seen_at=at(13), hop_id=h.id))
    s.commit()
    assert page(s, at(14))["attention"]["questions"] == 1
    s.add(ForwardMessage(call_id=c.id, kind="answer", text="نعم", created_at=at(15), status="sent"))
    s.commit()
    assert page(s, at(16))["attention"]["questions"] == 0


def test_repeat_customer(cs):  # noqa: F811
    s, _, p = cs
    old, _ = fwd(s, p["G-STAFF"], "a", t=at(11) - timedelta(days=40), number="966501234567")
    mid, _ = fwd(s, p["G-STAFF"], "b", t=at(11) - timedelta(days=5), number="0501234567")
    new, _ = fwd(s, p["G-STAFF"], "c", t=at(11), number="+966 50 123 4567")
    other, _ = fwd(s, p["G-STAFF"], "d", t=at(11, 30), number="0559999999")
    got = {i["call_id"]: i["repeat"] for i in page(s, at(12))["items"]}
    assert got == {new.id: 1, other.id: 0}                       # the 40-day-old one is outside the 30 days


def test_api_need_and_closed_by_hand(fwd_env):  # noqa: F811
    client, *_ = fwd_env
    from test_agent_supervisor_api import login, switch_on
    switch_on(client)
    login(client, "ali")
    assert client.post("/api/forwards/123456/closed-by-hand", json={"hop_id": 1}).status_code == 403
    assert client.get("/api/forwards?need=late").status_code == 403
    login(client, "root")
    r = client.get("/api/forwards?need=late")
    assert r.status_code == 200 and set(r.json()["attention"]) == set(fr.NEEDS)
    assert client.get("/api/forwards?need=nope").status_code == 422
    r = client.post("/api/forwards/123456/closed-by-hand", json={"hop_id": 1})
    assert r.status_code == 409



def test_failed_send_then_a_note_is_handled(cs):  # noqa: F811
    from callsight.db.models import CallFollowupNote
    s, _, p = cs
    c, f = fwd(s, p["G-STAFF"], "a", status="failed", err="x")
    assert page(s, at(12))["attention"]["send"] == 1
    s.add(CallFollowupNote(call_id=c.id, round=2, what_happened="حُلّت", solved=True, created_by=f.filled_by,
                           created_at=at(12)))
    s.commit()
    assert page(s, at(13))["attention"]["send"] == 0


def test_supervisor_closed_waits_for_our_check_not_late(cs):  # noqa: F811
    s, _, p = cs
    c, _ = fwd(s, p["G-SALEH"], "a")
    h = hop(s, c)
    h.closed_at = at(15)
    s.commit()
    line = page(s, at(11) + 25 * H)["items"][0]
    assert "late" not in line["needs"] and line["waiting_check"]
    assert datetime.fromisoformat(line["due"]) == at(11) + 48 * H


def test_nobody_to_pass_to_is_send_and_shows_why(cs):  # noqa: F811
    s, _, p = cs
    c, _ = fwd(s, p["G-STAFF"], "a")
    hop(s, c).error = forward.ERR_NO_NEXT
    s.commit()
    line = page(s, at(11) + 25 * H, need="send")["items"][0]
    assert line["hop_error"] == forward.ERR_NO_NEXT and {"late", "send"} <= set(line["needs"])


def test_close_with_nothing_left_there_is_not_asked(cs):  # noqa: F811
    s, _, p = cs
    c, _ = fwd(s, p["G-STAFF"], "a")
    h = hop(s, c)
    h.result, h.ended_at, h.erp_close, h.error = "solved", at(12), "failed", forward.ERR_ENDED
    s.commit()
    assert page(s, at(13))["attention"]["erp_close"] == 0


def test_need_list_ignores_state_and_level_filters(cs):  # noqa: F811
    s, _, p = cs
    fwd(s, p["G-STAFF"], "a", status="failed", err="x")
    frm = day(at(11))
    out = fr.listing(s, frm, frm, need="send", state="open", level=2, now=at(12))
    assert len(out["items"]) == out["attention"]["send"] == 1
