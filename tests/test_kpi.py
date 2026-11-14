"""KPI engine against the test Postgres: a hand-calculated fixture week (spec 2026-11-14-kpi-reports).

Week under test: Sunday 2026-11-01 .. Saturday 2026-11-07 (Riyadh). Every expected number below is worked out
by hand from the call list in `week()`; the comments show the sums.
"""
import itertools
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from callsight import kpi
from callsight.config import RIYADH
from callsight.db.models import Agent, Base, Call, CallLeg, ReconcileRun
from callsight.ingest.classify import TeamConfig

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
URL = os.getenv("TEST_DATABASE_URL")
TEAM = TeamConfig(ext_team={"104": "customer_service", "119": "customer_service", "116": "after_sales"},
                  queue_team={"6401": "customer_service"})
CS, AS = "customer_service", "after_sales"
_ids = itertools.count(1)


@pytest.fixture(scope="module")
def engine():
    if not URL or "_test" not in URL:
        pytest.skip("TEST_DATABASE_URL not set (must point to a *_test database)")
    eng = create_engine(URL, connect_args={"connect_timeout": 3})
    try:
        with eng.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database not reachable: {exc}")
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE call_leg, call, agent, reconcile_run RESTART IDENTITY CASCADE"))
    with sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


def at(month, day, hh, mm=0):
    return datetime(2026, month, day, hh, mm, tzinfo=RIYADH)


def add(s, name, start, *, direction="inbound", team=CS, status="answered", agent=None, missed=None, cust=None,
        talk=0, wait=0, hold=None, rings=()):
    """One call; `rings` = extension legs [(ext, disposition)] for the missed-ring count."""
    c = Call(uid=name, direction=direction, entry="queue", customer_number=cust, team=team, agent_ext=agent,
             missed_agent_ext=missed, start_time=start, end_time=start, wait_seconds=wait, talk_seconds=talk,
             hold_seconds=hold, total_seconds=talk + wait, status=status)
    s.add(c)
    s.flush()
    for ext, disp in rings:
        s.add(CallLeg(id=next(_ids), call_id=c.id, uid=name, kind="extension", start_time=start, to_number=ext,
                      disposition=disp, raw={}))
    return c


def week(s):
    """The fixture: 2 calls before the week (look-back), 19 in/around it. Times are Riyadh."""
    add(s, "P1", at(10, 30, 12), agent="104", cust="+966501111111", talk=100, wait=3)      # 70 h before C1
    add(s, "P2", at(10, 28, 9), agent="119", cust="0502222222", talk=50, wait=3)           # > 72 h before C2
    add(s, "C1", at(11, 1, 10), agent="119", cust="00966501111111", talk=200, wait=10)     # repeat of P1 -> 104
    add(s, "C2", at(11, 1, 10), agent="104", cust="0502222222", talk=300, wait=20, hold=30)  # not a repeat
    add(s, "C3", at(11, 2, 9), agent="119", cust="0503333333", talk=120, wait=6,            # 104 missed, 119 took it
        rings=[("104", "NO ANSWER"), ("119", "ANSWERED")])
    add(s, "C4", at(11, 2, 11), status="abandoned_queue", missed="104", cust="0504444444",
        rings=[("104", "NO ANSWER"), ("119", "NO ANSWER")])
    add(s, "C5", at(11, 3, 14), direction="outbound", agent="119", cust="0505555555", talk=60)
    add(s, "C6", at(11, 3, 15), agent="104", cust="0505555555", talk=180, wait=8)           # repeat of outbound C5 -> 119
    add(s, "C7", at(11, 4, 8), status="no_answer", missed="119", cust="0506666666", rings=[("119", "NO ANSWER")])
    add(s, "C8", at(11, 4, 9), agent="104", cust="0506666666", talk=90, wait=12)            # repeat, unattributed
    add(s, "C9", at(11, 5, 9), team=AS, agent="116", cust="0507777777", talk=240, wait=5)
    add(s, "C10", at(11, 5, 12), team=AS, status="voicemail", missed="116", cust="0507777777",
        rings=[("116", "NO ANSWER")])
    add(s, "C11", at(11, 6, 10), team=AS, agent="116", cust="0507777777", talk=60, wait=4)  # repeat -> 116 (C9)
    add(s, "C12", at(11, 7, 10), team=AS, agent="116", cust="0507777777", talk=120, wait=3)  # chain: repeat -> 116 (C11)
    add(s, "C13", at(11, 7, 23, 30), agent="119", cust=None, talk=30, wait=2)               # Sat 23:30 Riyadh: in
    add(s, "C14", at(11, 8, 0, 10), agent="104", cust="0501414141", talk=100, wait=5)       # Sun 00:10 Riyadh: next week
    add(s, "C15", at(11, 2, 12), direction="internal", agent="119", talk=40)                # internal: never counted
    add(s, "C16", at(11, 3, 16), status="busy", missed="104", cust="0508888888", rings=[("104", "BUSY")])
    add(s, "C17", at(11, 4, 10), direction="outbound", team=AS, status="no_answer", agent="116", cust="0509999999")
    add(s, "C18", at(11, 6, 11), status="abandoned_ivr", cust="0501212121")
    add(s, "C19", at(11, 5, 12), agent="114", cust="0501313131", talk=50, wait=7)           # supervisor answered
    add(s, "C20", at(11, 30, 10), agent="119", cust="0502020202", talk=60, wait=5)          # week across month edge
    s.add_all([Agent(ext="104", name="Agent 104", team=CS, in_team=True),
               Agent(ext="119", name="Agent 119", team=CS, in_team=True),
               Agent(ext="116", name="Agent 116", team=AS, in_team=True)])
    s.commit()


