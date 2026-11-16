"""Spec 2026-11-16-rating-set: N calls from chosen days, spread over days and agents, picked once, rated first."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from callsight.analysis import blindset, ratingset
from callsight.analysis.views import rating_queue
from callsight.asr.queueing import AsrConfig
from callsight.config import RIYADH
from callsight.db.models import Call, RatingPick, Transcript
from test_call_analysis import engine, make_call  # noqa: F401  (engine is a fixture)

CFG = AsrConfig(start_date=date(2026, 11, 15))
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=RIYADH)
D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 7, 12)
_n = [0]


@pytest.fixture
def s(engine):  # noqa: F811
    with engine.begin() as c:
        names = ", ".join(t for t in ("rating_pick", "blind_set", "satisfaction_label", "call_analysis",
                                      "transcript_segment", "transcript", "call"))
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    with sessionmaker(engine)() as ses:
        yield ses


def old_call(s, day: date, agent: str, talk=60, rec="r.wav", status="answered") -> Call:
    _n[0] += 1
    st = datetime(day.year, day.month, day.day, 9, tzinfo=RIYADH) + timedelta(minutes=_n[0] % 600)
    c = Call(uid=f"rs{_n[0]}", direction="inbound", entry="queue", start_time=st, end_time=st + timedelta(minutes=2),
             talk_seconds=talk, total_seconds=talk + 10, status=status, agent_ext=agent,
             recording_status="ok" if rec else "none", recording_path=rec)
    s.add(c)
    s.commit()
    return c


def test_spread_over_days_and_agents_and_queued(s):
    for _ in range(30):
        old_call(s, D1, "101")                    # one agent with many calls
    for _ in range(3):
        old_call(s, D1, "102")
    for _ in range(4):
        old_call(s, D2, "103")                    # a small day: gives its rest to the others
    for _ in range(20):
        old_call(s, D3, "104")
    old_call(s, D1, "101", talk=10)              # too short
    old_call(s, D1, "101", rec=None)             # no recording
    old_call(s, D1, "101", status="no_answer")
    old_call(s, date(2026, 10, 5), "101")        # another day
    got = ratingset.create(s, [D3, D1, D2], 30, CFG, NOW)
    assert got["picked"] == 30 and got["per_day"] == {"2026-07-12": 13, "2026-10-01": 13, "2026-10-02": 4}
    picks = s.scalars(select(RatingPick).order_by(RatingPick.rank)).all()
    d1 = [p for p in picks if p.day == D1]
    assert sum(p.agent_ext == "102" for p in d1) == 3          # agents take turns: the small agent gets all 3
    queued = ratingset.queue_transcripts(s, CFG, NOW)
    assert len(queued) == 30 and s.scalar(select(Transcript.status).limit(1)) == "queued"
    assert ratingset.queue_transcripts(s, CFG, NOW) == []       # never twice
    with pytest.raises(ValueError, match="exists already"):
        ratingset.create(s, [D1], 5, CFG, NOW)


def test_same_days_same_pick(s):
    for i in range(12):
        old_call(s, D1, str(101 + i % 3))
    a = [c for c, _, _ in ratingset.pick(s, [D1], 5, 3600)]
    b = [c for c, _, _ in ratingset.pick(s, [D1], 5, 3600)]
    assert a == b and len(set(a)) == 5


def test_nothing_to_pick_and_bad_days(s):
    with pytest.raises(ValueError, match="fetch the days first"):
        ratingset.create(s, [D1], 10, CFG, NOW)
    with pytest.raises(ValueError):
        ratingset.parse_days("")
    assert ratingset.parse_days("2026-10-02, 2026-10-01,2026-10-01") == [D1, D2]


def test_queue_and_blind_pool_use_the_set(s):
    new = make_call(s, rec="n.wav", start=datetime(2026, 11, 15, 10, tzinfo=RIYADH))   # newest, not picked
    olds = [old_call(s, D1, "101") for _ in range(3)]
    ratingset.create(s, [D1], 3, CFG, NOW)
    for c in olds:
        s.add(Transcript(call_id=c.id, model="whisper-large-v3", status="done", text="x"))
    s.commit()
    order = [i["call_id"] for i in rating_queue(s, 1)["items"]]
    assert order[:3] != [new.id] and set(order[:3]) == {c.id for c in olds} and order[3] == new.id
    assert {cid for cid, _ in blindset.pool(s)} == {c.id for c in olds}     # blind set drawn from the picks
    st = ratingset.status(s)
    assert st["total"] == {"picked": 3, "transcribed": 3, "analysed": 0, "rated": 0}


def test_shares_even_with_small_days():
    assert ratingset.shares({D1: 100, D2: 2, D3: 100}, 10) == {D1: 4, D2: 2, D3: 4}
    assert ratingset.shares({D1: 3, D2: 2}, 10) == {D1: 3, D2: 2}
    assert ratingset.shares({D1: 0, D2: 5}, 3) == {D1: 0, D2: 3}
