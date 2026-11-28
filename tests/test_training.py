"""Spec 2026-11-16-priority-no-followup, "Part 4: the training lookup": the caller's student record from the training
system and the person the forward form suggests. The training system is always faked here (no real call)."""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from callsight import forward
from callsight.api.main import ApiConfig, create_app
from callsight.db.models import Call, ForwardPerson
from datetime import datetime, timedelta, timezone

from callsight import training_api
from callsight.db.models import CallFollowup
from callsight.training_api import (TrainingClient, TrainingConfig, TrainingUnavailable, local_mobile, parse,
                                   search_key)
from test_agent_supervisor_api import env, login, switch_on  # noqa: F401
from test_api import SECRET, TEAM, engine  # noqa: F401
from test_followups import s  # noqa: F401
from test_forward import YAML, FakeTargets, fs  # noqa: F401

CFG = TrainingConfig(url="https://training.example/api/v1", key="sk_train")


def answer(trainers=(), branch="فرع الدمام حي الزهور (رجال)", status="مستمر"):
    return {"data": [{"student": "طالب تجربة", "placements": [
        {"branch": branch, "diploma": "دبلوم إدارة السلامة", "level": "الخامس", "section": "الشعبة 1",
         "status": status, "trainers": list(trainers)}]}]}


# ---------- number + answer parsing ----------

@pytest.mark.parametrize("raw,want", [
    ("0551234567", "0551234567"), ("966551234567", "0551234567"), ("+966 55 123 4567", "0551234567"),
    ("00966551234567", "0551234567"), ("551234567", "0551234567"), ("0966551234567", "0551234567"),
    ("90551234567", "0551234567"),
    ("0138123456", None), ("119", None), (None, None), ("", None)])
def test_local_mobile(raw, want):
    assert local_mobile(raw) == want


def test_parse_keeps_the_fields_and_reads_any_trainer_shape():
    got = parse(answer(trainers=["أحمد علي", {"name": "سعيد عمر"}, {"fullName": "خالد سالم"}, {"x": 1}, 7, "أحمد علي"]))
    p = got[0]["placements"][0]
    assert got[0]["student"] == "طالب تجربة"
    assert (p["branch"], p["diploma"], p["level"], p["section"], p["status"]) == (
        "فرع الدمام حي الزهور (رجال)", "دبلوم إدارة السلامة", "الخامس", "الشعبة 1", "مستمر")
    assert p["trainers"] == ["أحمد علي", "سعيد عمر", "خالد سالم"]     # duplicates, numbers, empty items dropped


@pytest.mark.parametrize("bad", [None, [], "x", {"data": None}, {"data": "x"}, {"data": [1, None]},
                                 {"data": [{"placements": "x"}]}])
def test_parse_never_breaks_on_a_bad_answer(bad):
    assert all(st["placements"] == [] for st in parse(bad))


# ---------- client ----------

def test_client_sends_key_and_q_and_caches():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=answer())
    c = TrainingClient(CFG, transport=httpx.MockTransport(handler))
    assert c.students("0551234567")[0]["student"] == "طالب تجربة"
    assert c.students("0551234567")[0]["student"] == "طالب تجربة"
    assert len(seen) == 1                                               # second time from memory
    r = seen[0]
    assert r.headers["X-Api-Key"] == "sk_train" and r.url.path == "/api/v1/students/responsible-trainer"
    assert r.url.params["q"] == "0551234567"


@pytest.mark.parametrize("resp", [httpx.Response(401, json={"message": "bad key"}), httpx.Response(500),
                                  httpx.Response(200, text="<html>")])
def test_client_errors_are_unavailable_without_the_number(resp):
    c = TrainingClient(CFG, transport=httpx.MockTransport(lambda req: resp))
    with pytest.raises(TrainingUnavailable) as e:
        c.students("0551234567")
    assert "0551234567" not in str(e.value)