WEEK = (date(2026, 11, 1), date(2026, 11, 8))


def agents(r):
    return {a["ext"]: a for a in r["agents"]}


# ---------- period helpers ----------

@pytest.mark.parametrize("day, start", [
    (date(2026, 11, 1), date(2026, 11, 1)),    # Sunday -> itself
    (date(2026, 11, 7), date(2026, 11, 1)),    # Saturday -> previous Sunday
    (date(2026, 11, 4), date(2026, 11, 1)),
    (date(2026, 11, 8), date(2026, 11, 8)),
    (date(2026, 12, 1), date(2026, 11, 29)),   # week across a month edge
    (date(2027, 1, 2), date(2026, 12, 27)),    # and across a year edge
])
def test_week_is_sunday_to_saturday(day, start):
    p = kpi.period_for("week", day)
    assert (p.start, p.end) == (start, start.fromordinal(start.toordinal() + 7))
    assert p.start.weekday() == 6 and p.previous().end == p.start


@pytest.mark.parametrize("day, start, end, prev", [
    (date(2026, 11, 14), date(2026, 11, 1), date(2026, 12, 1), date(2026, 10, 1)),
    (date(2026, 12, 31), date(2026, 12, 1), date(2027, 1, 1), date(2026, 11, 1)),
    (date(2026, 1, 1), date(2026, 1, 1), date(2026, 2, 1), date(2025, 12, 1)),
    (date(2028, 2, 29), date(2028, 2, 1), date(2028, 3, 1), date(2028, 1, 1)),
])
def test_month_edges(day, start, end, prev):
    p = kpi.period_for("month", day)
    assert (p.start, p.end, p.previous().start, p.previous().end) == (start, end, prev, start)


def test_bounds_are_riyadh_midnight():
    lo, hi = kpi.period_for("week", date(2026, 11, 3)).bounds()
    assert lo == datetime(2026, 10, 31, 21, 0, tzinfo=timezone.utc)
    assert hi == datetime(2026, 11, 7, 21, 0, tzinfo=timezone.utc)


def test_partial_period():
    p = kpi.period_for("week", date(2026, 11, 1))
    assert p.is_partial(at(11, 7, 23, 59)) and not p.is_partial(at(11, 8, 0, 0))
    with pytest.raises(ValueError):
        kpi.period_for("year", date(2026, 11, 1))


# ---------- KPIs: hand-calculated week ----------

def test_team_kpis_customer_service(session):
    week(session)
    r = kpi.compute(session, *WEEK, teams=TEAM, team=CS)
    t = r["summary"]
    # inbound CS in the week: C1 C2 C3 C4 C6 C7 C8 C13 C16 C18 C19 = 11 (C14 is next week, C15 internal)
    assert (t["calls"], t["answered"], t["unanswered"]) == (11, 7, 4)
    assert t["answer_rate"] == pytest.approx(7 / 11)
    # AHT = (200 + (300+30 hold) + 120 + 180 + 90 + 30 + 50) / 7 = 1000 / 7
    assert t["aht"] == pytest.approx(1000 / 7)
    assert t["avg_ring"] == pytest.approx((10 + 20 + 6 + 8 + 12 + 2 + 7) / 7)
    assert (t["outbound"], t["outbound_answered"], t["outbound_aht"]) == (1, 1, 60)
    assert (t["repeats"], t["repeats_unattributed"]) == (3, 1)          # C1, C6, C8 (C8 unattributed)
    assert t["satisfaction"] is None
    assert r["by_status"] == {"answered": 7, "abandoned_queue": 1, "abandoned_ivr": 1, "no_answer": 1,
                              "busy": 1, "voicemail": 0}
    assert set(agents(r)) == {"104", "119"}


