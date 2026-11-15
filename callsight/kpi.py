"""KPI engine: periods (Sunday weeks / calendar months, Riyadh time) and KPI aggregates in SQL.

Definitions: product/mission.md + specs/2026-11-14-kpi-reports/spec.md ("Decisions" 1-6).
Internal calls never count. AHT = talk (+ hold when known; the CDR has none) over answered calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session

from .config import RIYADH
from .db.models import Agent, Call, CallLeg, ReconcileRun
from .ingest.classify import TeamConfig
from .ingest.service import day_bounds

PERIODS = ("week", "month")
STATUSES = ("answered", "abandoned_queue", "abandoned_ivr", "no_answer", "busy", "voicemail")
UNANSWERED = STATUSES[1:]
REPEAT_HOURS = 72
TRUSTED_DAYS = 7


# ---------- periods ----------

@dataclass(frozen=True)
class Period:
    kind: str       # week / month
    start: date
    end: date       # exclusive

    def bounds(self) -> tuple[datetime, datetime]:
        return day_bounds(self.start)[0], day_bounds(self.end)[0]

    def previous(self) -> "Period":
        if self.kind == "range":            # same number of days, just before
            return Period("range", self.start - (self.end - self.start), self.start)
        return period_for(self.kind, self.start - timedelta(days=1))

    def is_partial(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) < self.bounds()[1]

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days)]

    def as_dict(self, now: datetime | None = None) -> dict:
        return {"kind": self.kind, "start": self.start.isoformat(),
                "end": (self.end - timedelta(days=1)).isoformat(), "partial": self.is_partial(now)}


def period_for(kind: str, day: date) -> Period:
    """The week (Sunday..Saturday) or calendar month that contains `day`."""
    if kind == "week":
        start = day - timedelta(days=(day.weekday() + 1) % 7)   # Python: Monday=0 .. Sunday=6
        return Period(kind, start, start + timedelta(days=7))
    if kind == "month":
        start = day.replace(day=1)
        end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        return Period(kind, start, end)
    raise ValueError(f"period must be one of {PERIODS}")


MAX_RANGE_DAYS = 366


def date_range(first: date, last: date) -> Period:
    """Any days chosen by the user, `last` included (shown as من … إلى …)."""
    if last < first:
        raise ValueError("the end date is before the start date")
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"at most {MAX_RANGE_DAYS} days")
    return Period("range", first, last + timedelta(days=1))


def last_closed(kind: str, now: datetime | None = None) -> Period:
    """The latest period that has fully ended (for scheduled reports)."""
    today = (now or datetime.now(timezone.utc)).astimezone(RIYADH).date()
    return period_for(kind, today).previous()


# ---------- helpers ----------

def _f(v) -> float | None:
    return float(v) if v is not None else None


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _riyadh_day(col):
    return func.date(func.timezone("Asia/Riyadh", col))


def team_names(teams: TeamConfig) -> list[str]:
    return sorted(set(teams.ext_team.values()) | set(teams.queue_team.values()))


def _scope(teams: TeamConfig, team: str | None, agent: str | None) -> tuple[list[str], list[str]]:
    """(team names, agent extensions) for the chosen scope; raises ValueError for unknown team/agent."""
    if team is not None and team not in team_names(teams):
        raise ValueError(f"unknown team {team!r}")
    if agent is not None:
        if agent not in teams.ext_team:
            raise ValueError(f"extension {agent!r} is not in teams.yaml")
        if team is not None and teams.ext_team[agent] != team:
            raise ValueError(f"extension {agent!r} is not in team {team!r}")
        return [teams.ext_team[agent]], [agent]
    names = [team] if team else team_names(teams)
    return names, sorted((e for e, t in teams.ext_team.items() if t in names), key=lambda e: (len(e), e))


def mask_number(number: str | None) -> str:
    """Privacy in PDFs: '0501234567' -> '05XX…4567'. Works on the digits only and always hides at least 4 of them:
    the prefix is shown only for numbers of 10+ digits, shorter ones show just the last 3-4 digits."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if not digits:
        return "–"
    if len(digits) >= 10:
        return f"{digits[:2]}XX…{digits[-4:]}"
    if len(digits) >= 8:
        return f"XX…{digits[-4:]}"
    if len(digits) >= 6:
        return f"XX…{digits[-3:]}"
    return "X" * len(digits)


# ---------- queries (one per KPI group) ----------