def test_client_network_error_and_https_only():
    def boom(req):
        raise httpx.ConnectTimeout("t", request=req)
    with pytest.raises(TrainingUnavailable):
        TrainingClient(CFG, transport=httpx.MockTransport(boom)).students("0551234567")
    with pytest.raises(ValueError):
        TrainingClient(TrainingConfig(url="http://x", key="k"))


def test_failure_pauses_lookups_and_a_good_answer_ends_the_pause(monkeypatch):
    calls, state = [], {"code": 500}

    def handler(req):
        calls.append(1)
        return httpx.Response(state["code"], json=answer())
    clock = {"t": 1000.0}
    monkeypatch.setattr(training_api.time, "monotonic", lambda: clock["t"])
    c = TrainingClient(CFG, transport=httpx.MockTransport(handler))
    with pytest.raises(TrainingUnavailable):
        c.students("0551234567")
    with pytest.raises(TrainingUnavailable):
        c.students("0559999999")                      # another number: paused, not sent
    assert len(calls) == 1
    clock["t"] += training_api.FAIL_SECONDS + 1
    state["code"] = 200
    assert c.students("0559999999")
    assert len(calls) == 2


def test_per_minute_limit_and_cache_expiry(monkeypatch):
    calls = []
    clock = {"t": 1000.0}
    monkeypatch.setattr(training_api.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(training_api, "PER_MINUTE", 2)
    c = TrainingClient(CFG, transport=httpx.MockTransport(lambda req: calls.append(1) or httpx.Response(200, json=answer())))
    c.students("0550000001")
    c.students("0550000002")
    with pytest.raises(TrainingUnavailable):
        c.students("0550000003")
    clock["t"] += 61
    c.students("0550000003")
    c.students("0550000001")                          # still cached (10 min)
    assert len(calls) == 3
    clock["t"] += training_api.CACHE_SECONDS
    c.students("0550000001")
    assert len(calls) == 4


def test_too_many_at_once_and_too_big(monkeypatch):
    c = TrainingClient(CFG, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=answer())))
    for _ in range(training_api.MAX_RUNNING):
        c._running.acquire()
    with pytest.raises(TrainingUnavailable):
        c.students("0551234567")
    big = httpx.MockTransport(lambda req: httpx.Response(200, content=b" " * (training_api.MAX_BYTES + 1)))
    with pytest.raises(TrainingUnavailable):
        TrainingClient(CFG, transport=big).students("0551234567")


def test_config_from_env(monkeypatch):
    monkeypatch.delenv("TRAINING_API_URL", raising=False)
    monkeypatch.setenv("TRAINING_API_KEY", " ")
    assert not TrainingConfig.from_env().enabled
    monkeypatch.setenv("TRAINING_API_KEY", "k")
    assert TrainingConfig.from_env().enabled and TrainingConfig.from_env().url == "https://training.sstli.com/api/v1"


# ---------- the suggestion ----------

def synced(fs):  # noqa: F811
    s, path = fs
    forward.sync_people(s, FakeTargets(), path)
    return s


def pid(s, guid):
    return s.scalar(select(ForwardPerson.id).where(ForwardPerson.guid == guid))


def test_trainer_in_the_list_is_suggested(fs):  # noqa: F811
    s = synced(fs)
    got = forward.suggest(s, parse(answer(trainers=[{"name": "صالح أحمد الغامدي"}])))
    assert got == {"person_id": pid(s, "G-STAFF"), "kind": "trainer", "name": "صالح أحمد الغامدي"}


def test_no_trainer_gives_the_branch_supervisor(fs):  # noqa: F811
    s = synced(fs)
    got = forward.suggest(s, parse(answer()))                       # "الزهور" in both branch names
    assert got["kind"] == "branch_supervisor" and got["person_id"] == pid(s, "G-SALEH")


def test_unknown_or_ambiguous_trainer_falls_back_and_one_word_is_no_match(fs):  # noqa: F811
    s = synced(fs)
    for t in (["غير موجود في القائمة"], ["الغامدي"], ["صالح الغامدي"]):   # unknown / one word / two people
        assert forward.suggest(s, parse(answer(trainers=t)))["kind"] == "branch_supervisor"


