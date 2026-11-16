"""The rating set (spec 2026-11-16-rating-set): N calls from days the user chose, rated by hand to finish the model.

Flow (3-agent review 2026-11-16: only the picked recordings are downloaded, never whole days):
1. fetch: ONE walk of the PBX call list over the chosen days, the team calls stored; their recordings are marked
   `held` (on the PBX, not downloaded: the 2-minute download job skips them).
2. create: pool per day (Riyadh) = answered customer calls of a team agent with a recording file on the PBX, talk
   more than MIN_TALK s up to ASR_MAX_SECONDS, not a branch supervisor's proof call, not a test call, not erased.
   N split evenly over the days (a small day gives its rest to the others); inside a day the agents take turns
   (sha256(salt:...) order, so the same days give the same calls). The recordings are downloaded in that order; a
   recording that fails is replaced by the next call of the same day. Stored once in `rating_pick`.
3. The picked calls are queued for a transcript (old days are before ASR_START_DATE, so the 5-minute job skips them);
   their analysis follows by the normal job when NLP_ENABLED=true. The blind set is drawn from them (blindset).
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..asr.queueing import CUSTOMER_DIRECTIONS, AsrConfig, add_row
from ..config import RIYADH
from ..db.models import CallAnalysis, Call, RatingPick, SatisfactionLabel, Transcript
from .settings import rating_excluded

DEFAULT_N = 500
MAX_N = 2000
MAX_DAYS = 31
MIN_TALK = 20                    # a rating needs a real conversation: more than 20 s
SALT = "callsight-rating-v1"
HELD = "held"                    # recording on the PBX, not downloaded on purpose (old day, not picked)


def _key(salt: str, x: object) -> str:
    return hashlib.sha256(f"{salt}:{x}".encode()).hexdigest()


def parse_days(text: str) -> list[date]:
    days = sorted({date.fromisoformat(x.strip()) for x in (text or "").split(",") if x.strip()})
    if not 1 <= len(days) <= MAX_DAYS:
        raise ValueError(f"give 1..{MAX_DAYS} days as YYYY-MM-DD,YYYY-MM-DD")
    return days


def _range(days: list[date]) -> tuple[datetime, datetime]:
    lo = datetime(days[0].year, days[0].month, days[0].day, tzinfo=RIYADH)
    hi = datetime(days[-1].year, days[-1].month, days[-1].day, tzinfo=RIYADH) + timedelta(days=1)
    return lo, hi


def fetch(s: Session, pbx, team, days: list[date], cfg: AsrConfig) -> dict[str, int]:
    """Store the team calls of the days (one PBX walk) and mark their old recordings `held`. -> team calls per day."""
    from ..ingest.classify import is_team_call
    from ..ingest.service import ingest_rows
    lo, hi = _range(days)
    want = set(days)
    rows = [r for r in pbx.fetch_cdr_between(lo, hi)
            if datetime.fromtimestamp(int(r.get("timestamp") or 0), RIYADH).date() in want]
    by_uid: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_uid[r["uid"]].append(r)
    team_uids = [u for u, legs in by_uid.items() if is_team_call(legs, team)]
    if team_uids:
        ingest_rows(s, [r for u in team_uids for r in by_uid[u]], team)
        s.commit()
    keep = cfg.start_time                         # calls since ASR_START_DATE are downloaded as usual
    per = {d.isoformat(): 0 for d in days}
    for i in range(0, len(team_uids), 1000):
        chunk = team_uids[i:i + 1000]
        cond = [Call.uid.in_(chunk), Call.recording_status.in_(("pending", "error"))]
        if keep is not None:
            cond.append(Call.start_time < keep)
        s.execute(update(Call).where(*cond).values(recording_status=HELD))
        for st in s.scalars(select(Call.start_time).where(Call.uid.in_(chunk))):
            per[st.astimezone(RIYADH).date().isoformat()] += 1
    s.commit()
    return per


def exists_already(s: Session) -> bool:
    return bool(s.scalar(select(exists().where(RatingPick.call_id.is_not(None)))))


def pool(s: Session, days: list[date], max_seconds: int) -> dict[date, list[tuple[int, str]]]:
    lo, hi = _range(days)
    talk = func.coalesce(Call.talk_seconds, 0)
    rows = s.execute(select(Call.id, Call.agent_ext, Call.start_time).where(
        Call.status == "answered", Call.direction.in_(CUSTOMER_DIRECTIONS), Call.agent_ext.is_not(None),
        Call.recording_status.in_(("ok", HELD, "pending", "error")), Call.recording_file.is_not(None),
        Call.team.is_distinct_from("forward"), Call.content_deleted_at.is_(None),
        talk > MIN_TALK, talk <= max_seconds, Call.start_time >= lo, Call.start_time < hi)
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


def day_order(calls: list[tuple[int, str]], salt: str) -> list[tuple[int, str]]:
    """All calls of one day, agents taking turns (agent order and each agent's calls in hash order)."""
    by_agent: dict[str, list[int]] = defaultdict(list)
    for cid, ext in calls:
        by_agent[ext].append(cid)
    queues = [(ext, sorted(ids, key=lambda c: _key(salt, c)))
              for ext, ids in sorted(by_agent.items(), key=lambda x: _key(salt, x[0]))]
    out: list[tuple[int, str]] = []
    turn = 0
    while any(turn < len(ids) for _, ids in queues):
        out += [(ids[turn], ext) for ext, ids in queues if turn < len(ids)]
        turn += 1
    return out


def pick(s: Session, days: list[date], n: int, max_seconds: int, salt: str = SALT,
         have=None) -> list[tuple[int, date, str]]:
    """The set. `have(call_id) -> bool` (download the recording); a call without one is replaced by the next of
    the same day. Without `have` every call counts as available (planning / tests)."""
    by_day = pool(s, days, max_seconds)
    quota = shares({d: len(by_day.get(d, [])) for d in days}, n)
    picked = []
    for d in days:
        got = 0
        for cid, ext in day_order(by_day.get(d, []), salt):
            if got >= quota[d]:
                break
            if have is None or have(cid):
                picked.append((cid, d, ext))
                got += 1
    return sorted(picked, key=lambda p: _key(salt, p[0]))


def create(s: Session, days: list[date], n: int, cfg: AsrConfig, now: datetime, salt: str = SALT,
           pbx=None, audio_dir: Path | None = None) -> dict:
    """Pick once, download the picked recordings (with pbx), store. -> counts per day. ValueError when a set exists
    or nothing can be picked."""
    if not 1 <= n <= MAX_N:
        raise ValueError(f"--n must be 1..{MAX_N}")
    if exists_already(s):
        raise ValueError("the rating set exists already (it is picked once)")
    have = None
    if pbx is not None:
        from ..ingest.service import fetch_one_recording

        def have(cid: int) -> bool:
            call = s.get(Call, cid)
            if call.recording_status == "ok" and call.recording_path:
                return True
            fetch_one_recording(s, pbx, audio_dir, call)      # commits; ok / error (counted try)
            return call.recording_status == "ok"
    picked = pick(s, days, n, cfg.max_seconds, salt, have)
    if not picked:
        raise ValueError("no calls with a recording on these days: run rating-set fetch first")
    try:
        s.add_all(RatingPick(call_id=cid, rank=i, day=d, agent_ext=ext, salt=salt)
                  for i, (cid, d, ext) in enumerate(picked, 1))
        s.commit()
    except IntegrityError as exc:
        s.rollback()
        raise ValueError("the rating set exists already (made at the same time)") from exc
    per = defaultdict(int)
    for _, d, _ in picked:
        per[d] += 1
    return {"picked": len(picked), "per_day": {d.isoformat(): per.get(d, 0) for d in days}}


def queue_transcripts(s: Session, cfg: AsrConfig, now: datetime) -> list[int]:
    """Transcripts for picked calls: a new row where none exists, a failed one tried again. -> ids to send."""
    usable = (Call.content_deleted_at.is_(None), Call.recording_status == "ok", Call.id.not_in(rating_excluded()))
    calls = s.scalars(select(Call).join(RatingPick, RatingPick.call_id == Call.id).where(
        *usable, ~exists().where(Transcript.call_id == Call.id, Transcript.model == cfg.model))
        .order_by(RatingPick.rank)).all()
    out = [c.id for c in calls if add_row(s, c, cfg, now) == "queued"]
    failed = s.scalars(select(Transcript).join(RatingPick, RatingPick.call_id == Transcript.call_id)
                       .join(Call, Call.id == Transcript.call_id)
                       .where(*usable, Transcript.model == cfg.model, Transcript.status == "error")
                       .with_for_update(of=Transcript)).all()
    for row in failed:
        row.status, row.tries, row.error, row.queued_at, row.redo = "queued", 0, None, now, True
        row.started_at = row.finished_at = None
        out.append(row.call_id)
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
