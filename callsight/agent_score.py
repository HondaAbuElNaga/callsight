"""Customer-service employee KPI % (spec 2026-11-23-agent-kpi). Page only (not the PDF, no snapshots).

Three parts, each 0-100 in steps of 10, and the final KPI = the average of the parts that have data:
1. Answering: (answered + recalled) / (answered + missed), rounded UP to the next 10. Missed = an inbound call that rang
   his extension and nobody answered (once per call); recalled = a real-try outbound call (followups.proper_try_sql)
   by any customer-service extension to the same number (last 9 digits) after the call, before the end of that call's
   work day (21:30). A missed call without a number cannot be recalled and is left out.
2. Forms: forms filled on time / forms due (followups rules), rounded UP to the next 10.
3. AHT (talk + hold of answered inbound): <= 7 min -> 100, then -10 for every started 3 minutes, never below 0.
The period's days are work days: day D = (D-1) 21:30 .. D 21:30 Riyadh (a call after 21:30 is the next day's work).
"""
from __future__ import annotations

from datetime import date, datetime

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import followups
from .db.models import Call, CallLeg, norm_number

TEAM = "customer_service"
AHT_FULL_SECONDS = 7 * 60
AHT_STEP_SECONDS = 3 * 60


def band_up(part: int, whole: int) -> int | None:
    """part/whole rounded UP to the next 10 (95% -> 100, 90% -> 90, 85% -> 90); None when nothing to count."""
    if whole <= 0:
        return None
    return -(-10 * part // whole) * 10


def aht_score(aht: float | None) -> int | None:
    if aht is None:
        return None
    if aht <= AHT_FULL_SECONDS:
        return 100
    steps = -(-(aht - AHT_FULL_SECONDS) // AHT_STEP_SECONDS)
    return max(0, 100 - 10 * int(steps))


def final(*parts: int | None) -> float | None:
    got = [p for p in parts if p is not None]
    return sum(got) / len(got) if got else None


def window(start: date, end: date) -> tuple[datetime, datetime]:
    """[start, end) days as work days: (start-1) 21:30 .. (end-1) 21:30 Riyadh."""
    return followups.work_day_start(start), followups.work_day_start(end)


def _answering(s: Session, lo, hi, exts: list[str], callers: list[str]) -> dict[str, dict]:
    out = {e: {"answered": 0, "missed": 0, "recalled": 0} for e in exts}
    for e, n in s.execute(select(Call.agent_ext, func.count()).where(
            Call.direction == "inbound", Call.status == "answered", Call.agent_ext.in_(exts),
            Call.start_time >= lo, Call.start_time < hi).group_by(Call.agent_ext)):
        out[e]["answered"] = n
    missed = s.execute(select(CallLeg.to_number, Call.id, Call.start_time, Call.end_time, Call.customer_number)
                       .join(Call, Call.id == CallLeg.call_id)
                       .where(CallLeg.kind == "extension", CallLeg.to_number.in_(exts),
                              func.coalesce(func.upper(CallLeg.disposition), "") != "ANSWERED",
                              Call.direction == "inbound", Call.status != "answered",
                              norm_number(Call.customer_number) != "",
                              Call.start_time >= lo, Call.start_time < hi)
                       .distinct()).all()
    recalled = _recalled(s, {(cid, start, end, num) for _, cid, start, end, num in missed}, callers, lo, hi)
    for ext, cid, *_ in missed:
        out[ext]["missed"] += 1
        out[ext]["recalled"] += cid in recalled
    return out


def _digits9(number: str | None) -> str:
    return "".join(ch for ch in (number or "") if ch.isdigit())[-9:]


def _recalled(s: Session, calls: set, callers: list[str], lo: datetime, hi: datetime) -> set[int]:
    """Ids of missed calls with a real-try team callback after the call and before the end of its work day.
    One query: every real-try team outbound call of the window (+1 day: a call's work day ends <= hi + 1 day)."""
    if not calls:
        return set()
    tries: dict[str, list[datetime]] = {}
    for num, t in s.execute(select(Call.customer_number, Call.start_time).where(
            Call.direction == "outbound", Call.agent_ext.in_(callers), followups.proper_try_sql(Call),
            Call.start_time > lo, Call.start_time < hi + timedelta(days=1))):
        tries.setdefault(_digits9(num), []).append(t)
    got: set[int] = set()
    for cid, start, end, num in calls:
        after, until = end or start, followups.work_day_end(start)
        if any(after < t < until for t in tries.get(_digits9(num), ())):
            got.add(cid)
    return got


def _forms(s: Session, lo, hi, exts: list[str], callers: list[str], now: datetime) -> dict[str, dict]:
    out = {e: {"due": 0, "on_time": 0} for e in exts}
    st = followups.settings(s)
    start, since = st[followups.SETTING_START], st[followups.SETTING_KPI]
    if start is None:
        return out
    count_from = max(start, since) if since else start
    for r in followups.rows(s, exts, count_from, lo, hi, callers=callers):
        state = followups.form_state(r.call, r.form, now)
        if state == "missing":                 # deadline not passed and not filled yet
            continue
        b = out[r.call.agent_ext]
        b["due"] += 1
        b["on_time"] += state == "on_time"
    return out


def _aht(s: Session, lo, hi, exts: list[str]) -> dict[str, float]:
    handle = Call.talk_seconds + func.coalesce(Call.hold_seconds, 0)
    rows = s.execute(select(Call.agent_ext, func.avg(handle)).where(
        Call.direction == "inbound", Call.status == "answered", Call.agent_ext.in_(exts),
        Call.start_time >= lo, Call.start_time < hi).group_by(Call.agent_ext))
    return {e: float(a) for e, a in rows if a is not None}


def compute(s: Session, start: date, end: date, exts: list[str], team_exts: list[str],
            now: datetime | None = None) -> dict:
    """Per customer-service extension in `exts` (others are skipped): the 3 parts and the final KPI."""
    now = now or followups._utcnow()
    exts = [e for e in exts if e in set(team_exts)]
    lo, hi = window(start, end)
    out = {"from": lo.isoformat(), "to": hi.isoformat(), "agents": {}}
    if not exts:
        return out
    ans, forms, aht = _answering(s, lo, hi, exts, team_exts), _forms(s, lo, hi, exts, team_exts, now), \
        _aht(s, lo, hi, exts)
    for e in exts:
        a, f = ans[e], forms[e]
        p1 = band_up(a["answered"] + a["recalled"], a["answered"] + a["missed"])
        p2 = band_up(f["on_time"], f["due"])
        p3 = aht_score(aht.get(e))
        out["agents"][e] = {**a, "answer_score": p1, "forms_due": f["due"], "forms_on_time": f["on_time"],
                            "forms_score": p2, "aht": aht.get(e), "aht_score": p3, "kpi": final(p1, p2, p3)}
    return out


def team_exts(ext_team: dict[str, str]) -> list[str]:
    return sorted(e for e, t in ext_team.items() if t == TEAM)