_HANDLE = Call.talk_seconds + func.coalesce(Call.hold_seconds, 0)
_ANSWERED = Call.status == "answered"


def _team_inbound(s: Session, lo, hi, names) -> tuple[dict, dict]:
    where = (Call.direction == "inbound", Call.team.in_(names), Call.start_time >= lo, Call.start_time < hi)
    calls, answered, aht, ring = s.execute(select(
        func.count(), func.count().filter(_ANSWERED), func.avg(_HANDLE).filter(_ANSWERED),
        func.avg(Call.wait_seconds).filter(_ANSWERED)).where(*where)).one()
    by = dict(s.execute(select(Call.status, func.count()).where(*where).group_by(Call.status)).all())
    unanswered = sum(by.get(st, 0) for st in UNANSWERED)
    return ({"calls": calls, "answered": answered, "unanswered": unanswered, "answer_rate": _rate(answered, calls),
             "aht": _f(aht), "avg_ring": _f(ring)}, {st: by.get(st, 0) for st in STATUSES})


def _team_outbound(s: Session, lo, hi, names) -> dict:
    n, ans, aht = s.execute(select(
        func.count(), func.count().filter(_ANSWERED), func.avg(Call.talk_seconds).filter(_ANSWERED))
        .where(Call.direction == "outbound", Call.team.in_(names), Call.start_time >= lo, Call.start_time < hi)).one()
    return {"outbound": n, "outbound_answered": ans, "outbound_aht": _f(aht)}


def _agent_inbound(s: Session, lo, hi, exts) -> dict[str, tuple[int, float | None]]:
    rows = s.execute(select(Call.agent_ext, func.count(), func.avg(_HANDLE))
                     .where(Call.direction == "inbound", _ANSWERED, Call.agent_ext.in_(exts),
                            Call.start_time >= lo, Call.start_time < hi).group_by(Call.agent_ext))
    return {e: (n, _f(a)) for e, n, a in rows}


def _agent_outbound(s: Session, lo, hi, exts) -> dict[str, tuple[int, int, float | None]]:
    rows = s.execute(select(Call.agent_ext, func.count(), func.count().filter(_ANSWERED),
                            func.avg(Call.talk_seconds).filter(_ANSWERED))
                     .where(Call.direction == "outbound", Call.agent_ext.in_(exts),
                            Call.start_time >= lo, Call.start_time < hi).group_by(Call.agent_ext))
    return {e: (n, ans, _f(a)) for e, n, ans, a in rows}


def _missed_rings_where(lo, hi, exts):
    """Extension legs to the agent not answered, once per inbound call (a later pick-up by the same agent doesn't count)."""
    return (CallLeg.kind == "extension", func.coalesce(func.upper(CallLeg.disposition), "") != "ANSWERED",
            CallLeg.to_number.in_(exts), Call.direction == "inbound",
            Call.agent_ext.is_distinct_from(CallLeg.to_number), Call.start_time >= lo, Call.start_time < hi)


def _missed_rings(s: Session, lo, hi, exts) -> dict[str, int]:
    rows = s.execute(select(CallLeg.to_number, func.count(func.distinct(CallLeg.call_id)))
                     .join(Call, Call.id == CallLeg.call_id).where(*_missed_rings_where(lo, hi, exts))
                     .group_by(CallLeg.to_number))
    return dict(rows.all())


_NORM = "right(regexp_replace({0}.customer_number, '[^0-9]', '', 'g'), 9)"
_REPEATS = text(f"""
SELECT c.id, c.customer_number, {_NORM.format('c')} AS num,
  (SELECT p.agent_ext FROM call p
    WHERE p.team = c.team AND p.direction IN ('inbound', 'outbound') AND p.status = 'answered'
      AND {_NORM.format('p')} = {_NORM.format('c')}
      AND p.start_time >= c.start_time - make_interval(hours => :hours) AND p.start_time < c.start_time
    ORDER BY p.start_time DESC, p.id DESC LIMIT 1) AS credited
FROM call c
WHERE c.direction = 'inbound' AND c.status = 'answered' AND c.team IN :names
  AND c.start_time >= :lo AND c.start_time < :hi
  AND c.customer_number IS NOT NULL AND {_NORM.format('c')} <> ''
  AND EXISTS (SELECT 1 FROM call p
    WHERE p.team = c.team AND p.direction IN ('inbound', 'outbound') AND p.id <> c.id
      AND {_NORM.format('p')} = {_NORM.format('c')}
      AND p.start_time >= c.start_time - make_interval(hours => :hours) AND p.start_time < c.start_time)
""").bindparams(bindparam("names", expanding=True))


