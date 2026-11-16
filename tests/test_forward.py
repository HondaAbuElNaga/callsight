"""Spec 2026-11-16-priority-no-followup, part 4 D1: forward a not-solved problem to a branch supervisor in the
customer-service system. The other system is always faked here (httpx MockTransport / a fake client): no real call."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from callsight import followups as fu
from callsight import forward
from callsight.api.main import ApiConfig, create_app
from callsight.cs_api import CsAuthError, CsClient, CsConfig, CsError, CsUnavailable, employees, supervisors
from callsight.db.models import Call, CallFollowup, ForwardPerson
from test_agent_supervisor_api import env, login, switch_on  # noqa: F401
from test_api import SECRET, TEAM, engine  # noqa: F401
from test_followups import DAY, at, mk, rows, s, start_on, user  # noqa: F401

CFG = CsConfig(url="https://cs.example/api/customer-service", key="sk_test", user_guid="PBX-GUID")
YAML = """people:
  - {branch: "فرع الزهور", name: "صالح الغامدي", ext: "101", guid: null}
  - {branch: "حفر الباطن حي الواحه", name: "السيد عبدالمنعم", ext: "401"}
  - {branch: "المدينة المنورة", name: "شريف", ext: "501", guid: "G-FIXED"}
"""
TARGETS = {"total": 5, "groups": [
    {"key": "men", "branches": [
        {"guid": "B1", "name": "رجال الزهور", "employees": [
            {"guid": "G-SALEH", "name": "صالح محمد الغامدي", "isBranchSupervisor": True},
            {"guid": "G-STAFF", "name": "صالح أحمد الغامدي", "isBranchSupervisor": False}]},
        {"guid": "B2", "name": "رجال الواحة", "employees": [
            {"guid": "G-SAYED", "name": "السيد عبد المنعم علي", "isBranchSupervisor": True}]},
        {"guid": "B3", "name": "رجال المدينة", "employees": [
            {"guid": "G-FIXED", "name": "شريف حسن", "isBranchSupervisor": True}]}]}]}


@pytest.fixture
def fs(s, tmp_path):  # noqa: F811
    s.execute(text("TRUNCATE forward_person RESTART IDENTITY CASCADE"))
    s.flush()
    p = tmp_path / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    return s, p


class FakeTargets:
    def __init__(self, targets=TARGETS):
        self.targets = targets

    def forward_targets(self):
        return self.targets


# ---------- client (MockTransport) ----------

def mock_client(handler) -> CsClient:
    return CsClient(CFG, transport=httpx.MockTransport(handler))


def test_client_sends_key_and_user_guid_and_maps_errors():
    seen = []

    def ok(req: httpx.Request):
        seen.append(req)
        if req.url.path.endswith("/customers/by-phone"):
            return httpx.Response(200, json={"found": False, "created": True, "accountGuid": "ACC"})
        return httpx.Response(200, json={"guid": "T-1", "number": 1631, "message": "تم إنشاء التذكرة."})

    with mock_client(ok) as c:
        assert c.customer_by_phone("0501234567") == "ACC"
        assert c.create_ticket("ACC", "نص", 3, "G-SALEH") == ("T-1", 1631)
    assert all(r.headers["X-Api-Key"] == "sk_test" for r in seen)
    import json
    body = json.loads(seen[1].content)
    assert body == {"userGuid": "PBX-GUID", "accountGuid": "ACC", "callType": 0, "notes": "نص", "priority": 3,
                    "close": False, "forwardToGuid": "G-SALEH"}
    assert json.loads(seen[0].content)["createIfMissing"] is True

    for status, exc in ((400, CsError), (404, CsError), (401, CsAuthError), (403, CsAuthError),
                        (500, CsUnavailable), (503, CsUnavailable)):
        with mock_client(lambda req, st=status: httpx.Response(st, json={"message": "رقم جوال غير صحيح"})) as c:
            with pytest.raises(exc) as got:
                c.customer_by_phone("x")
            if exc is not CsUnavailable:
                assert got.value.message == "رقم جوال غير صحيح"

    def down(req):
        raise httpx.ConnectError("no route", request=req)
    with mock_client(down) as c, pytest.raises(CsUnavailable) as got:
        c.forward_targets()
    assert "cs.example" not in str(got.value)              # never the address / key in the error text
    with pytest.raises(ValueError):
        CsClient(CsConfig(url="http://cs.example", key="k", user_guid="u"))


def test_config_enabled_only_with_key_and_user(monkeypatch):
    monkeypatch.setenv("CS_API_KEY", "")
    monkeypatch.setenv("CS_USER_GUID", "u")
    assert CsConfig.from_env().enabled is False
    monkeypatch.setenv("CS_API_KEY", "sk")
    assert CsConfig.from_env().enabled is True
    assert CsConfig.from_env().url == "https://api4.sstli.com/api/customer-service"


def test_supervisors_only():
    got = supervisors(TARGETS)
    assert [x["guid"] for x in got] == ["G-SALEH", "G-SAYED", "G-FIXED"]
    assert supervisors({}) == []


# ---------- people: yaml + sync ----------

def test_yaml_checks(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text('people:\n  - {branch: "a", name: ""}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="needs branch and name"):
        forward.load_people_yaml(p)
    p.write_text('people:\n  - {branch: "a", name: "b"}\n  - {branch: "a", name: "b"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="twice"):
        forward.load_people_yaml(p)
    p.write_text('people:\n  - {branch: "a", name: "b", ext: "1x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bad ext"):
        forward.load_people_yaml(p)


def test_real_yaml_has_the_seven_supervisors():
    got = forward.load_people_yaml(forward.people_file())
    assert sorted(p["ext"] for p in got) == ["101", "201", "301", "401", "501", "601", "901"]


def test_match_person_by_name_words_and_branch():
    sups = supervisors(TARGETS)
    assert forward.match_person({"name": "السيد عبدالمنعم", "branch": "حفر الباطن"}, sups)[0]["guid"] == "G-SAYED"
    assert forward.match_person({"name": "نادر", "branch": "x"}, sups) == (None, "not found")
    two = sups + [{"guid": "G-2", "name": "صالح الغامدي", "branch": "رجال الرياض"}]
    assert forward.match_person({"name": "صالح الغامدي", "branch": "فرع الزهور"}, two)[0]["guid"] == "G-SALEH"
    hit, why = forward.match_person({"name": "صالح الغامدي", "branch": "جدة"}, two)
    assert hit is None and "2 people" in why                 # never a guess between two people


def test_sync_links_people_and_options(fs):
    s, path = fs
    lines = forward.sync_people(s, None, path)                 # link off: yaml only, nobody choosable yet
    assert lines == [] and forward.options(s) == [{"id": 3, "name": "شريف", "branch": "المدينة المنورة",
                                                   "ext": "501", "supervisor": True, "manager": False}]
    lines = forward.sync_people(s, FakeTargets(), path)
    assert lines[0].startswith("employees: 4 (3 branch supervisors") and sum(x.startswith("ok") for x in lines) == 3
    people = {p.name: p for p in s.scalars(select(ForwardPerson))}
    assert people["صالح الغامدي"].guid == "G-SALEH" and people["صالح الغامدي"].full_name == "صالح محمد الغامدي"
    assert people["صالح الغامدي"].branch == "رجال الزهور"            # the other system's branch (chain matches on it)
    assert people["السيد عبدالمنعم"].guid == "G-SAYED" and people["شريف"].guid == "G-FIXED"
    staff = people["صالح أحمد الغامدي"]                              # every employee can be chosen (forward chain)
    assert staff.guid == "G-STAFF" and not staff.is_supervisor and staff.ext is None
    assert len(forward.options(s)) == 4 and len(list(s.scalars(select(ForwardPerson)))) == 4   # rows reused
    # gone there -> inactive; removed from the yaml -> still an employee, no ext any more
    gone = {"groups": [{"key": "men", "branches": [{"name": "رجال الزهور", "employees": [
        {"guid": "G-SALEH", "name": "صالح محمد الغامدي", "isBranchSupervisor": True}]},
        {"name": "رجال المدينة", "employees": [{"guid": "G-FIXED", "name": "شريف حسن", "isBranchSupervisor": True}]}]}]}
    path.write_text(YAML.replace('  - {branch: "المدينة المنورة", name: "شريف", ext: "501", guid: "G-FIXED"}\n', ""),
                    encoding="utf-8")
    lines = forward.sync_people(s, FakeTargets(gone), path)
    assert any(x.startswith("NOT LINKED حفر") for x in lines) and any(x.startswith("removed") for x in lines)
    opts = {o["name"]: o for o in forward.options(s)}
    assert set(opts) == {"صالح الغامدي", "شريف حسن"} and opts["شريف حسن"]["ext"] is None


def test_sync_managers(fs):
    s, path = fs
    path.write_text(path.read_text(encoding="utf-8") + 'managers:\n  - {name: "صالح أحمد"}\n', encoding="utf-8")
    lines = forward.sync_people(s, FakeTargets(), path)
    assert "1 managers linked" in lines[0]
    m = s.scalars(select(ForwardPerson).where(ForwardPerson.guid == "G-STAFF")).one()
    assert m.is_manager and m.name == "صالح أحمد" and forward.level_of(m) == 3


def test_sync_two_lines_on_one_person_link_neither(fs):
    s, path = fs
    path.write_text('people:\n  - {branch: "a", name: "صالح"}\n  - {branch: "b", name: "صالح الغامدي"}\n',
                    encoding="utf-8")
    one = {"groups": [{"key": "men", "branches": [{"name": "رجال", "employees": [
        {"guid": "G-SALEH", "name": "صالح الغامدي", "isBranchSupervisor": True}]}]}]}
    lines = forward.sync_people(s, FakeTargets(one), path)
    assert any("same person" in x for x in lines)
    assert [(o["name"], o["ext"]) for o in forward.options(s)] == [("صالح الغامدي", None)]   # plain, no ext


# ---------- form rules + steps ----------

def linked(s, path):
    forward.sync_people(s, FakeTargets(), path)
    return {p.name: p for p in s.scalars(select(ForwardPerson))}


def fwd_data(person_id, now, **kw):
    return {"what_happened": "لم تصل الشهادة", "solved": False, "priority": "high", "responsible_kind": "forward",
            "forward_person_id": person_id, "forward_note": "  اتصل بعد العصر  ",
            "expected_at": now + timedelta(hours=5), **kw}


def test_forward_validation(fs):
    s, path = fs
    start_on(s)
    people = linked(s, path)
    pid = people["صالح الغامدي"].id
    c = mk(s, "a", at(10))
    now = at(11)
    with pytest.raises(fu.FormError, match="غير مفعّل"):
        fu.validate(s, fwd_data(pid, now), c, now)
    for bad, msg in (({"forward_person_id": None}, "اختر مشرف الفرع"), ({"forward_person_id": 999}, "اختر مشرف"),
                     ({"forward_note": "x" * 501}, "500")):
        with pytest.raises(fu.FormError, match=msg):
            fu.validate(s, fwd_data(pid, now, **bad), c, now, forward_ok=True)
    people["صالح الغامدي"].active = False
    s.flush()
    with pytest.raises(fu.FormError, match="اختر مشرف"):
        fu.validate(s, fwd_data(pid, now), c, now, forward_ok=True)
    people["صالح الغامدي"].active = True
    nonum = mk(s, "b", at(10, 5), number="")
    with pytest.raises(fu.FormError, match="رقم"):
        fu.validate(s, fwd_data(pid, now), nonum, now, forward_ok=True)
    clean = fu.validate(s, fwd_data(pid, now), c, now, forward_ok=True)
    assert clean["forward_person_id"] == pid and clean["forward_note"] == "اتصل بعد العصر"
    assert clean["responsible_kind"] == "forward"
    # other answers carry no forward fields
    other = fu.validate(s, {**fwd_data(pid, now), "responsible_kind": "customer"}, c, now, forward_ok=True)
    assert other["forward_person_id"] is None and other["forward_note"] is None
    # notes: never forward on their own; in a forwarded problem never solved and never another person
    note = {"what_happened": "x", "solved": False, "responsible_kind": "forward", "expected_at": now + timedelta(hours=3)}
    with pytest.raises(fu.FormError, match="النموذج الأول"):
        fu.validate(s, note, c, now, priority="high", forward_ok=True)
    assert fu.validate(s, note, c, now, priority="high", forwarded=True)["responsible_kind"] == "forward"
    with pytest.raises(fu.FormError, match="هو من يغلقها"):
        fu.validate(s, {**note, "solved": True}, c, now, priority="high", forwarded=True)
    with pytest.raises(fu.FormError, match="لا يتغيّر"):
        fu.validate(s, {**note, "responsible_kind": "customer"}, c, now, priority="high", forwarded=True)


def test_forward_save_steps_and_unsolved(fs):
    s, path = fs
    start_on(s)
    people = linked(s, path)
    u = user(s)
    c = mk(s, "a", at(10))
    now = at(11)
    f = fu.save(s, c, fwd_data(people["صالح الغامدي"].id, now), u, now, forward_ok=True)
    assert f.forward_status == "pending" and f.forward_tries == 0
    d = rows(s, at(12))["a"]
    assert d["step"] == "forwarded" and datetime.fromisoformat(d["solve_by"]) == now + timedelta(hours=5)
    assert d["forward"]["name"] == "صالح الغامدي" and d["forward"]["status"] == "pending"
    assert d["history"][0]["responsible"] == "صالح الغامدي"
    assert rows(s, now + timedelta(hours=7))["a"]["step"] == "forward_overdue"
    got, _ = fu.unsolved(s, ["119"], DAY, at(12), callers=["119"])
    assert [r.call.uid for r in got] == ["a"]
    # changed to the customer before it was sent: the send is dropped
    f = fu.save(s, c, {**fwd_data(None, now), "responsible_kind": "customer"}, u, now, forward_ok=True)
    assert f.forward_status is None and f.forward_person_id is None
    # sent: the form is locked
    f = fu.save(s, c, fwd_data(people["صالح الغامدي"].id, now), u, now, forward_ok=True)
    f.forward_status = "sent"
    s.commit()
    with pytest.raises(fu.FormError, match="لا يمكن تعديل"):
        fu.save(s, c, fwd_data(people["صالح الغامدي"].id, now), u, now, forward_ok=True)


def test_note_on_a_forwarded_problem(fs):
    s, path = fs
    start_on(s)
    people = linked(s, path)
    u = user(s)
    c = mk(s, "a", at(10))
    now = at(11)
    fu.save(s, c, fwd_data(people["صالح الغامدي"].id, now), u, now, forward_ok=True)
    note = {"round": 2, "what_happened": "العميل اتصل", "solved": False, "responsible_kind": "forward",
            "expected_at": now + timedelta(hours=4)}
    with pytest.raises(fu.FormError, match="هو من يغلقها"):
        fu.add_note(s, c, {**note, "solved": True}, u, ["119"], now + timedelta(minutes=5))
    s.rollback()
    n = fu.add_note(s, c, note, u, ["119"], now + timedelta(minutes=5))
    assert n.responsible_kind == "forward"
    d = rows(s, at(12))["a"]
    assert d["step"] == "forwarded"
    assert datetime.fromisoformat(d["solve_by"]) == now + timedelta(hours=5)       # the form's time, not the note's


# ---------- the ticket text ----------

def test_ticket_notes_fit_590_and_hold_the_facts(fs):
    s, _ = fs
    c = mk(s, "a", at(10))
    f = CallFollowup(call_id=c.id, owner_ext="119", what_happened="ن" * 500, solved=False, priority="high",
                     responsible_kind="forward", forward_note="م" * 500, expected_at=at(15), filled_by=1,
                     first_filled_at=at(11), updated_by=1, updated_at=at(11))
    txt = forward.ticket_notes(c, f, "علي")
    assert len(txt) <= 590
    for part in ("عالية", "0501234567", "2026-11-10 10:00", "2026-11-10 15:00", "علي (119)", str(c.id), "ما حدث: ن",
                 "ملاحظة للمسؤول: م"):
        assert part in txt
    f.what_happened, f.forward_note = "قصير", None
    txt = forward.ticket_notes(c, f, "")
    assert "ما حدث: قصير" in txt and "ملاحظة" not in txt


# ---------- the send job ----------

class FakeCs:
    def __init__(self, fail=None):
        self.fail, self.tickets = fail, []

    def customer_by_phone(self, phone):
        if self.fail:
            raise self.fail
        return "ACC-" + phone

    def create_ticket(self, account, notes, priority, forward_to):
        self.tickets.append((account, priority, forward_to, notes))
        return f"T-{len(self.tickets)}", 1630 + len(self.tickets)


def pending_form(s, path, uid="a", person="صالح الغامدي", when=None):
    start_on(s) if s.scalar(text("SELECT count(*) FROM app_setting")) == 0 else None
    people = {p.name: p for p in s.scalars(select(ForwardPerson))} or linked(s, path)
    u = s.scalar(text("SELECT id FROM app_user LIMIT 1")) or user(s).id
    c = mk(s, uid, at(10))
    now = when or datetime.now(timezone.utc)
    f = CallFollowup(call_id=c.id, owner_ext="119", what_happened="x", solved=False, priority="medium",
                     responsible_kind="forward", forward_person_id=people[person].id, forward_status="pending",
                     expected_at=now + timedelta(hours=3), filled_by=u, first_filled_at=now, updated_by=u,
                     updated_at=now)
    s.add(f)
    s.commit()
    return f


def test_send_ok(fs):
    s, path = fs
    f = pending_form(s, path)
    cs = FakeCs()
    assert forward.send_pending(s, cs) == {"sent": 1, "failed": 0, "retry": 0}
    s.refresh(f)
    assert (f.forward_status, f.forward_ticket_guid, f.forward_ticket_number) == ("sent", "T-1", 1631)
    assert f.forwarded_at is not None and f.forward_error is None
    acc, prio, to, notes = cs.tickets[0]
    assert acc == "ACC-0501234567" and prio == 2 and to == "G-SALEH" and "متوسطة" in notes
    assert forward.send_pending(s, cs) == {"sent": 0, "failed": 0, "retry": 0}       # never twice


@pytest.mark.parametrize("err,status,msg", [
    (CsError(400, "رقم جوال غير صحيح"), "failed", "رقم جوال غير صحيح"),
    (CsUnavailable("ConnectError"), "pending", forward.ERR_UNAVAILABLE),
])
def test_send_errors(fs, err, status, msg):
    s, path = fs
    f = pending_form(s, path)
    forward.send_pending(s, FakeCs(fail=err))
    s.refresh(f)
    assert (f.forward_status, f.forward_error, f.forward_tries) == (status, msg, 1)


def test_send_bad_key_stops_and_keeps_pending(fs):
    s, path = fs
    f = pending_form(s, path)
    with pytest.raises(CsAuthError):
        forward.send_pending(s, FakeCs(fail=CsAuthError(401, "x")))
    s.refresh(f)
    assert f.forward_status == "pending" and f.forward_error == forward.ERR_AUTH


def test_send_waits_for_a_link_then_gives_up_after_24h(fs):
    s, path = fs
    f = pending_form(s, path)
    p = s.get(ForwardPerson, f.forward_person_id)
    p.guid = None
    s.commit()
    forward.send_pending(s, FakeCs())
    s.refresh(f)
    assert f.forward_status == "pending" and f.forward_error == forward.ERR_NO_GUID
    forward.send_pending(s, FakeCs(), now=datetime.now(timezone.utc) + timedelta(hours=25))
    s.refresh(f)
    assert f.forward_status == "failed" and f.forward_error == forward.ERR_EXPIRED


def test_send_drops_erased_calls(fs):
    s, path = fs
    f = pending_form(s, path)
    s.get(Call, f.call_id).content_deleted_at = datetime.now(timezone.utc)
    s.commit()
    cs = FakeCs()
    forward.send_pending(s, cs)
    s.refresh(f)
    assert f.forward_status is None and cs.tickets == []


# ---------- API ----------

@pytest.fixture
def fwd_env(env, tmp_path):  # noqa: F811
    _, Session, ids, *_ = env
    p = tmp_path / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    with Session() as s:
        s.execute(text("TRUNCATE forward_person RESTART IDENTITY CASCADE"))
        s.commit()
        forward.sync_people(s, FakeTargets(), p)
    sends = []
    on = {"v": True}
    app = create_app(Session, ApiConfig(secret=SECRET, audio_dir=tmp_path, teams=lambda: TEAM, reports_dir=tmp_path,
                                        topics=lambda: {}, forward_enabled=lambda: on["v"],
                                        forward_send=lambda: sends.append(1)))
    return TestClient(app), Session, ids, sends, on


def test_api_forward_flow(fwd_env):
    client, Session, ids, sends, on = fwd_env
    switch_on(client)
    login(client, "ali")
    url = f"/api/calls/{ids['own']}/followup"
    got = client.get(url).json()
    assert got["forward_enabled"] is True
    assert {o["name"] for o in got["forward_options"]} == {"السيد عبدالمنعم", "صالح الغامدي", "شريف",
                                                          "صالح أحمد الغامدي"}
    pid = next(o["id"] for o in got["forward_options"] if o["name"] == "صالح الغامدي")
    t = datetime.now(timezone.utc) + timedelta(hours=3)
    body = {"what_happened": "لم تصل الشهادة", "solved": False, "priority": "medium", "responsible_kind": "forward",
            "forward_person_id": pid, "forward_note": "عاجل", "expected_at": t.isoformat()}
    r = client.put(url, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["step"] == "forwarded" and d["forward"]["status"] == "pending" and d["forward"]["name"] == "صالح الغامدي"
    assert d["form"]["forward_person_id"] == pid and d["form"]["forward_note"] == "عاجل"
    assert "ticket_guid" not in str(d) and sends == [1]
    lst = client.get("/api/followups", params={"state": ""}).json()
    assert [i["call_id"] for i in lst["unsolved"]] == [ids["own"]]
    with Session() as s:
        f = s.scalars(select(CallFollowup).where(CallFollowup.call_id == ids["own"])).one()
        f.forward_status, f.forward_ticket_number = "sent", 1631
        s.commit()
    d = client.get(url).json()
    assert d["can_edit"] is False and d["forward"]["ticket_number"] == 1631
    assert client.put(url, json=body).status_code == 422
    login(client, "boss")                                   # not even a supervisor changes a sent form
    assert client.put(url, json=body).status_code == 422


def test_api_forward_off(fwd_env):
    client, _, ids, sends, on = fwd_env
    on["v"] = False
    switch_on(client)
    login(client, "ali")
    url = f"/api/calls/{ids['own']}/followup"
    got = client.get(url).json()
    assert got["forward_enabled"] is False and got["forward_options"] == []
    t = datetime.now(timezone.utc) + timedelta(hours=3)
    r = client.put(url, json={"what_happened": "x", "solved": False, "priority": "low", "responsible_kind": "forward",
                              "forward_person_id": 1, "expected_at": t.isoformat()})
    assert r.status_code == 422 and "غير مفعّل" in r.json()["detail"] and sends == []


# ---------- review D1 fixes ----------

def test_sent_lock_reads_the_row_again_under_the_lock(fs, engine):  # noqa: F811
    """An edit that loaded the form as "pending" before the job marked it "sent" must still be refused."""
    from sqlalchemy.orm import sessionmaker
    s, path = fs
    start_on(s)
    people = linked(s, path)
    u = user(s)
    c = mk(s, "a", at(10))
    now = at(11)
    f = fu.save(s, c, fwd_data(people["صالح الغامدي"].id, now), u, now, forward_ok=True)
    assert f.forward_status == "pending"                     # this session now holds a "pending" copy
    with sessionmaker(engine)() as job:                      # the send job, in its own session
        jf = job.get(CallFollowup, f.id)
        jf.forward_status, jf.forward_ticket_guid = "sent", "T-1"
        job.commit()
    with pytest.raises(fu.FormError, match="لا يمكن تعديل"):
        fu.save(s, c, {**fwd_data(None, now), "responsible_kind": "customer"}, u, now, forward_ok=True)


def test_failed_forward_needs_action_and_the_agent_picks_again(fs):
    s, path = fs
    start_on(s)
    people = linked(s, path)
    u = user(s)
    c = mk(s, "a", at(10))
    now = at(11)
    f = fu.save(s, c, fwd_data(people["صالح الغامدي"].id, now), u, now, forward_ok=True)
    fu.add_note(s, c, {"round": 2, "what_happened": "x", "solved": False, "responsible_kind": "forward",
                       "expected_at": now + timedelta(hours=4)}, u, ["119"], now + timedelta(minutes=5))
    f.forward_status, f.forward_error = "failed", forward.ERR_EXPIRED
    s.commit()
    assert rows(s, at(12))["a"]["step"] == "forward_failed"
    assert "forward_failed" in fu.ACTION_STEPS and "forward_failed" in fu.OPEN_STEPS
    assert not fu.forwarded(f) and fu.forward_failed(f)
    # picking again (another supervisor) sends again
    later = now + timedelta(hours=30)
    f = fu.save(s, c, fwd_data(people["السيد عبدالمنعم"].id, later), u, later, forward_ok=True)
    assert f.forward_status == "pending" and f.forward_error is None


def test_api_failed_forward_reopens_the_form_after_the_deadline(fwd_env):
    client, Session, ids, sends, on = fwd_env
    switch_on(client)
    login(client, "ali")
    url = f"/api/calls/{ids['own']}/followup"
    pid = client.get(url).json()["forward_options"][0]["id"]
    t = datetime.now(timezone.utc) + timedelta(hours=3)
    body = {"what_happened": "x", "solved": False, "priority": "low", "responsible_kind": "forward",
            "forward_person_id": pid, "expected_at": t.isoformat()}
    assert client.put(url, json=body).status_code == 200
    with Session() as s:                     # the deadline passed and the send failed
        c = s.get(Call, ids["own"])
        c.start_time -= timedelta(days=2)
        c.end_time -= timedelta(days=2)
        f = s.scalars(select(CallFollowup).where(CallFollowup.call_id == ids["own"])).one()
        f.forward_status, f.forward_error = "failed", "x"
        s.commit()
    switch_on(client)
    with Session() as s:
        from callsight.followups import SETTING_START, set_setting
        set_setting(s, SETTING_START, (datetime.now(timezone.utc) - timedelta(days=3)).date(), None)
        s.commit()
    login(client, "ali")
    d = client.get(url).json()
    assert d["step"] == "forward_failed" and d["can_edit"] is True
    r = client.put(url, json={**body, "responsible_kind": "customer", "forward_person_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["forward"] is None and r.json()["step"] == "waiting_solution"


def test_ticket_timeout_is_not_retried(fs):
    s, path = fs
    f = pending_form(s, path)

    class Slow(FakeCs):
        def create_ticket(self, *a):
            raise CsUnavailable("ReadTimeout")
    forward.send_pending(s, Slow())
    s.refresh(f)
    assert (f.forward_status, f.forward_error) == ("failed", forward.ERR_UNSURE)


def test_client_retryable_codes_and_bad_ticket_answers():
    for st in (408, 429):
        with mock_client(lambda req, st=st: httpx.Response(st, json={})) as c, pytest.raises(CsUnavailable):
            c.forward_targets()
    with mock_client(lambda req: httpx.Response(200, json={"guid": "x" * 65, "number": 1})) as c:
        with pytest.raises(CsError):
            c.create_ticket("A", "n", 2, "G")
    with mock_client(lambda req: httpx.Response(200, json={"guid": "T", "number": 2**31})) as c:
        assert c.create_ticket("A", "n", 2, "G") == ("T", None)


def test_health_lists_forward_jobs_only_when_on(fs):
    from callsight import health
    s, _ = fs
    now = datetime.now(timezone.utc)
    names = lambda on: {i.key for i in health.jobs(s, now, forward_on=on)}  # noqa: E731
    assert "job.forward_send" in names(True) and "job.forward_send" not in names(False)


def test_failed_forward_with_notes_other_answers_go_in_a_note(fwd_env):
    client, Session, ids, sends, on = fwd_env
    switch_on(client)
    login(client, "ali")
    url = f"/api/calls/{ids['own']}/followup"
    opts = client.get(url).json()["forward_options"]
    t = datetime.now(timezone.utc) + timedelta(hours=3)
    body = {"what_happened": "x", "solved": False, "priority": "low", "responsible_kind": "forward",
            "forward_person_id": opts[0]["id"], "expected_at": t.isoformat()}
    assert client.put(url, json=body).status_code == 200
    r = client.post(url + "/notes", json={"round": 2, "what_happened": "y", "solved": False,
                                          "responsible_kind": "forward", "expected_at": t.isoformat()})
    assert r.status_code == 200, r.text
    with Session() as s:
        f = s.scalars(select(CallFollowup).where(CallFollowup.call_id == ids["own"])).one()
        f.forward_status = "failed"
        s.commit()
    assert client.get(url).json()["step"] == "forward_failed"
    r = client.put(url, json={"what_happened": "x", "solved": True, "priority": "low"})
    assert r.status_code == 422 and "ملاحظة" in r.json()["detail"]
    # the note closes it (the supervisor never got it)
    r = client.post(url + "/notes", json={"round": 3, "what_happened": "حُلّت", "solved": True})
    assert r.status_code == 200, r.text
    assert r.json()["step"] == "solved_after"


def test_rate_limit_on_the_ticket_is_retried(fs):
    s, path = fs
    f = pending_form(s, path)

    class Busy(FakeCs):
        def create_ticket(self, *a):
            raise CsUnavailable("HTTP 429", 429)
    forward.send_pending(s, Busy())
    s.refresh(f)
    assert (f.forward_status, f.forward_error) == ("pending", forward.ERR_UNAVAILABLE)
