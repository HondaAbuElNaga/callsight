"""Customer-service employee KPI % (spec 2026-11-23-agent-kpi): bands, AHT steps, the 21:30 work day, and a
hand-calculated fixture day against the test Postgres."""
import itertools
from datetime import date, datetime, timedelta

import pytest

from callsight import agent_score as sc
from callsight.config import RIYADH
from callsight.db.models import CallLeg
from test_api import engine  # noqa: F401  (fixture)
from test_followups import form, mk, s, start_on, user  # noqa: F401  (s is a fixture)

D = date(2026, 11, 24)
_leg = itertools.count(1)


def at(h, m=0, day=D):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=RIYADH)


# ---------- pure scoring ----------

@pytest.mark.parametrize("part,whole,want", [(10, 10, 100), (95, 100, 100), (90, 100, 90), (85, 100, 90),
                                             (81, 100, 90), (80, 100, 80), (1, 100, 10), (0, 5, 0), (0, 0, None)])
def test_band_rounds_up_to_the_next_10(part, whole, want):
    assert sc.band_up(part, whole) == want


@pytest.mark.parametrize("aht,want", [(None, None), (0, 100), (420, 100), (420.5, 90), (600, 90), (601, 80),
                                      (780, 80), (781, 70), (10_000, 0)])
def test_aht_bands(aht, want):
    assert sc.aht_score(aht) == want


def test_final_is_the_average_of_parts_with_data():
    assert sc.final(100, None, 80) == 90
    assert sc.final(None, None, None) is None
    assert sc.final(60, 50, 90) == pytest.approx(200 / 3)


def test_window_is_work_days_ending_2130():
    assert sc.window(D, D + timedelta(days=1)) == (at(21, 30, D - timedelta(days=1)), at(21, 30))


# ---------- fixture day ----------

def missed(s, uid, start, number, rings):
    c = mk(s, uid, start, status="no_answer", agent=None, talk=0, number=number)
    for ext, disp in rings:
        s.add(CallLeg(id=next(_leg), call_id=c.id, uid=uid, kind="extension", start_time=start, to_number=ext,
                      disposition=disp, raw={}))
    s.flush()
    return c


def day(s):
    """Work day D = (D-1) 21:30 .. D 21:30."""
    start_on(s, D - timedelta(days=1))
    u = user(s, "ali", ext="104")
    a1 = mk(s, "A1", at(10), agent="104", talk=300)                                  # handle 300
    a2 = mk(s, "A2", at(21, 45, D - timedelta(days=1)), agent="104", talk=600)       # after 21:30 -> day D
    mk(s, "A3", at(21, 40), agent="104", talk=1000)                                  # next work day: left out
    form(s, a1, u, filled=at(21), solved=True)                                       # on time (by 21:30)
    form(s, a2, u, filled=at(9), solved=True)            # before the cut-over: old rule, due 23:55 the 23rd -> late
    missed(s, "M1", at(11), "0501111111", [("104", "NO ANSWER")])
    mk(s, "R1", at(15), direction="outbound", agent="119", number="+966501111111")    # recalled, same work day
    missed(s, "M2", at(12), "0502222222", [("104", "NO ANSWER")])
    mk(s, "R2", at(9, day=D + timedelta(days=1)), direction="outbound", agent="119", number="0502222222")  # too late
    m3 = mk(s, "M3", at(13), agent="119", talk=60)                                    # colleague answered it
    for ext, disp in (("104", "NO ANSWER"), ("119", "ANSWERED")):
        s.add(CallLeg(id=next(_leg), call_id=m3.id, uid="M3", kind="extension", start_time=at(13), to_number=ext,
                      disposition=disp, raw={}))
    missed(s, "M4", at(14), None, [("104", "NO ANSWER")])                             # no number: left out
    missed(s, "M5", at(16), "0503333333", [("104", "NO ANSWER")])
    mk(s, "R5", at(16, 10), direction="outbound", agent="104", number="0503333333", status="no_answer",
       talk=0, total=5)                                                               # early hang-up: no try
    mk(s, "AS", at(10), agent="116", talk=100)                                        # after-sales: no score
    s.flush()


def test_fixture_day_hand_calculated(s):  # noqa: F811
    day(s)
    got = sc.compute(s, D, D + timedelta(days=1), ["104", "116", "119"], ["104", "119"], now=at(12, day=D + timedelta(days=2)))
    assert set(got["agents"]) == {"104", "119"}
    a = got["agents"]["104"]
    # answered A1 + A2 = 2; missed M1, M2, M5 = 3 (M3 a colleague took, M4 no number); recalled M1 -> 3/5 = 60
    assert (a["answered"], a["missed"], a["recalled"], a["answer_score"]) == (2, 3, 1, 60)
    assert (a["forms_due"], a["forms_on_time"], a["forms_score"]) == (2, 1, 50)
    assert a["aht"] == pytest.approx(450) and a["aht_score"] == 90                    # (300 + 600) / 2 = 7:30
    assert a["kpi"] == pytest.approx(200 / 3)
    b = got["agents"]["119"]
    # M3 answered, no missed; its form never filled -> due 1, on time 0; AHT 60 s
    assert (b["answered"], b["missed"], b["answer_score"]) == (1, 0, 100)
    assert (b["forms_due"], b["forms_on_time"], b["forms_score"]) == (1, 0, 0)
    assert b["aht_score"] == 100 and b["kpi"] == pytest.approx(200 / 3)


def test_call_after_2130_counts_next_day(s):  # noqa: F811
    day(s)
    nxt = sc.compute(s, D + timedelta(days=1), D + timedelta(days=2), ["104"], ["104", "119"],
                     now=at(12, day=D + timedelta(days=3)))["agents"]["104"]
    assert nxt["answered"] == 1 and nxt["aht"] == pytest.approx(1000)                 # only A3
    assert nxt["answer_score"] == 100 and nxt["aht_score"] == 60                       # 16:40 -> 4 steps


def test_no_data_gives_no_scores(s):  # noqa: F811
    got = sc.compute(s, D, D + timedelta(days=1), ["104"], ["104", "119"], now=at(23))["agents"]["104"]
    assert got["answer_score"] is None and got["forms_score"] is None and got["kpi"] is None
