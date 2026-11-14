"""Ingest services: CDR rows -> legs + calls in Postgres, recordings, reconciliation, agent sync.

Every function takes a SQLAlchemy Session and a PbxAdapter, so the same code runs from the CLI
and from Celery jobs, and tests can pass a fake adapter.
"""
from __future__ import annotations

import logging
import wave
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import RIYADH
from ..db.models import Agent, Call, CallLeg, IngestState, Queue, ReconcileRun
from ..pbx.base import PbxAdapter, PbxError, PbxUnavailable
from ..pbx.yeastar import safe_recording_name
from .classify import TeamConfig, classify, is_team_call, leg_kind, to_number

log = logging.getLogger(__name__)
CURSOR_KEY = "last_cdr_id"
MAX_RECORDING_ATTEMPTS = 3


@dataclass
class IngestResult:
    rows: int = 0
    new_legs: int = 0
    calls: int = 0
    skipped_calls: int = 0


def _int_or_none(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _leg_values(row: dict, call_id: int) -> dict:
    return {
        "id": int(row["id"]),
        "call_id": call_id,
        "uid": row["uid"],
        "kind": leg_kind(row),
        "start_time": datetime.fromtimestamp(int(row.get("timestamp") or 0), tz=timezone.utc),
        "from_number": (row.get("call_from_number") or None),
        "to_number": to_number(row),
        "disposition": row.get("disposition"),
        "duration": _int_or_none(row.get("duration")),
        "ring_duration": _int_or_none(row.get("ring_duration")),
        "talk_duration": _int_or_none(row.get("talk_duration")),
        "reason": (row.get("reason") or "").strip()[:300] or None,
        "record_file": row.get("record_file") or None,
        "raw": row,
    }


def ingest_rows(session: Session, rows: Iterable[dict], team: TeamConfig) -> IngestResult:
    """Store team calls from raw CDR rows. Idempotent: same rows twice -> same result."""
    res = IngestResult()
    by_uid: dict[str, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        if r.get("uid") and r.get("id") is not None:
            by_uid[r["uid"]][int(r["id"])] = r
            res.rows += 1

    for uid, new_legs in by_uid.items():
        # Serialise work on one call across processes (worker + CLI): read legs only after the lock.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:uid))"), {"uid": uid})
        old = {leg.id: leg.raw for leg in session.scalars(select(CallLeg).where(CallLeg.uid == uid))}
        legs = {**old, **new_legs}
        if not is_team_call(legs.values(), team):
            res.skipped_calls += 1
            continue
        f = classify(list(legs.values()), team)
        fields = {k: v for k, v in vars(f).items()}

        session.execute(insert(Call).values(**fields, recording_status="none")
                        .on_conflict_do_nothing(index_elements=["uid"]))
        call = session.scalars(select(Call).where(Call.uid == uid).with_for_update()).one()
        old_file = call.recording_file
        for k, v in fields.items():
            setattr(call, k, v)
        if call.recording_file and (call.recording_file != old_file or call.recording_status == "none"):
            call.recording_status, call.recording_attempts, call.recording_path = "pending", 0, None
        elif not call.recording_file:
            call.recording_status, call.recording_path = "none", None

        for leg_id, row in legs.items():
            vals = _leg_values(row, call.id)
            stmt = insert(CallLeg).values(**vals)
            session.execute(stmt.on_conflict_do_update(
                index_elements=["id"], set_={k: stmt.excluded[k] for k in vals if k != "id"}))
        res.new_legs += len(set(new_legs) - set(old))
        res.calls += 1
        session.flush()
    return res


# ---------- cursor ----------

def get_cursor(session: Session) -> int | None:
    st = session.get(IngestState, CURSOR_KEY)
    return int(st.value) if st else None


def set_cursor(session: Session, rows: list[dict]) -> None:
    if not rows:
        return
    top = max(int(r["id"]) for r in rows)
    st = session.get(IngestState, CURSOR_KEY)
    if st is None:
        session.add(IngestState(key=CURSOR_KEY, value=str(top)))
    elif top > int(st.value):
        st.value = str(top)


def run_ingest(session: Session, pbx: PbxAdapter, team: TeamConfig) -> IngestResult:
    """Pull new CDR rows since the cursor (first run: last 24 h)."""
    last = get_cursor(session)
    if last is None:
        now = datetime.now(timezone.utc)
        rows = pbx.fetch_cdr_between(now - timedelta(days=1), now + timedelta(minutes=5))
    else:
        rows = pbx.fetch_cdr_since(last)
    res = ingest_rows(session, rows, team)
    set_cursor(session, rows)
    session.commit()
    return res


def run_backfill(session: Session, pbx: PbxAdapter, team: TeamConfig, days: int) -> IngestResult:
    now = datetime.now(timezone.utc)
    rows = pbx.fetch_cdr_between(now - timedelta(days=days), now + timedelta(minutes=5))
    res = ingest_rows(session, rows, team)
    set_cursor(session, rows)
    session.commit()
    return res


# ---------- recordings ----------

def check_wav(path: Path) -> None:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() < 1 or w.getnframes() < 1:
            raise PbxError("empty WAV")


def recording_rel_path(call: Call) -> Path:
    d = call.start_time.astimezone(RIYADH)
    return Path(f"{d:%Y}") / f"{d:%m}" / f"{d:%d}" / safe_recording_name(call.recording_file or "")


def fetch_one_recording(session: Session, pbx: PbxAdapter, audio_dir: Path, call: Call,
                        after_ok: Callable[[Session, Call], object] | None = None) -> bool:
    """Download + check one recording. `after_ok(session, call)` runs after the commit (queues the transcript)."""
    ok = False
    try:
        rel = recording_rel_path(call)
        dest = audio_dir / rel
        if not dest.exists():
            pbx.download_recording(call.recording_file, dest)
        try:
            check_wav(dest)
        except (wave.Error, EOFError, PbxError) as exc:
            dest.unlink(missing_ok=True)
            raise PbxError(f"bad WAV: {exc}") from exc
        call.recording_path, call.recording_status = rel.as_posix(), "ok"
        ok = True
        return True
    except PbxUnavailable:
        raise                      # PBX down / token limit: not this file's fault, don't count a try
    except (PbxError, OSError) as exc:
        call.recording_attempts += 1
        call.recording_status = "missing" if call.recording_attempts >= MAX_RECORDING_ATTEMPTS else "error"
        log.warning("recording for call %s failed (%s/%s): %s", call.id, call.recording_attempts,
                    MAX_RECORDING_ATTEMPTS, exc)
        return False
    finally:
        session.commit()
        if ok and after_ok is not None:
            after_ok(session, call)


def fetch_recordings(session: Session, pbx: PbxAdapter, audio_dir: Path, limit: int = 50,
                     after_ok: Callable[[Session, Call], object] | None = None) -> tuple[int, int]:
    calls = session.scalars(
        select(Call).where(Call.recording_status.in_(("pending", "error")),
                           Call.recording_attempts < MAX_RECORDING_ATTEMPTS)
        .order_by(Call.start_time).limit(limit)).all()
    ok = sum(fetch_one_recording(session, pbx, audio_dir, c, after_ok) for c in calls)
    return ok, len(calls) - ok


# ---------- reconciliation ----------

def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=RIYADH)
    return start, start + timedelta(days=1)


def reconcile_day(session: Session, pbx: PbxAdapter, team: TeamConfig, audio_dir: Path, day: date) -> ReconcileRun:
    """Compare one Riyadh day of PBX CDR with our DB, fill the gaps, store a report."""
    start, end = day_bounds(day)
    rows = pbx.fetch_cdr_between(start, end)
    by_uid: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_uid[r["uid"]].append(r)
    team_uids = {u for u, legs in by_uid.items() if is_team_call(legs, team)}
    team_leg_ids = {int(r["id"]) for u in team_uids for r in by_uid[u]}

    def missing_legs() -> int:
        if not team_leg_ids:
            return 0
        have = set(session.scalars(select(CallLeg.id).where(CallLeg.id.in_(team_leg_ids))))
        return len(team_leg_ids - have)

    before = missing_legs()
    if before:
        ingest_rows(session, [r for u in team_uids for r in by_uid[u]], team)
        session.commit()
    after = missing_legs()

    # Every answered customer call (inbound/outbound) should have a recording. Internal calls between
    # colleagues are not recorded by the PBX, so they are not expected.
    expected = session.scalars(select(Call).where(
        Call.uid.in_(team_uids), Call.status == "answered", Call.direction != "internal")).all() if team_uids else []
    rec_before = [c for c in expected if c.recording_status != "ok"]
    for c in rec_before:                       # one more try, even for calls that hit the retry limit
        if c.recording_file:
            fetch_one_recording(session, pbx, audio_dir, c)
    rec_after = sum(1 for c in expected if c.recording_status != "ok")
    db_calls = session.scalar(select(func.count()).select_from(Call).where(Call.uid.in_(team_uids))) if team_uids else 0

    run = ReconcileRun(day=day, pbx_team_calls=len(team_uids), pbx_team_legs=len(team_leg_ids),
                       db_team_calls=db_calls or 0, missing_legs_before=before, missing_legs_after=after,
                       recordings_expected=len(expected), missing_recordings_before=len(rec_before),
                       missing_recordings_after=rec_after,
                       ok=(after == 0 and rec_after == 0 and (db_calls or 0) == len(team_uids)))
    session.add(run)
    session.commit()
    return run


# ---------- agents ----------

def sync_agents(session: Session, pbx: PbxAdapter, team: TeamConfig) -> tuple[int, int]:
    queues = pbx.list_queues()
    exts = {e["ext"]: e["name"] for e in pbx.list_extensions()}
    for q in queues:
        session.merge(Queue(number=q["number"], name=q["name"], team=team.queue_team.get(q["number"])))
        for a in q["agents"]:
            exts.setdefault(a["ext"], a["name"])
    for ext, name in exts.items():
        session.merge(Agent(ext=ext, name=name or "", team=team.ext_team.get(ext), in_team=ext in team.ext_team))
    session.commit()
    return len(exts), len(queues)


# ---------- stats ----------

def day_stats(session: Session, day: date) -> list[tuple[str, str, str | None, int]]:
    """(direction, status, agent_ext, count) for one Riyadh day."""
    start, end = day_bounds(day)
    q = (select(Call.direction, Call.status, func.coalesce(Call.agent_ext, Call.missed_agent_ext), func.count())
         .where(Call.start_time >= start, Call.start_time < end)
         .group_by(Call.direction, Call.status, func.coalesce(Call.agent_ext, Call.missed_agent_ext))
         .order_by(Call.direction, Call.status))
    return [tuple(r) for r in session.execute(q)]