def _repeats(s: Session, lo, hi, names) -> list:
    """One row per repeat call: (id, customer_number, num, credited ext of the latest earlier answered call or None)."""
    return list(s.execute(_REPEATS, {"names": names, "lo": lo, "hi": hi, "hours": REPEAT_HOURS}))


def _top_repeaters(rows, n: int = 5) -> list[dict]:
    """Most repeated callers, numbers masked (privacy: PDFs and the page never show full numbers)."""
    by_num: dict[str, list] = {}
    for r in rows:
        by_num.setdefault(r.num, []).append(r)
    top = sorted(by_num.values(), key=lambda rs: (-len(rs), rs[0].num))[:n]
    return [{"number": mask_number(rs[0].customer_number), "repeats": len(rs)} for rs in top]


def _days(s: Session, period_days: list[date], lo, hi, names, agent: str | None) -> list[dict]:
    day = _riyadh_day(Call.start_time)
    if agent is None:
        rows = s.execute(select(day, func.count(), func.count().filter(_ANSWERED))
                         .where(Call.direction == "inbound", Call.team.in_(names),
                                Call.start_time >= lo, Call.start_time < hi).group_by(day))
        got = {d: (n, a, n - a) for d, n, a in rows}
    else:
        answered = dict(s.execute(select(day, func.count()).where(
            Call.direction == "inbound", _ANSWERED, Call.agent_ext == agent,
            Call.start_time >= lo, Call.start_time < hi).group_by(day)).all())
        missed = dict(s.execute(select(day, func.count(func.distinct(CallLeg.call_id)))
                                .join(Call, Call.id == CallLeg.call_id)
                                .where(*_missed_rings_where(lo, hi, [agent])).group_by(day)).all())
        got = {d: (answered.get(d, 0), answered.get(d, 0), missed.get(d, 0)) for d in set(answered) | set(missed)}
    return [{"day": d, "calls": got.get(d, (0, 0, 0))[0], "answered": got.get(d, (0, 0, 0))[1],
             "unanswered": got.get(d, (0, 0, 0))[2]} for d in period_days]


# ---------- public ----------

def compute(session: Session, start: date, end: date, *, teams: TeamConfig, team: str | None = None,
            agent: str | None = None) -> dict:
    """All KPIs for [start, end) Riyadh days and one scope: all teams, one team, or one agent (teams.yaml)."""
    names, exts = _scope(teams, team, agent)
    period_days = Period("custom", start, end).days()
    lo, hi = day_bounds(start)[0], day_bounds(end)[0]
    inbound, by_status = _team_inbound(session, lo, hi, names)
    repeat_rows = _repeats(session, lo, hi, names)
    credited = [r.credited for r in repeat_rows]
    a_in, a_out = _agent_inbound(session, lo, hi, exts), _agent_outbound(session, lo, hi, exts)
    missed = _missed_rings(session, lo, hi, exts)
    names_by_ext = dict(session.execute(select(Agent.ext, Agent.name).where(Agent.ext.in_(exts))).all())
    rows = [{"ext": e, "name": names_by_ext.get(e) or "", "team": teams.ext_team[e],
             "calls": a_in.get(e, (0, None))[0], "aht": a_in.get(e, (0, None))[1], "missed_rings": missed.get(e, 0),
             "outbound": a_out.get(e, (0, 0, None))[0], "outbound_answered": a_out.get(e, (0, 0, None))[1],
             "outbound_aht": a_out.get(e, (0, 0, None))[2], "repeats": sum(1 for c in credited if c == e)}
            for e in exts]
    if agent is None:
        # credited to nobody, or to someone without an agent row (e.g. supervisor 114) -> "unattributed", so that
        # repeats = sum of agent rows + unattributed always holds
        summary = {**inbound, **_team_outbound(session, lo, hi, names), "repeats": len(credited),
                   "repeats_unattributed": sum(1 for c in credited if c not in exts)}
    else:
        r = rows[0]
        summary = {"calls": r["calls"], "answered": r["calls"], "unanswered": r["missed_rings"], "answer_rate": None,
                   "aht": r["aht"], "avg_ring": None, "outbound": r["outbound"],
                   "outbound_answered": r["outbound_answered"], "outbound_aht": r["outbound_aht"],
                   "repeats": r["repeats"], "repeats_unattributed": None}
        by_status = None
    summary["satisfaction"] = None   # Phase 3: shown as "غير متاح", never 0
    if agent is not None:
        repeat_rows = [r for r in repeat_rows if r.credited == agent]
    return {"summary": summary, "agents": rows, "by_status": by_status, "top_repeaters": _top_repeaters(repeat_rows),
            "days": _days(session, period_days, lo, hi, names, agent)}


