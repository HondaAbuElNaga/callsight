"""The rating set (spec 2026-11-16-rating-set): N calls from days the user chose, rated by hand to finish the model.

- Pool per day (Riyadh): answered customer calls of a team agent, recording downloaded, talk MIN_TALK..max seconds,
  not a branch supervisor's proof call, not a test call (rating-exclude.yaml).
- Share: N split evenly over the days; a day with fewer calls gives its rest to the others (largest remainder).
- Inside a day: agents take turns (each agent's calls in sha256(salt:call_id) order), so no one agent fills a day.
- Picked once (same days + salt -> same calls), stored in `rating_pick`, ranked by hash (a mix of days for the rater).
- The picked calls are queued for a transcript (old days are before ASR_START_DATE, so the 5-minute job skips them);
  their analysis follows by the normal job when NLP_ENABLED=true. The blind set is drawn from them (blindset.pool).
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ..asr.queueing import CUSTOMER_DIRECTIONS, AsrConfig, add_row
from ..config import RIYADH
from ..db.models import CallAnalysis, Call, RatingPick, SatisfactionLabel, Transcript
from .settings import rating_excluded

DEFAULT_N = 500
MAX_N = 2000
MAX_DAYS = 31
MIN_TALK = 20                    # a rating needs a real conversation
SALT = "callsight-rating-v1"


def _key(salt: str, call_id: int) -> str:
    return hashlib.sha256(f"{salt}:{call_id}".encode()).hexdigest()


def parse_days(text: str) -> list[date]:
    days = sorted({date.fromisoformat(x.strip()) for x in (text or "").split(",") if x.strip()})
    if not 1 <= len(days) <= MAX_DAYS:
        raise ValueError(f"give 1..{MAX_DAYS} days as YYYY-MM-DD,YYYY-MM-DD")
    return days


def exists_already(s: Session) -> bool:
    return bool(s.scalar(select(exists().where(RatingPick.call_id.is_not(None)))))


def pool(s: Session, days: list[date], max_seconds: int) -> dict[date, list[tuple[int, str]]]:
    lo = datetime(days[0].year, days[0].month, days[0].day, tzinfo=RIYADH)
    hi = datetime(days[-1].year, days[-1].month, days[-1].day, tzinfo=RIYADH) + timedelta(days=1)
    talk = func.coalesce(Call.talk_seconds, 0)
    rows = s.execute(select(Call.id, Call.agent_ext, Call.start_time).where(
        Call.status == "answered", Call.direction.in_(CUSTOMER_DIRECTIONS), Call.agent_ext.is_not(None),
        Call.recording_status == "ok", Call.team.is_distinct_from("forward"), Call.content_deleted_at.is_(None),
        talk >= MIN_TALK, talk <= max_seconds, Call.start_time >= lo, Call.start_time < hi)
        .order_by(Call.id)).all()
    skip, want = rating_excluded(), set(days)
    out: dict[date, list[tuple[int, str]]] = defaultdict(list)
    for cid, ext, st in rows:
        d = st.astimezone(RIYADH).date()
        if d in want and cid not in skip:
            out[d].append((cid, ext))
    return out


def shares(sizes: dict[date, int], n: int) -> dict[date, int]:
    """Even split of n over the days, each capped by its size; what a small day cannot take goes to the others
    (water level: every day gets min(size, L), the last few calls one each to the earliest days that still have)."""
    n = min(n, sum(sizes.values()))
    level = 0
    while sum(min(v, level + 1) for v in sizes.values()) <= n and level < max(sizes.values(), default=0):
        level += 1
    quota = {d: min(v, level) for d, v in sizes.items()}
    left = n - sum(quota.values())
    for d in sorted(sizes):
        if left and sizes[d] > quota[d]:
            quota[d] += 1
            left -= 1
    return quota


def _day_pick(calls: list[tuple[int, str]], k: int, salt: str) -> list[tuple[int, str]]:
    """k calls of one day, agents taking turns (agent order and each agent's calls in hash order)."""
    by_agent: dict[str, list[int]] = defaultdict(list)
    for cid, ext in calls:
        by_agent[ext].append(cid)
    queues = [(ext, sorted(ids, key=lambda c: _key(salt, c)))
              for ext, ids in sorted(by_agent.items(), key=lambda x: hashlib.sha256(f"{salt}:{x[0]}".encode()).hexdigest())]
    out: list[tuple[int, str]] = []
    turn = 0
    while len(out) < k and any(turn < len(ids) for _, ids in queues):
        for ext, ids in queues:
            if turn < len(ids) and len(out) < k:
                out.append((ids[turn], ext))
        turn += 1
    return out


def pick(s: Session, days: list[date], n: int, max_seconds: int, salt: str = SALT) -> list[tuple[int, date, str]]:
    by_day = pool(s, days, max_seconds)
    quota = shares({d: len(by_day.get(d, [])) for d in days}, n)
    picked = [(cid, d, ext) for d in days for cid, ext in _day_pick(by_day.get(d, []), quota[d], salt)]
    return sorted(picked, key=lambda p: _key(salt, p[0]))


def create(s: Session, days: list[date], n: int, cfg: AsrConfig, now: datetime, salt: str = SALT) -> dict:
    """Pick once and store. Returns per-day counts. Raises ValueError when a set exists or nothing can be picked."""
    if not 1 <= n <= MAX_N:
        raise ValueError(f"--n must be 1..{MAX_N}")
    if exists_already(s):
        raise ValueError("the rating set exists already (it is picked once)")
    picked = pick(s, days, n, cfg.max_seconds, salt)
    if not picked:
        raise ValueError("no calls with a recording on these days: fetch the days first (reconcile)")
    s.add_all(RatingPick(call_id=cid, rank=i, day=d, agent_ext=ext, salt=salt)
              for i, (cid, d, ext) in enumerate(picked, 1))
    s.commit()
    per = defaultdict(int)
    for _, d, _ in picked:
        per[d] += 1
    return {"picked": len(picked), "per_day": {d.isoformat(): per.get(d, 0) for d in days}}


def queue_transcripts(s: Session, cfg: AsrConfig, now: datetime) -> list[int]:
    """Transcript rows for picked calls that have none (by ASR_MODEL). Returns the ids to send to the asr queue."""
    calls = s.scalars(select(Call).join(RatingPick, RatingPick.call_id == Call.id).where(
        ~exists().where(Transcript.call_id == Call.id, Transcript.model == cfg.model))
        .order_by(RatingPick.rank)).all()
    out = [c.id for c in calls if add_row(s, c, cfg, now) == "queued"]
    s.commit()
    return out


def ids(s: Session) -> set[int]:
    return set(s.scalars(select(RatingPick.call_id)))


def status(s: Session) -> dict:
    tr = exists().where(Transcript.call_id == RatingPick.call_id, Transcript.status == "done")
    an = exists().where(CallAnalysis.call_id == RatingPick.call_id, CallAnalysis.status == "done")
    lab = exists().where(SatisfactionLabel.call_id == RatingPick.call_id)
    rows = s.execute(select(RatingPick.day, func.count(), func.count().filter(tr), func.count().filter(an),
                            func.count().filter(lab)).group_by(RatingPick.day).order_by(RatingPick.day)).all()
    days = [{"day": d.isoformat(), "picked": n, "transcribed": t, "analysed": a, "rated": r} for d, n, t, a, r in rows]
    tot = {k: sum(x[k] for x in days) for k in ("picked", "transcribed", "analysed", "rated")}
    return {"days": days, "total": tot}
