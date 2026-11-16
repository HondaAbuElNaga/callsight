"""Spec 2026-11-16-priority-no-followup, "Part 4: forwards page": the archive, one problem's story, the numbers."""
from datetime import timedelta

from callsight import forward_report as fr
from callsight.config import RIYADH
from test_api import engine  # noqa: F401
from test_followups import at, s  # noqa: F401
from test_forward_chain import Fake, cs, form_to, tick  # noqa: F401
from test_forward_d2 import sup_call
from test_forward import fwd_env  # noqa: F401
from test_agent_supervisor_api import env  # noqa: F401


class Talk(Fake):
    """Tickets that also carry timeline items (what each person wrote)."""

    def __init__(self):
        super().__init__()
        self.items: dict[str, list[dict]] = {}

    def get_ticket(self, guid):
        return {"ticket": {"closedAt": self.closed.get(guid)}, "timeline": self.items.get(guid, [])}


def say(fake, guid, t, note, who, typ="followup"):
    fake.items.setdefault(guid, []).append({"kind": "followup", "type": typ, "note": note, "at": t.isoformat(),
                                            "actorName": who})


def day():
    return at(11).astimezone(RIYADH).date()


def test_story_and_numbers_through_all_three_levels(cs):  # noqa: F811
    s, _, p = cs
    fake = Talk()
    c, f = form_to(s, p["G-STAFF"])
    say(fake, "T-1", at(13), "سأتواصل مع العميل غدًا", "خالد أحمد")
    tick(s, fake, at(14))
    t2 = at(11) + timedelta(hours=24, minutes=5)
    tick(s, fake, t2)                                                      # -> branch supervisor
    sup_call(s, "short", t2 + timedelta(hours=1), talk=30, score=9)
    say(fake, "T-1", t2 + timedelta(hours=2), "تم الاتصال", "صالح محمد الغامدي", "followup_close")
    fake.closed["T-1"] = (t2 + timedelta(hours=2)).isoformat()
    tick(s, fake, t2 + timedelta(hours=3))                                 # closed, call too short: not solved
    t3 = t2 + timedelta(hours=24, minutes=5)
    tick(s, fake, t3)
    tick(s, fake, t3 + timedelta(minutes=5))                               # managers + copies
    say(fake, "COPY-2", t3 + timedelta(hours=1), "أرسلنا الشهادة للعميل", "مدير ثالث", "followup_close")
    fake.closed["COPY-2"] = (t3 + timedelta(hours=1)).isoformat()
    tick(s, fake, t3 + timedelta(hours=2))

    st = fr.story(s, c.id)
    assert st["state"] == "solved" and st["agent"]["what_happened"] == "x"
    lv = [(h["level"], h["name"], h["result"]) for h in st["hops"]]
    assert lv[:2] == [(1, "خالد أحمد", "passed_on"), (2, "صالح الغامدي", "passed_on")]
    assert [x[2] for x in lv[2:]] == ["closed_by_other", "closed_by_other", "solved", "closed_by_other"]
    assert [i["note"] for i in st["hops"][0]["items"]] == ["سأتواصل مع العميل غدًا"]       # what the employee wrote
    sup = st["hops"][1]
    assert [i["note"] for i in sup["items"]] == ["تم الاتصال"] and sup["closed_at"]
    assert sup["proof"]["reason"] == "short" and len(sup["calls"]) == 1                  # his call, why not
    m3 = st["hops"][4]
    assert m3["name"] == "مدير ثالث" and m3["solution"] == "أرسلنا الشهادة للعميل"
    assert [i["note"] for i in m3["items"]] == ["أرسلنا الشهادة للعميل"]

    out = fr.listing(s, day(), day() + timedelta(days=3))
    assert [i["call_id"] for i in out["items"]] == [c.id]
    line = out["items"][0]
    assert line["solved_by"] == {"name": "مدير ثالث", "level": 3} and line["level"] == 3
    assert line["seconds_to_solve"] > 48 * 3600
    ins = out["insights"]
    assert ins["totals"]["forwarded"] == 1 and ins["totals"]["solved_3"] == 1 and ins["totals"]["open"] == 0
    emp = ins["employees"][0]
    assert (emp["name"], emp["received"], emp["solved"], emp["not_solved_24h"]) == ("خالد أحمد", 1, 0, 1)
    sv = ins["supervisors"][0]
    assert (sv["received"], sv["not_solved_24h"], sv["closed_no_good_call"]) == (1, 1, 1)
    mg = {d["name"]: d for d in ins["managers"]}
    assert mg["مدير ثالث"]["solved"] == 1 and mg["مدير أول"]["closed_by_other"] == 1
    assert ins["branches"][0] == {"branch": "رجال الزهور", "received": 1, "solved_in_branch": 0, "went_to_managers": 1}
    # filters
    assert fr.listing(s, day(), day(), state="open")["items"] == []
    assert len(fr.listing(s, day(), day(), level=3)["items"]) == 1
    assert fr.listing(s, day(), day(), level=1)["items"] == []
    assert len(fr.listing(s, day(), day(), person_id=p["G-SALEH"].id)["items"]) == 1
    assert fr.listing(s, day() + timedelta(days=1), day() + timedelta(days=2))["items"] == []   # by forward day


def test_employee_solves_in_time(cs):  # noqa: F811
    s, _, p = cs
    fake = Talk()
    c, f = form_to(s, p["G-STAFF"])
    say(fake, "T-1", at(15), "تم الحل", "خالد أحمد", "followup_close")
    fake.closed["T-1"] = at(15).isoformat()
    tick(s, fake, at(16))
    ins = fr.listing(s, day(), day())["insights"]
    assert ins["totals"]["solved_1"] == 1
    e = ins["employees"][0]
    assert (e["solved"], e["avg_seconds_to_solve"]) == (1, 5 * 3600)       # got it 11:00, solved at the 16:00 run
    assert ins["branches"][0]["solved_in_branch"] == 1
    assert fr.story(s, c.id)["hops"][0]["solution"] == "تم الحل"


def test_story_none_for_a_call_without_forward(cs):  # noqa: F811
    s, _, p = cs
    assert fr.story(s, 999999) is None


def test_api_staff_sees_the_page(fwd_env):  # noqa: F811
    client, *_ = fwd_env
    from test_agent_supervisor_api import login, switch_on
    switch_on(client)
    login(client, "root")
    r = client.get("/api/forwards")
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["insights"]["totals"]["forwarded"] == 0
    assert client.get("/api/forwards", params={"state": "x"}).status_code == 422
    assert client.get("/api/forwards", params={"from": "2026-01-01", "to": "2027-06-01"}).status_code == 422
    assert client.get("/api/forwards/123456").status_code == 404
