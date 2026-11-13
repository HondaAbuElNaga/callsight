"""Call list queries. One `where` builder is shared by the list and the summary, so their counts always agree."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..db.models import Agent, Call, CallLeg
from ..ingest.service import day_bounds

STATUSES = ("answered", "abandoned_queue", "abandoned_ivr", "no_answer", "busy", "voicemail")
DIRECTIONS = ("inbound", "outbound", "internal")
PAGE_SIZE = 50


@dataclass
class CallFilter:
    date_from: date
    date_to: date
    agent: str | None = None
    status: str | None = None
    direction: str | None = None
    q: str | None = None

    def apply(self, stmt: Select) -> Select:
        start, _ = day_bounds(self.date_from)
        _, end = day_bounds(self.date_to)
        stmt = stmt.where(Call.start_time >= start, Call.start_time < end)
        if self.agent:
            stmt = stmt.where(or_(Call.agent_ext == self.agent, Call.missed_agent_ext == self.agent))
        if self.status:
            stmt = stmt.where(Call.status == self.status)
        if self.direction:
            stmt = stmt.where(Call.direction == self.direction)
        if self.q:
            stmt = stmt.where(Call.customer_number.contains(self.q, autoescape=True))
        return stmt


def _row(call: Call, agent_name: str | None) -> dict:
    return {
        "id": call.id,
        "start_time": call.start_time.isoformat(),
        "direction": call.direction,
        "entry": call.entry,
        "customer_number": call.customer_number,
        "queue_number": call.queue_number,
        "agent_ext": call.agent_ext or call.missed_agent_ext,
        "agent_name": agent_name,
        "answered_by_agent": call.agent_ext is not None,
        "status": call.status,
        "ivr_seconds": call.ivr_seconds,
        "wait_seconds": call.wait_seconds,
        "talk_seconds": call.talk_seconds,
        "total_seconds": call.total_seconds,
        "has_recording": call.recording_status == "ok",
    }


def list_calls(session: Session, f: CallFilter, page: int = 1) -> dict:
    agent = aliased(Agent)
    ext = func.coalesce(Call.agent_ext, Call.missed_agent_ext)
    base = f.apply(select(Call, agent.name).outerjoin(agent, agent.ext == ext))
    total = session.scalar(f.apply(select(func.count()).select_from(Call)))
    rows = session.execute(base.order_by(Call.start_time.desc(), Call.id.desc())
                           .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    return {"total": total or 0, "page": page, "page_size": PAGE_SIZE,
            "items": [_row(c, name) for c, name in rows]}


def summary(session: Session, f: CallFilter) -> dict:
    counts = dict(session.execute(f.apply(select(Call.status, func.count()).select_from(Call))
                                  .group_by(Call.status)).all())
    return {"total": sum(counts.values()), "by_status": {s: counts.get(s, 0) for s in STATUSES}}


def call_detail(session: Session, call_id: int) -> dict | None:
    call = session.get(Call, call_id)
    if call is None:
        return None
    ext = call.agent_ext or call.missed_agent_ext
    name = session.get(Agent, ext).name if ext and session.get(Agent, ext) else None
    legs = session.scalars(select(CallLeg).where(CallLeg.call_id == call_id).order_by(CallLeg.id)).all()
    return {**_row(call, name), "legs": [{
        "id": leg.id, "kind": leg.kind, "start_time": leg.start_time.isoformat(), "to_number": leg.to_number,
        "disposition": leg.disposition, "duration": leg.duration, "ring_duration": leg.ring_duration,
        "talk_duration": leg.talk_duration, "reason": leg.reason} for leg in legs]}


def team_agents(session: Session) -> list[dict]:
    return [{"ext": a.ext, "name": a.name, "team": a.team}
            for a in session.scalars(select(Agent).where(Agent.in_team).order_by(Agent.ext))]