def test_team_kpis_after_sales(session):
    week(session)
    t = kpi.compute(session, *WEEK, teams=TEAM, team=AS)["summary"]
    assert (t["calls"], t["answered"], t["unanswered"]) == (4, 3, 1)    # C9 C10(voicemail) C11 C12
    assert t["aht"] == pytest.approx((240 + 60 + 120) / 3)
    assert t["avg_ring"] == pytest.approx(4)
    assert (t["outbound"], t["outbound_answered"], t["outbound_aht"]) == (1, 0, None)
    assert (t["repeats"], t["repeats_unattributed"]) == (2, 0)          # chain C9 -> C11 -> C12 = 2


def test_all_teams_is_the_sum(session):
    week(session)
    r = kpi.compute(session, *WEEK, teams=TEAM)
    t = r["summary"]
    assert (t["calls"], t["answered"], t["unanswered"]) == (15, 10, 5)
    assert t["aht"] == pytest.approx((1000 + 420) / 10)
    assert t["avg_ring"] == pytest.approx((65 + 12) / 10)
    assert (t["outbound"], t["outbound_answered"], t["outbound_aht"]) == (2, 1, 60)
    assert (t["repeats"], t["repeats_unattributed"]) == (5, 1)
    assert r["by_status"]["voicemail"] == 1 and sum(r["by_status"].values()) == 15
    assert [a["ext"] for a in r["agents"]] == ["104", "116", "119"]     # teams.yaml only: no 114, no 302


def test_per_agent_rows(session):
    week(session)
    a = agents(kpi.compute(session, *WEEK, teams=TEAM))
    # 104: answered C2 C6 C8; missed rings C3 (then 119 answered), C4, C16 (busy); credited repeat C1 (via P1)
    assert (a["104"]["calls"], a["104"]["missed_rings"], a["104"]["repeats"]) == (3, 3, 1)
    assert a["104"]["aht"] == pytest.approx((330 + 180 + 90) / 3)
    assert (a["104"]["outbound"], a["104"]["outbound_aht"]) == (0, None)
    # 119: answered C1 C3 C13; missed C4 C7; outbound C5; credited repeat C6 (earlier call was outbound C5)
    assert (a["119"]["calls"], a["119"]["missed_rings"], a["119"]["repeats"]) == (3, 2, 1)
    assert a["119"]["aht"] == pytest.approx((200 + 120 + 30) / 3)
    assert (a["119"]["outbound"], a["119"]["outbound_answered"], a["119"]["outbound_aht"]) == (1, 1, 60)
    # 116: answered C9 C11 C12; missed C10 (voicemail); outbound C17 not answered; repeats C11 C12
    assert (a["116"]["calls"], a["116"]["missed_rings"], a["116"]["repeats"]) == (3, 1, 2)
    assert (a["116"]["outbound"], a["116"]["outbound_answered"]) == (1, 0)
    assert a["104"]["name"] == "Agent 104" and a["116"]["team"] == AS
    # per-agent calls don't add up to the team (C19 answered by supervisor 114)
    assert sum(x["calls"] for x in a.values()) == 9


def test_agent_scope(session):
    week(session)
    r = kpi.compute(session, *WEEK, teams=TEAM, agent="119")
    t = r["summary"]
    assert (t["calls"], t["answered"], t["unanswered"], t["repeats"]) == (3, 3, 2, 1)
    assert t["answer_rate"] is None and t["avg_ring"] is None and t["repeats_unattributed"] is None
    assert t["aht"] == pytest.approx(350 / 3) and r["by_status"] is None
    assert [a["ext"] for a in r["agents"]] == ["119"]
    assert sum(d["calls"] for d in r["days"]) == 3 and sum(d["unanswered"] for d in r["days"]) == 2


def test_days_series_uses_riyadh_days(session):
    week(session)
    days = kpi.compute(session, *WEEK, teams=TEAM)["days"]
    assert [d["day"] for d in days] == [date(2026, 11, d) for d in range(1, 8)]
    assert [d["calls"] for d in days] == [2, 2, 2, 2, 3, 2, 2]
    assert [d["answered"] for d in days] == [2, 1, 1, 1, 2, 1, 2]
    assert [d["unanswered"] for d in days] == [0, 1, 1, 1, 1, 1, 0]


def test_repeat_needs_customer_number(session):
    add(session, "A", at(11, 2, 9), agent="104", cust=None, talk=10)
    add(session, "B", at(11, 2, 10), agent="104", cust=None, talk=10)
    add(session, "C", at(11, 2, 11), agent="104", cust="", talk=10)
    add(session, "D", at(11, 2, 12), agent="104", cust="", talk=10)
    session.commit()
    assert kpi.compute(session, *WEEK, teams=TEAM)["summary"]["repeats"] == 0