def test_women_branch_never_gets_a_men_supervisor_and_no_branch_no_guess(fs):  # noqa: F811
    s = synced(fs)
    assert forward.suggest(s, parse(answer(branch="فرع الدمام حي الزهور (نساء)"))) is None
    assert forward.suggest(s, parse(answer(branch="فرع تبوك"))) is None
    assert forward.suggest(s, []) is None


def test_two_branches_equally_close_is_no_guess(fs):  # noqa: F811
    s = synced(fs)       # "الزهور" -> رجال الزهور, "مدينة" -> رجال المدينة: one word each
    assert forward.suggest(s, parse(answer(branch="فرع الدمام حي الزهور مدينة الأعمال1 (رجال)"))) is None


def test_trainer_name_order_and_first_name_matter(fs):  # noqa: F811
    s = synced(fs)
    got = forward.suggest(s, parse(answer(trainers=["أحمد صالح الغامدي"])))      # not "صالح أحمد الغامدي"
    assert got["kind"] == "branch_supervisor"
    got = forward.suggest(s, parse(answer(trainers=["صالح أحمد"])))
    assert got == {"person_id": pid(s, "G-STAFF"), "kind": "trainer", "name": "صالح أحمد الغامدي"}


def test_several_students_on_one_phone_no_suggestion(fs):  # noqa: F811
    s = synced(fs)
    d = answer(trainers=["صالح أحمد"])
    d["data"].append(answer()["data"][0])
    assert forward.suggest(s, parse(d)) is None


def test_running_placement_supervisor_beats_a_finished_placement_trainer(fs):  # noqa: F811
    s = synced(fs)
    d = answer(branch="فرع الواحة (رجال)", status="منسحب", trainers=["صالح أحمد"])
    d["data"][0]["placements"].append(answer(branch="فرع المدينة (رجال)")["data"][0]["placements"][0])
    assert forward.suggest(s, parse(d))["person_id"] == pid(s, "G-FIXED")


def test_one_shared_city_word_is_not_a_branch(fs):  # noqa: F811
    s = synced(fs)       # "رجال الزهور" needs زهور; a branch of another district must not get him
    assert forward.suggest(s, parse(answer(branch="فرع الرياض حي النسيم (رجال)"))) is None


def test_running_placement_first(fs):  # noqa: F811
    s = synced(fs)
    d = answer(branch="فرع الواحة (رجال)", status="منسحب")
    d["data"][0]["placements"].append(answer(branch="فرع المدينة (رجال)")["data"][0]["placements"][0])
    assert forward.suggest(s, parse(d))["person_id"] == pid(s, "G-FIXED")


def test_inactive_people_are_never_suggested(fs):  # noqa: F811
    s = synced(fs)
    s.get(ForwardPerson, pid(s, "G-SALEH")).active = False
    s.commit()
    assert forward.suggest(s, parse(answer())) is None


# ---------- API ----------

@pytest.fixture
def tenv(env, tmp_path):  # noqa: F811
    _, Session, ids, *_ = env
    p = tmp_path / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    with Session() as s:
        s.execute(text("TRUNCATE forward_person RESTART IDENTITY CASCADE"))
        s.commit()
        forward.sync_people(s, FakeTargets(), p)
    asked, state = [], {"answer": answer(), "fail": False, "fwd": True}

    def lookup(phone):
        asked.append(phone)
        if state["fail"]:
            raise TrainingUnavailable("HTTP 500")
        return parse(state["answer"])
    app = create_app(Session, ApiConfig(secret=SECRET, audio_dir=tmp_path, teams=lambda: TEAM, reports_dir=tmp_path,
                                        topics=lambda: {}, forward_enabled=lambda: state["fwd"],
                                        training_lookup=lookup))
    return TestClient(app), Session, ids, asked, state


