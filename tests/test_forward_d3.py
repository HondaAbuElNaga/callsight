"""Spec 2026-11-16-priority-no-followup, part 4 D3: questions from the person holding the forwarded problem, the
agent's answer / notes / the call's AI summary written on the ticket, the tracker (req 17). The other system is
faked: no real call."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from callsight import followups as fu
from callsight import forward
from callsight.cs_api import CsAuthError, CsError, CsUnavailable
from callsight.db.models import AdminLog, Agent, CallAnalysis, CallFollowup, ForwardHop, ForwardMessage, ForwardPerson
from test_agent_supervisor_api import env, login, switch_on  # noqa: F401
from test_api import engine  # noqa: F401
from test_followups import DAY, at, mk, rows, s, start_on, user  # noqa: F401
from test_forward import fwd_env  # noqa: F401
from test_forward_d2 import TICKET_OPEN, fs, hop, sent_form, sup_call  # noqa: F401

QUESTION = {"kind": "followup", "type": "followup", "note": "هل العميل في الرياض؟", "at": "2026-11-10T12:00:00",
            "actorName": "صالح"}
OUR_TEXT = {"kind": "followup", "type": "followup", "note": "رد موظف خدمة العملاء «ali»: نعم", "at":
            "2026-11-10T12:30:00", "actorName": "PBX"}


def ticket(*items, closed=None):
    return {"ticket": {"number": 1631, "closedAt": closed}, "timeline": [*TICKET_OPEN["timeline"], *items]}


class Fake:
    """The ticket API: get_ticket answers `answer`; followup() writes are recorded (or raise `fail`)."""

    def __init__(self, answer=None, fail=None):
        self.answer, self.fail, self.wrote = answer or ticket(), fail, []

    def get_ticket(self, guid):
        return self.answer

    def followup(self, guid, note, close=False):
        if self.fail:
            raise self.fail
        self.wrote.append((guid, note, close))
        return {}


def read(s, f, fake, now):
    forward.store_ticket(s, hop(s, f), fake.get_ticket("T-1"), now)
    s.commit()


def one(s, c, now):
    r = fu.one(s, c, ["104", "119", "116"], DAY)
    return r, r.as_dict(now)


def msg(s, c, kind="answer", text_="نعم في الرياض", at_=None, status="pending", by=None):
    m = ForwardMessage(call_id=c.id, kind=kind, text=text_, created_by=by, created_at=at_ or at(13), status=status)
    s.add(m)
    s.commit()
    return m


# ---------- questions ----------

def test_a_followup_from_the_holder_is_a_question_ours_is_not(fs):
    s, path = fs
    c, f = sent_form(s, path)
    read(s, f, Fake(ticket(QUESTION, OUR_TEXT)), at(12, 40))
    r, d = one(s, c, at(12, 45))
    assert [q["text"] for q in d["forward"]["questions"]] == ["هل العميل في الرياض؟"]
    assert d["forward"]["questions"][0]["from"] == "صالح"


def test_an_answer_or_seen_closes_the_questions_shown_before_it(fs):
    s, path = fs
    c, f = sent_form(s, path)
    read(s, f, Fake(ticket(QUESTION)), at(12, 40))
    msg(s, c, at_=at(12, 50))
    assert one(s, c, at(13))[1]["forward"]["questions"] == []
    later = {**QUESTION, "note": "وما رقم الطلب؟", "at": "2026-11-10T13:10:00"}
    read(s, f, Fake(ticket(QUESTION, later)), at(13, 15))     # a new question after the answer
    assert [q["text"] for q in one(s, c, at(13, 20))[1]["forward"]["questions"]] == ["وما رقم الطلب؟"]
    msg(s, c, kind="seen", text_=None, at_=at(13, 30), status="done")
    assert one(s, c, at(13, 40))[1]["forward"]["questions"] == []


def test_no_question_once_the_chain_is_solved(fs):
    s, path = fs
    c, f = sent_form(s, path)
    read(s, f, Fake(ticket(QUESTION)), at(12, 40))
    h = hop(s, f)
    h.result, h.ended_at = "solved", at(14)
    s.commit()
    assert one(s, c, at(15))[1]["forward"]["questions"] == []


def test_unsolved_puts_a_question_first(fs):
    s, path = fs
    c, f = sent_form(s, path)
    other = mk(s, "b", at(9), number="0509999999")
    u = s.scalar(select(CallFollowup.filled_by).where(CallFollowup.call_id == c.id))
    s.add(CallFollowup(call_id=other.id, owner_ext="119", what_happened="y", solved=False, priority="medium",
                       responsible_kind="customer", callback_at=at(9, 30), expected_at=at(20), filled_by=u,
                       first_filled_at=at(9, 5), updated_by=u, updated_at=at(9, 5)))   # callback missed: urgent
    s.commit()
    read(s, f, Fake(ticket(QUESTION)), at(12, 40))
    got, _ = fu.unsolved(s, ["119"], DAY, at(13), callers=["104", "119", "116"])
    assert [r.call.uid for r in got] == ["a", "b"]


# ---------- writing on the ticket ----------

def test_answer_is_written_on_the_open_ticket_with_the_prefix(fs):
    s, path = fs
    c, f = sent_form(s, path)
    u = s.scalar(select(CallFollowup.filled_by).where(CallFollowup.call_id == c.id))
    s.merge(Agent(ext="119", name="علي سالم", team="customer_service", in_team=True))
    s.commit()
    m = msg(s, c, by=u)
    fake = Fake()
    done = forward.send_messages(s, fake, now=at(13, 5))
    assert done["msg_sent"] == 1
    assert fake.wrote == [("T-1", "رد موظف خدمة العملاء «علي سالم»: نعم في الرياض", False)]   # never the login name
    s.refresh(m)
    assert m.status == "sent" and m.sent_at == at(13, 5)
    assert forward.send_messages(s, fake, now=at(13, 10))["msg_sent"] == 0     # never twice


def test_a_closed_ticket_waits_then_is_written_after_the_reopen(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    h.closed_at = at(12)
    s.commit()
    m = msg(s, c)
    fake = Fake()
    forward.send_messages(s, fake, now=at(13, 5))
    s.refresh(m)
    assert m.status == "waiting" and fake.wrote == []
    h.closed_at = None                                  # the chain reopened it
    s.commit()
    forward.send_messages(s, fake, now=at(14))
    s.refresh(m)
    assert m.status == "sent" and len(fake.wrote) == 1


def test_waiting_too_long_or_chain_ended_fails_with_a_reason(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    h.closed_at = at(12)
    s.commit()
    m = msg(s, c)
    forward.send_messages(s, Fake(), now=at(13) + forward.MSG_WINDOW + timedelta(minutes=1))
    s.refresh(m)
    assert m.status == "failed" and m.error == forward.ERR_MSG_EXPIRED
    h.result, h.closed_at = "solved", at(12)
    s.commit()
    m2 = msg(s, c)
    forward.send_messages(s, Fake(), now=at(13, 5))
    s.refresh(m2)
    assert m2.status == "failed" and m2.error == forward.ERR_ENDED


def test_errors_refused_retry_and_bad_key(fs):
    s, path = fs
    c, f = sent_form(s, path)
    m = msg(s, c)
    forward.send_messages(s, Fake(fail=CsUnavailable("ConnectTimeout")), now=at(13, 5))
    s.refresh(m)
    assert m.status == "pending" and m.error == forward.ERR_UNAVAILABLE and m.tries == 1
    with pytest.raises(CsAuthError):
        forward.send_messages(s, Fake(fail=CsAuthError(401, "bad key")), now=at(13, 10))
    s.refresh(m)
    assert m.status == "pending" and m.error == forward.ERR_AUTH
    forward.send_messages(s, Fake(fail=CsError(404, "التذكرة غير موجودة")), now=at(13, 15))
    s.refresh(m)
    assert m.status == "failed" and m.error == "التذكرة غير موجودة"


def test_every_open_manager_copy_gets_the_answer(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    h.result, h.ended_at = "passed_on", at(12)
    m1 = s.scalar(select(ForwardPerson.id).where(ForwardPerson.id != h.person_id).limit(1))
    s.add_all([ForwardHop(call_id=c.id, level=3, person_id=m1, ticket_guid="T-1", sent_at=at(12), result="open"),
               ForwardHop(call_id=c.id, level=3, person_id=m1, ticket_guid="COPY-1", sent_at=at(12), result="open"),
               ForwardHop(call_id=c.id, level=3, person_id=m1, result="pending")])
    s.commit()
    msg(s, c)
    fake = Fake()
    forward.send_messages(s, fake, now=at(13, 5))
    assert sorted(g for g, *_ in fake.wrote) == ["COPY-1", "T-1"]


def test_the_ai_summary_is_written_once(fs):
    s, path = fs
    c, f = sent_form(s, path)
    s.add(CallAnalysis(call_id=c.id, model="m", prompt_version="p", status="done", summary="العميل لم يستلم الشهادة"))
    s.commit()
    fake = Fake()
    assert forward.send_messages(s, fake, now=at(13))["summaries"] == 1
    assert forward.send_messages(s, fake, now=at(13, 5))["summaries"] == 0
    assert [n for _, n, _ in fake.wrote] == ["ملخص المكالمة (آلي): العميل لم يستلم الشهادة"]
    assert s.scalar(select(ForwardMessage.created_by).where(ForwardMessage.kind == "summary")) is None


def test_a_note_on_a_forwarded_problem_goes_on_the_ticket(fs):
    s, path = fs
    c, f = sent_form(s, path, forwarded=at(11))
    u = s.get(type(user(s, "sara", "agent", "119")), f.filled_by)
    fu.add_note(s, c, {"round": 2, "what_happened": "العميل أرسل الإيصال", "solved": False,
                       "responsible_kind": "forward", "expected_at": at(15)}, u, ["104", "119", "116"], now=at(12))
    m = s.scalars(select(ForwardMessage).where(ForwardMessage.call_id == c.id)).one()
    assert (m.kind, m.text, m.status, m.created_by) == ("note", "العميل أرسل الإيصال", "pending", u.id)


# ---------- tracker (req 17) ----------

def test_tracker_shows_each_step_in_order(fs):
    s, path = fs
    c, f = sent_form(s, path)
    read(s, f, Fake(ticket(QUESTION)), at(12, 40))
    sup_call(s, "sup-1", at(13), talk=90, number=c.customer_number, score=9)
    msg(s, c, at_=at(12, 50), status="sent")
    tr = one(s, c, at(13, 30))[1]["forward"]["tracker"]
    assert [(i["kind"], i["state"]) for i in tr] == [
        ("step", "done"),          # سُجّلت
        ("step", "done"),          # حُوّلت
        ("step", "done"),          # استلمها: his question is his first action
        ("question", "done"),
        ("answer", "done"),
        ("call", "done"),
        ("step", "current")]       # حُلّت ✓ (not yet)
    assert datetime.fromisoformat(tr[2]["at"]) == at(12) and "صالح" in tr[2]["label"]
    assert tr[5]["detail"] == "ردّ العميل · 90 ث · الرضا 9/10"


def test_tracker_waiting_step_turns_late_after_24h(fs):
    s, path = fs
    c, f = sent_form(s, path)
    read(s, f, Fake(), at(11, 30))
    tr = one(s, c, at(12, day=DAY + timedelta(days=1)))[1]["forward"]["tracker"]
    cur = [i for i in tr if i["state"] in ("current", "late")]
    assert len(cur) == 1 and cur[0]["state"] == "late" and cur[0]["label"].startswith("استلمها")
    assert cur[0]["detail"] == "متأخر" and tr[-1]["label"] == "حُلّت ✓" and tr[-1]["state"] == "future"


def test_tracker_solved_and_passed_on(fs):
    s, path = fs
    c, f = sent_form(s, path)
    h = hop(s, f)
    h.result, h.ended_at = "passed_on", at(11, day=DAY + timedelta(days=1))
    m1 = s.scalar(select(ForwardPerson.id).where(ForwardPerson.id != h.person_id).limit(1))
    s.add(ForwardHop(call_id=c.id, level=3, person_id=m1, ticket_guid="T-1", sent_at=h.ended_at,
                     closed_at=h.ended_at + timedelta(hours=1), solution="أُرسلت الشهادة", result="solved",
                     ended_at=h.ended_at + timedelta(hours=1)))
    s.commit()
    tr = one(s, c, at(15, day=DAY + timedelta(days=1)))[1]["forward"]["tracker"]
    labels = [i["label"] for i in tr]
    assert any(x.startswith("لم تُحل خلال 24 ساعة") for x in labels)
    assert labels[-1].startswith("حلّها") and tr[-1]["detail"] == "أُرسلت الشهادة" and tr[-1]["state"] == "done"
    assert "حُلّت ✓" not in labels


def test_tracker_before_the_ticket_and_after_a_failed_send(fs):
    s, path = fs
    c, f = sent_form(s, path)
    f.forward_status = "pending"
    s.commit()
    tr = one(s, c, at(12))[1]["forward"]["tracker"]
    assert [i["state"] for i in tr] == ["done", "current", "future"]
    f.forward_status, f.forward_error = "failed", "الرقم غير صحيح"
    s.commit()
    tr = one(s, c, at(12))[1]["forward"]["tracker"]
    assert tr[-1]["state"] == "bad" and tr[-1]["detail"] == "الرقم غير صحيح"


# ---------- API ----------

def test_api_agent_answers_a_question(fwd_env):
    client, Session, ids, sends, _ = fwd_env
    switch_on(client)
    now = datetime.now(timezone.utc)
    with Session() as s:
        s.execute(text("TRUNCATE forward_message, forward_event, forward_hop RESTART IDENTITY CASCADE"))
        pid = s.scalar(select(ForwardPerson.id).where(ForwardPerson.ext == "101"))
        u = s.scalar(text("SELECT id FROM app_user WHERE username = :n"), {"n": "ali"})
        s.add(CallFollowup(call_id=ids["own"], owner_ext="119", what_happened="x", solved=False, priority="low",
                           responsible_kind="forward", forward_person_id=pid, forward_status="sent",
                           forward_ticket_guid="T", forwarded_at=now - timedelta(minutes=20),
                           expected_at=now + timedelta(hours=2), filled_by=u,
                           first_filled_at=now - timedelta(minutes=20), updated_by=u,
                           updated_at=now - timedelta(minutes=20)))
        h = ForwardHop(call_id=ids["own"], level=2, person_id=pid, ticket_guid="T",
                       sent_at=now - timedelta(minutes=20), result="open")
        s.add(h)
        s.commit()
    url = f"/api/calls/{ids['own']}/forward/messages"
    login(client, "ali")
    assert client.post(url, json={"kind": "seen"}).status_code == 409          # nothing asked yet
    with Session() as s:
        forward.store_ticket(s, s.get(ForwardHop, h.id), ticket(QUESTION), now)
        s.commit()
    d = client.get("/api/followups").json()
    assert d["counts"]["questions"] == 1
    assert [i["call_id"] for i in client.get("/api/followups", params={"state": "questions"}).json()["items"]] \
        == [ids["own"]]
    assert client.post(url, json={"kind": "answer", "text": "  "}).status_code == 422
    assert client.post(url, json={"kind": "answer", "text": "x" * 501}).status_code == 422
    assert client.post(url, json={"kind": "other", "text": "x"}).status_code == 422
    r = client.post(url, json={"kind": "answer", "text": "نعم في الرياض"})
    assert r.status_code == 200, r.text
    assert r.json()["forward"]["questions"] == [] and sends
    assert r.json()["forward"]["messages"][0]["status"] == "pending"
    assert client.get("/api/followups").json()["counts"]["questions"] == 0
    with Session() as s:
        log = s.scalars(select(AdminLog).where(AdminLog.action == "forward_answer")).one()
        assert log.target == f"call:{ids['own']}"
    login(client, "root")
    assert client.post(url, json={"kind": "answer", "text": "x"}).status_code == 403     # supervisors watch


# ---------- review fixes ----------

def test_our_text_without_diacritics_is_still_ours(fs):
    s, path = fs
    c, f = sent_form(s, path)
    stripped = {**QUESTION, "note": "حلت عند «مدير أول»", "actorName": "someone"}       # "حُلّت عند" without marks
    read(s, f, Fake(ticket(stripped)), at(12, 40))
    assert one(s, c, at(13))[1]["forward"]["questions"] == []


def test_a_supervisor_note_is_not_written_on_the_ticket(fs):
    s, path = fs
    c, f = sent_form(s, path, forwarded=at(11))
    boss = user(s, "boss", "supervisor", None)
    fu.add_note(s, c, {"round": 2, "what_happened": "ملاحظة داخلية", "solved": False,
                       "responsible_kind": "forward", "expected_at": at(15)}, boss, ["104", "119", "116"], now=at(12))
    assert s.scalars(select(ForwardMessage).where(ForwardMessage.call_id == c.id)).all() == []


def test_the_summary_text_is_not_repeated_in_the_panel(fs):
    s, path = fs
    c, f = sent_form(s, path)
    msg(s, c, kind="summary", text_="نص الملخص", status="sent")
    fwd = one(s, c, at(13))[1]["forward"]
    assert fwd["messages"][0]["text"] is None
    assert all(i["detail"] is None for i in fwd["tracker"] if i["kind"] == "summary")


def test_a_call_being_erased_is_skipped_this_run(fs, engine):  # noqa: F811
    s, path = fs
    c, f = sent_form(s, path)
    m = msg(s, c)
    from sqlalchemy.orm import Session as S
    with S(engine) as other:                    # the erase holds the call row
        other.get(type(c), c.id, with_for_update=True)
        fake = Fake()
        forward.send_messages(s, fake, now=at(13, 5))
        assert fake.wrote == []
        other.rollback()
    s.refresh(m)
    assert m.status == "pending"


def test_api_question_counter_ignores_the_priority_filter(fwd_env):
    client, Session, ids, sends, _ = fwd_env
    switch_on(client)
    now = datetime.now(timezone.utc)
    with Session() as s:
        s.execute(text("TRUNCATE forward_message, forward_event, forward_hop RESTART IDENTITY CASCADE"))
        pid = s.scalar(select(ForwardPerson.id).where(ForwardPerson.ext == "101"))
        u = s.scalar(text("SELECT id FROM app_user WHERE username = :n"), {"n": "ali"})
        s.add(CallFollowup(call_id=ids["own"], owner_ext="119", what_happened="x", solved=False, priority="low",
                           responsible_kind="forward", forward_person_id=pid, forward_status="sent",
                           forward_ticket_guid="T", forwarded_at=now - timedelta(minutes=20),
                           expected_at=now + timedelta(hours=2), filled_by=u,
                           first_filled_at=now - timedelta(minutes=20), updated_by=u,
                           updated_at=now - timedelta(minutes=20)))
        h = ForwardHop(call_id=ids["own"], level=2, person_id=pid, ticket_guid="T",
                       sent_at=now - timedelta(minutes=20), result="open")
        s.add(h)
        s.commit()
        forward.store_ticket(s, h, ticket(QUESTION), now)
        s.commit()
    login(client, "ali")
    d = client.get("/api/followups", params={"priority": "high"}).json()
    assert d["counts"]["questions"] == 1 and d["unsolved"] == []
    url = f"/api/calls/{ids['own']}/forward/messages"
    assert client.post(url, json={"kind": "seen"}).status_code == 200