def data_trusted(session: Session, need: int = TRUSTED_DAYS, today: date | None = None) -> bool:
    """True when the latest `need` reconciled days are consecutive and OK (latest run of each day counts)."""
    latest: dict[date, bool] = {}
    for day, ok in session.execute(select(ReconcileRun.day, ReconcileRun.ok)
                                   .order_by(ReconcileRun.day.desc(), ReconcileRun.id.desc()).limit(need * 10)):
        latest.setdefault(day, ok)
    days = sorted(latest, reverse=True)[:need]
    if len(days) < need:
        return False
    today = today or datetime.now(RIYADH).date()
    recent = days[0] >= today - timedelta(days=2)   # reconcile stopped -> banner comes back
    return recent and all(latest[d] for d in days) and (days[0] - days[-1]).days == need - 1


def report(session: Session, period: Period, teams: TeamConfig, team: str | None = None, agent: str | None = None,
           now: datetime | None = None) -> dict:
    """KPIs of a period + the previous period's summary (trend) + flags for the page and the PDF."""
    from . import followups
    cur = compute(session, period.start, period.end, teams=teams, team=team, agent=agent)
    prev_p = period.previous()
    prev = compute(session, prev_p.start, prev_p.end, teams=teams, team=team, agent=agent)["summary"]
    lo, hi = period.bounds()
    return {"period": period.as_dict(now), "previous_period": prev_p.as_dict(now),
            "scope": {"team": team, "agent": agent}, "trusted": data_trusted(session),
            **cur, "previous": prev,
            # agent-supervisor spec: forms + callbacks (show-only until the admin sets followup_kpi_since)
            "followup": followups.kpi(session, lo, hi, _scope(teams, team, agent)[1], now,
                                      callers=sorted(teams.ext_team))}


# ---------- plain text (CLI, hand-calculation check) ----------

def _txt(v, key: str) -> str:
    if v is None:
        return "n/a" if key == "satisfaction" else "-"
    if key == "answer_rate":
        return f"{v * 100:.1f}%"
    if key in ("aht", "avg_ring", "outbound_aht"):
        return f"{v:.1f}s"
    return str(v)


def format_text(data: dict) -> str:
    p, pp, sc = data["period"], data["previous_period"], data["scope"]
    scope = f"agent {sc['agent']}" if sc["agent"] else (sc["team"] or "all teams")
    lines = [f"{p['kind']} {p['start']} .. {p['end']}{' (partial)' if p['partial'] else ''} | scope: {scope} | "
             f"previous: {pp['start']} .. {pp['end']} | data trusted (7 OK reconciles): "
             f"{'yes' if data['trusted'] else 'NO'}",
             f"  {'KPI':22} {'period':>10} {'previous':>10}"]
    for key, cur in data["summary"].items():
        lines.append(f"  {key:22} {_txt(cur, key):>10} {_txt(data['previous'].get(key), key):>10}")
    if data["by_status"]:
        lines.append("  inbound by status: " + ", ".join(f"{k} {v}" for k, v in data["by_status"].items()))
    lines.append("  per day: " + " ".join(f"{d['day']:%m-%d}:{d['calls']}/{d['answered']}" for d in data["days"]))
    lines.append(f"  {'agent':12} {'team':17} {'calls':>5} {'aht':>7} {'missed':>6} {'out(ans)':>9} "
                 f"{'out_aht':>7} {'repeats':>7}")
    for a in data["agents"]:
        lines.append(f"  {a['ext'] + ' ' + a['name'][:7]:12} {a['team']:17} {a['calls']:>5} {_txt(a['aht'], 'aht'):>7} "
                     f"{a['missed_rings']:>6} {str(a['outbound']) + '(' + str(a['outbound_answered']) + ')':>9} "
                     f"{_txt(a['outbound_aht'], 'aht'):>7} {a['repeats']:>7}")
    return "\n".join(lines)