def test_repeat_window_is_72_hours_and_same_team(session):
    add(session, "A", at(11, 1, 9), agent="104", cust="0551231234", talk=10)
    add(session, "B", at(11, 4, 9), agent="119", cust="0551231234", talk=10)           # exactly 72 h later
    add(session, "C", at(11, 1, 9), team=AS, agent="116", cust="0559999999", talk=10)
    add(session, "D", at(11, 1, 10), agent="104", cust="0559999999", talk=10)          # other team before: no
    add(session, "E", at(11, 5, 9), agent="104", cust="0551231234", talk=10)           # 24 h after B: yes
    session.commit()
    a = agents(kpi.compute(session, *WEEK, teams=TEAM))
    assert kpi.compute(session, *WEEK, teams=TEAM)["summary"]["repeats"] == 2           # B (72 h incl.) and E
    assert (a["104"]["repeats"], a["119"]["repeats"]) == (1, 1)


def test_month_and_month_edge_week(session):
    week(session)
    m = kpi.period_for("month", date(2026, 11, 20))
    assert kpi.compute(session, m.start, m.end, teams=TEAM)["summary"]["calls"] == 17     # 15 + C14 + C20
    p = kpi.period_for("week", date(2026, 12, 1))                                          # Nov 29 .. Dec 5
    assert kpi.compute(session, p.start, p.end, teams=TEAM)["summary"]["calls"] == 1      # C20
    d = kpi.period_for("month", date(2026, 12, 1))
    assert kpi.compute(session, d.start, d.end, teams=TEAM)["summary"]["calls"] == 0


def test_empty_period_no_division_errors(session):
    r = kpi.compute(session, date(2026, 12, 6), date(2026, 12, 13), teams=TEAM)
    t = r["summary"]
    assert (t["calls"], t["answered"], t["unanswered"], t["repeats"]) == (0, 0, 0, 0)
    assert t["answer_rate"] is None and t["aht"] is None and t["avg_ring"] is None and t["outbound_aht"] is None
    assert all(a["calls"] == 0 and a["aht"] is None for a in r["agents"])
    assert len(r["days"]) == 7 and all(d["calls"] == 0 for d in r["days"])


def test_report_has_previous_period_and_flags(session):
    week(session)
    r = kpi.report(session, kpi.period_for("week", date(2026, 11, 3)), TEAM, now=at(11, 14, 9))
    assert r["previous"]["calls"] == 2 and r["previous_period"]["start"] == "2026-10-25"   # P1, P2
    assert r["period"] == {"kind": "week", "start": "2026-11-01", "end": "2026-11-07", "partial": False}
    assert r["trusted"] is False
    assert kpi.report(session, kpi.period_for("week", date(2026, 11, 10)), TEAM, now=at(11, 14, 9))["period"]["partial"]


def test_unknown_team_or_agent_rejected(session):
    with pytest.raises(ValueError):
        kpi.compute(session, *WEEK, teams=TEAM, team="sales")
    with pytest.raises(ValueError):
        kpi.compute(session, *WEEK, teams=TEAM, agent="114")


def _runs(s, days_ok):
    for d, ok in days_ok:
        s.add(ReconcileRun(day=d, pbx_team_calls=1, pbx_team_legs=1, db_team_calls=1, missing_legs_before=0,
                           missing_legs_after=0, recordings_expected=0, missing_recordings_before=0,
                           missing_recordings_after=0, ok=ok))
    s.commit()


def test_trusted_needs_7_consecutive_ok_days(session):
    assert kpi.data_trusted(session) is False
    _runs(session, [(date(2026, 11, d), True) for d in range(1, 7)])
    assert kpi.data_trusted(session) is False                     # 6 days
    _runs(session, [(date(2026, 11, 7), True)])
    assert kpi.data_trusted(session) is True
    _runs(session, [(date(2026, 11, 8), False)])
    assert kpi.data_trusted(session) is False                     # latest day failed
    _runs(session, [(date(2026, 11, 8), True)])                   # re-run of the same day OK -> latest counts
    assert kpi.data_trusted(session) is True


def test_trusted_breaks_on_gap(session):
    _runs(session, [(date(2026, 11, d), True) for d in (1, 2, 3, 5, 6, 7, 8)])
    assert kpi.data_trusted(session) is False


def test_mask_number():
    assert kpi.mask_number("0501234567") == "05XX…4567"
    assert kpi.mask_number("+966501234567") == "+966XX…4567"
    assert kpi.mask_number(None) == "–" and kpi.mask_number("123") == "XXX"