def test_api_agent_gets_student_and_suggestion(tenv):
    client, Session, ids, asked, state = tenv
    switch_on(client)
    login(client, "ali")
    assert client.get(f"/api/calls/{ids['own']}/followup").json()["training_enabled"] is True
    got = client.get(f"/api/calls/{ids['own']}/student").json()
    assert asked == ["0501234567"]
    assert got["enabled"] and got["error"] is None and got["students"][0]["student"] == "طالب تجربة"
    assert got["suggestion"]["kind"] == "branch_supervisor"
    opts = {o["id"] for o in client.get(f"/api/calls/{ids['own']}/followup").json()["forward_options"]}
    assert got["suggestion"]["person_id"] in opts                  # the form can pick it
    state["fwd"] = False                                           # link to the other system off: info only
    got = client.get(f"/api/calls/{ids['own']}/student").json()
    assert got["students"] and got["suggestion"] is None


def test_api_other_agent_and_outage_and_landline(tenv):
    client, Session, ids, asked, state = tenv
    switch_on(client)
    login(client, "sara")                                          # not her call
    assert client.get(f"/api/calls/{ids['own']}/student").status_code == 404
    login(client, "ali")
    state["fail"] = True
    assert client.get(f"/api/calls/{ids['own']}/student").json()["error"] == "unavailable"
    with Session() as s:
        s.get(Call, ids["own"]).customer_number = "0138123456"
        s.commit()
    assert client.get(f"/api/calls/{ids['own']}/student").json()["error"] == "not_mobile"
    assert len(asked) == 1                                         # a landline is never sent


@pytest.mark.parametrize("typed,want", [
    ("1023456789", "1023456789"), ("2123456789", "2123456789"), ("١٠٢٣٤٥٦٧٨٩", "1023456789"),
    (" 102 345 6789 ", "1023456789"), ("0551234567", "0551234567"), ("+966 55 123 4567", "0551234567"),
    ("3023456789", None), ("12345", None), ("abc1023456789", None), ("", None), (None, None), ("1" * 30, None)])
def test_search_key(typed, want):
    assert search_key(typed) == want


def test_api_agent_searches_by_national_id_when_the_number_finds_nobody(tenv):
    client, Session, ids, asked, state = tenv
    switch_on(client)
    login(client, "ali")
    state["answer"] = {"data": []}
    assert client.get(f"/api/calls/{ids['own']}/student").json()["students"] == []
    state["answer"] = answer()
    got = client.post(f"/api/calls/{ids['own']}/student", json={"q": "١٠٢٣٤٥٦٧٨٩"}).json()
    assert asked == ["0501234567", "1023456789"]
    assert got["students"][0]["student"] == "طالب تجربة" and got["suggestion"]["kind"] == "branch_supervisor"
    got = client.post(f"/api/calls/{ids['own']}/student", json={"q": "hello"}).json()
    assert got["error"] == "bad_query" and len(asked) == 2              # a bad entry is never sent
    login(client, "sara")                                                # still only the call's own agent
    assert client.post(f"/api/calls/{ids['own']}/student", json={"q": "1023456789"}).status_code == 404


def test_api_no_form_404_and_closed_form_403(tenv):
    client, Session, ids, asked, state = tenv
    login(client, "ali")                                           # follow-ups not switched on: no form
    assert client.get(f"/api/calls/{ids['own']}/student").status_code == 404
    switch_on(client)
    login(client, "ali")
    with Session() as s:
        ali = ids["ali"]
        now = datetime.now(timezone.utc)
        s.add(CallFollowup(call_id=ids["own"], owner_ext="119", what_happened="x", solved=False, priority="low",
                           responsible_kind="customer", expected_at=now + timedelta(hours=5),
                           callback_at=now - timedelta(minutes=1), filled_by=ali, first_filled_at=now,
                           updated_by=ali, updated_at=now))
        s.commit()
    assert client.get(f"/api/calls/{ids['own']}/student").status_code == 403   # callback time passed: closed
    assert asked == []


def test_api_off_and_followups_not_switched_on(env, tmp_path):  # noqa: F811
    client, Session, ids, *_ = env
    login(client, "ali")
    assert client.get(f"/api/calls/{ids['own']}/student").json() == {
        "enabled": False, "students": [], "suggestion": None, "error": None}
