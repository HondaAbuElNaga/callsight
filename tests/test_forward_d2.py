"""Spec 2026-11-16-priority-no-followup, part 4 D2: ticket status back, the supervisors' calls kept as proof, the
solved check (closed + last call answered, > 60 s, score > 7). The other system is faked: no real call."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from callsight import followups as fu
from callsight import forward
from callsight.config import RIYADH
from callsight.cs_api import CsAuthError, CsError, CsUnavailable
from callsight.db.models import Call, CallAnalysis, CallFollowup, ForwardEvent, ForwardPerson
from callsight.ingest.classify import FORWARD_TEAM, TeamConfig, is_forward_call
from callsight.ingest.service import ingest_rows
from test_agent_supervisor_api import env, login, switch_on  # noqa: F401
from test_api import engine  # noqa: F401
from test_followups import DAY, at, mk, rows, s, start_on, user  # noqa: F401
from test_forward import YAML, fwd_env, linked  # noqa: F401

TEAM = TeamConfig(ext_team={"104": "customer_service", "119": "customer_service", "116": "after_sales"})


@pytest.fixture
def fs(s, tmp_path):  # noqa: F811
    s.execute(text("TRUNCATE forward_person, forward_event RESTART IDENTITY CASCADE"))
    s.flush()
    p = tmp_path / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    return s, p


def sent_form(s, path, *, forwarded=None, number="0501234567"):
    """A problem forwarded to the supervisor with ext 101, ticket made."""
    start_on(s)
    people = linked(s, path)
    u = user(s)
    c = mk(s, "a", at(10), number=number)
    fwd = forwarded or at(11)
    f = CallFollowup(call_id=c.id, owner_ext="119", what_happened="x", solved=False, priority="medium",
                     responsible_kind="forward", forward_person_id=people["صالح الغامدي"].id, forward_status="sent",
                     forward_ticket_guid="T-1", forward_ticket_number=1631, forwarded_at=fwd,
                     expected_at=fwd + timedelta(hours=5), filled_by=u.id, first_filled_at=fwd, updated_by=u.id,
                     updated_at=fwd)
    s.add(f)
    s.commit()
    return c, f


def sup_call(s, uid, start, *, status="answered", talk=90, ext="101", number="0501234567", score=None):
    c = Call(uid=uid, direction="outbound", entry="direct", customer_number=number, team=FORWARD_TEAM, agent_ext=ext,
             start_time=start, end_time=start + timedelta(seconds=talk + 5), talk_seconds=talk,
             total_seconds=talk + 5, status=status, recording_status="none", wait_seconds=5)
    s.add(c)
    s.flush()
    if score is not None:
        s.add(CallAnalysis(call_id=c.id, model="m", prompt_version="p", status="done", llm_score=score))
    s.commit()
    return c


TICKET_OPEN = {"ticket": {"number": 1631, "closedAt": None}, "timeline": [
    {"kind": "event", "type": "created", "note": "", "at": "2026-11-10T11:00:00", "actorName": "PBX"},
    {"kind": "event", "type": "forwarded", "note": "", "at": "2026-11-10T11:00:01", "actorName": "PBX",
     "targetName": "صالح"}]}
TICKET_CLOSED = {"ticket": {"number": 1631, "closedAt": "2026-11-10T15:00:00"}, "timeline": [
    *TICKET_OPEN["timeline"],
    {"kind": "followup", "type": "followup", "note": "هل العميل في الرياض؟", "at": "2026-11-10T12:00:00",
     "actorName": "صالح"},
    {"kind": "followup", "type": "followup_close", "note": "تم إرسال الشهادة", "at": "2026-11-10T15:00:00",
     "actorName": "صالح"}]}


class FakePoll:
    def __init__(self, answer=TICKET_OPEN, fail=None):
        self.answer, self.fail, self.asked = answer, fail, []

    def get_ticket(self, guid):
        self.asked.append(guid)
        if self.fail:
            raise self.fail
        return self.answer


# ---------- reading the ticket back ----------

def test_parse_time():
    assert forward.parse_time("2026-11-10T15:00:00") == datetime(2026, 11, 10, 15, tzinfo=RIYADH)   # Saudi time
    assert forward.parse_time("2026-11-10T12:00:00Z") == datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    assert forward.parse_time("x") is None and forward.parse_time(None) is None and forward.parse_time(5) is None


def test_poll_stores_close_solution_and_new_items_once(fs):
    s, path = fs
    c, f = sent_form(s, path)
    cs = FakePoll(TICKET_OPEN)
    assert forward.poll_tickets(s, cs, now=at(12)) == {"read": 1, "new_items": 2, "missing": 0}
    assert cs.asked == ["T-1"]
    s.refresh(f)
    assert f.forward_closed_at is None and f.forward_checked_at is not None
    cs.answer = TICKET_CLOSED
    assert forward.poll_tickets(s, cs, now=at(16))["new_items"] == 2         # only the 2 new items
    s.refresh(f)
    assert f.forward_closed_at == datetime(2026, 11, 10, 15, tzinfo=RIYADH)
    assert f.forward_solution == "تم إرسال الشهادة"
    ev = list(s.scalars(select(ForwardEvent).where(ForwardEvent.call_id == c.id).order_by(ForwardEvent.id)))
    assert [e.type for e in ev] == ["created", "forwarded", "followup", "followup_close"]
    assert ev[2].actor == "صالح" and ev[2].note == "هل العميل في الرياض؟"
    cs.answer = {"ticket": {"closedAt": None}, "timeline": TICKET_CLOSED["timeline"] + [
        {"kind": "event", "type": "reopened", "at": "2026-11-10T17:00:00", "actorName": "x"}]}
    forward.poll_tickets(s, cs, now=at(18))
    s.refresh(f)
    assert f.forward_closed_at is None                                    # reopened there


def test_poll_only_sent_recent_and_not_erased(fs):
    s, path = fs
    c, f = sent_form(s, path)
    cs = FakePoll()
    forward.poll_tickets(s, cs, now=f.forwarded_at + timedelta(days=31))
    assert cs.asked == []
    c.content_deleted_at = at(12)
    s.commit()
    forward.poll_tickets(s, cs, now=at(12))
    assert cs.asked == []


def test_poll_errors(fs):
    s, path = fs
    c, f = sent_form(s, path)
    forward.poll_tickets(s, FakePoll(fail=CsError(404, "التذكرة غير موجودة")), now=at(12))
    s.refresh(f)
    assert f.forward_error == "التذكرة غير موجودة" and f.forward_status == "sent"
    assert forward.poll_tickets(s, FakePoll(fail=CsUnavailable("x")), now=at(12))["read"] == 0
    with pytest.raises(CsAuthError):
        forward.poll_tickets(s, FakePoll(fail=CsAuthError(401, "x")), now=at(12))


# ---------- the supervisors' calls (ingest) ----------

def leg(uid, ts, *, frm="101", to="0501234567", call_type="Outbound", disp="ANSWERED", talk=90, lid=1):
    return {"id": lid, "uid": uid, "timestamp": ts, "call_type": call_type, "call_from": f"Ext {frm}<{frm}>",
            "call_from_number": frm, "call_to": to, "call_to_number": to, "disposition": disp, "duration": talk + 5,
            "ring_duration": 5, "talk_duration": talk, "reason": "dst hangup", "record_file": f"{uid}.wav"}


def test_is_forward_call():
    since = at(11)
    watch = {"101": [("501234567", since)]}
    ts = int((since + timedelta(minutes=5)).timestamp())
    assert is_forward_call([leg("u", ts)], watch)
    assert is_forward_call([leg("u", ts, to="+966501234567")], watch)               # any number format
    assert not is_forward_call([leg("u", int((since - timedelta(minutes=5)).timestamp()))], watch)   # before
    assert not is_forward_call([leg("u", ts, frm="102")], watch)
    assert not is_forward_call([leg("u", ts, to="0559999999")], watch)
    assert not is_forward_call([leg("u", ts, call_type="Inbound")], watch)
    assert not is_forward_call([leg("u", ts)], {})


def test_ingest_keeps_a_watched_supervisor_call_as_team_forward(fs):
    s, path = fs
    c, f = sent_form(s, path, forwarded=datetime.now(timezone.utc) - timedelta(hours=1))
    watch = forward.watch_list(s)
    assert watch == {"101": [("501234567", f.forwarded_at)]}
    ts = int(datetime.now(timezone.utc).timestamp())
    r = ingest_rows(s, [leg("sup-1", ts), leg("other", ts, frm="102", lid=2)], TEAM, watch)
    s.commit()
    assert (r.calls, r.skipped_calls) == (1, 1)
    got = s.scalars(select(Call).where(Call.uid == "sup-1")).one()
    assert got.team == FORWARD_TEAM and got.agent_ext == "101" and got.recording_status == "pending"
    assert ingest_rows(s, [leg("sup-2", ts, lid=3)], TEAM, None).calls == 0        # no watch: skipped


# ---------- proof + steps ----------

@pytest.mark.parametrize("calls,ok,reason", [
    ([], False, "no_call"),
    ([("answered", 90, 8)], True, None),
    ([("answered", 90, 7)], False, "low_score"),
    ([("answered", 60, 9)], False, "short"),                       # MORE than 60 s
    ([("no_answer", 0, None)], False, "not_answered"),
    ([("answered", 90, None)], False, "waiting_score"),
    ([("answered", 90, 9), ("no_answer", 0, None)], False, "not_answered"),   # the last call decides
    ([("no_answer", 0, None), ("answered", 120, 10)], True, None),
])
def test_proof_rules(fs, calls, ok, reason):
    s, path = fs
    c, f = sent_form(s, path)
    f.forward_closed_at = at(15)
    s.commit()
    for n, (st, talk, score) in enumerate(calls):
        sup_call(s, f"p{n}", at(12, n * 10), status=st, talk=talk, score=score)
    d = rows(s, at(16))["a"]
    assert d["forward"]["proof"]["ok"] is ok and d["forward"]["proof"]["reason"] == reason
    assert d["step"] == ("forward_solved" if ok else "forward_unconfirmed")
    assert len(d["forward"]["proof"]["calls"]) == len(calls)


def test_steps_and_unsolved(fs):
    s, path = fs
    c, f = sent_form(s, path)
    sup_call(s, "before", at(10, 30), score=9)                     # before the forward: never counts
    sup_call(s, "other-ext", at(12), ext="102", score=9)           # another supervisor
    assert rows(s, at(12))["a"]["step"] == "forwarded"             # not closed yet
    sup_call(s, "good", at(13), score=9)
    assert rows(s, at(13, 30))["a"]["forward"]["proof"]["ok"] is True
    assert rows(s, at(13, 30))["a"]["step"] == "forwarded"         # a good call alone does not close it
    f.forward_closed_at = at(14)
    s.commit()
    assert rows(s, at(15))["a"]["step"] == "forward_solved"
    got, _ = fu.unsolved(s, ["119"], DAY, at(15), callers=["119"])
    assert got == []                                               # solved: leaves "تحتاج حل"
    f.forward_closed_at = None                                     # reopened there
    s.commit()
    assert rows(s, at(15))["a"]["step"] == "forwarded"


def test_supervisor_without_extension_is_never_confirmed(fs):
    s, path = fs
    c, f = sent_form(s, path)
    p = s.get(ForwardPerson, f.forward_person_id)
    p.ext = None
    f.forward_closed_at = at(14)
    s.commit()
    d = rows(s, at(15))["a"]
    assert d["step"] == "forward_unconfirmed" and d["forward"]["proof"]["reason"] == "no_ext"


# ---------- API ----------

def test_api_hides_supervisor_calls_and_counts_solved_today(fwd_env):
    client, Session, ids, *_ = fwd_env
    switch_on(client)
    now = datetime.now(timezone.utc)
    with Session() as s:
        own = s.get(Call, ids["own"])
        sup = sup_call(s, "sup-x", now - timedelta(minutes=5), number=own.customer_number, score=9)
        pid = s.scalar(select(ForwardPerson.id).where(ForwardPerson.ext == "101"))
        u = s.scalar(text("SELECT id FROM app_user WHERE username = :n"), {"n": "ali"})
        s.add(CallFollowup(call_id=own.id, owner_ext="119", what_happened="x", solved=False, priority="low",
                           responsible_kind="forward", forward_person_id=pid, forward_status="sent",
                           forward_ticket_guid="T", forwarded_at=now - timedelta(minutes=20),
                           forward_closed_at=now - timedelta(minutes=1), expected_at=now + timedelta(hours=2),
                           filled_by=u, first_filled_at=now - timedelta(minutes=20), updated_by=u,
                           updated_at=now - timedelta(minutes=20)))
        s.commit()
        sup_id = sup.id
    login(client, "root")
    day = datetime.now(RIYADH).date().isoformat()
    lst = client.get("/api/calls", params={"from": day, "to": day}).json()
    assert sup_id not in {i["id"] for i in lst["items"]}
    assert client.get(f"/api/calls/{sup_id}").status_code == 404
    login(client, "ali")
    d = client.get("/api/followups", params={"state": "forward_solved_today"}).json()
    assert d["counts"]["forward_solved_today"] == 1 and [i["call_id"] for i in d["items"]] == [ids["own"]]
    assert d["items"][0]["step"] == "forward_solved" and d["unsolved"] == []
    solved = client.get("/api/followups", params={"state": "solved"}).json()
    assert [i["call_id"] for i in solved["items"]] == [ids["own"]]
