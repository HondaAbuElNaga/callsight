"""Call list queries. One `where` builder is shared by the list and the summary, so their counts always agree."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, and_, exists, false, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..db.models import Agent, AppUser, Call, CallLeg, CallShare, Transcript
from ..ingest.service import day_bounds
from ..transcripts import displayed_order
from .auth import STAFF_ROLES
from ..followups import ring_info

STATUSES = ("answered", "abandoned_queue", "abandoned_ivr", "no_answer", "busy", "voicemail")
DIRECTIONS = ("inbound", "outbound", "internal")
PAGE_SIZE = 50


@dataclass(frozen=True)
class Scope:
    """Which calls a user may see. Staff: all. Agent: only calls they answered/made (`agent_ext`). Else: none."""
    everything: bool = False
    agent_ext: str | None = None
    user_id: int | None = None          # agents: calls shared with this user open one by one (never in the list)

    def where(self, stmt: Select) -> Select:
        if self.everything:
            return stmt
        if self.agent_ext:
            return stmt.where(Call.agent_ext == self.agent_ext)
        return stmt.where(false())

    def allows(self, call: Call) -> bool:
        return self.everything or (bool(self.agent_ext) and call.agent_ext == self.agent_ext)

    @property
    def is_agent(self) -> bool:
        return not self.everything


def visible_scope(user: AppUser) -> Scope:
    """The one place that decides call visibility (deny by default: an unknown role or an agent without ext sees
    nothing). Used by CallFilter.apply (list, summary, search, exports) and by every single-call endpoint."""
    if user.role in STAFF_ROLES:
        return Scope(everything=True)
    if user.role == "agent" and user.ext:
        return Scope(agent_ext=user.ext, user_id=user.id)
    return Scope()


@dataclass
class CallFilter:
    scope: Scope                       # required: no query without a visibility scope
    date_from: date
    date_to: date
    agent: str | None = None
    status: str | None = None
    direction: str | None = None
    q: str | None = None
    words: tuple[str, ...] = ()          # normalised transcript words (all must occur); never logged
    asr_model: str = "whisper-large-v3"  # decides the displayed transcript when a call has several

    def apply(self, stmt: Select) -> Select:
        start, _ = day_bounds(self.date_from)
        _, end = day_bounds(self.date_to)
        stmt = self.scope.where(stmt)
        stmt = stmt.where(Call.start_time >= start, Call.start_time < end)
        if self.agent:
            stmt = stmt.where(or_(Call.agent_ext == self.agent, Call.missed_agent_ext == self.agent))
        if self.status:
            stmt = stmt.where(Call.status == self.status)
        if self.direction:
            stmt = stmt.where(Call.direction == self.direction)
        if self.q:
            stmt = stmt.where(Call.customer_number.contains(self.q, autoescape=True))
        if self.words:
            stmt = stmt.where(self._transcript_match())
        return stmt

    def _transcript_match(self):
        """One EXISTS on the displayed done transcript of the call: a call is never counted twice."""
        t = aliased(Transcript)
        shown = (select(Transcript.id).where(Transcript.call_id == Call.id, Transcript.status == "done")
                 .order_by(*displayed_order(self.asr_model)).limit(1).correlate(Call).scalar_subquery())
        return exists().where(t.call_id == Call.id, t.id == shown,
                              and_(*(t.search_text.contains(w, autoescape=True) for w in self.words)))


def _row(call: Call, agent_name: str | None, transcript_match: bool = False) -> dict:
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
        # outbound only: ring seconds, who ended it, early hang-up (spec 2026-11-15-ring-time)
        "ring": ring_info(call),
        "has_recording": call.recording_status == "ok",
        "transcript_match": transcript_match,     # badge only: no snippet (text is shown only with an audit row)
    }


def list_calls(session: Session, f: CallFilter, page: int = 1) -> dict:
    agent = aliased(Agent)
    ext = func.coalesce(Call.agent_ext, Call.missed_agent_ext)
    base = f.apply(select(Call, agent.name).outerjoin(agent, agent.ext == ext))
    total = session.scalar(f.apply(select(func.count()).select_from(Call)))
    rows = session.execute(base.order_by(Call.start_time.desc(), Call.id.desc())
                           .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    return {"total": total or 0, "page": page, "page_size": PAGE_SIZE,
            # agents hear only calls a supervisor shared with them (opened one by one, never from a list)
            "items": [{**_row(c, name, bool(f.words)), **({"has_recording": False} if f.scope.is_agent else {})}
                      for c, name in rows]}


def summary(session: Session, f: CallFilter) -> dict:
    counts = dict(session.execute(f.apply(select(Call.status, func.count()).select_from(Call))
                                  .group_by(Call.status)).all())
    return {"total": sum(counts.values()), "by_status": {s: counts.get(s, 0) for s in STATUSES}}


def visible_call(session: Session, call_id: int, scope: Scope) -> Call | None:
    """The call if this scope may see it; None (-> 404, so other agents' call ids are not even confirmed)."""
    call = session.get(Call, call_id)
    if call is None:
        return None
    if scope.allows(call) or is_shared_with(session, call_id, scope):
        return call
    return None


def is_shared_with(session: Session, call_id: int, scope: Scope) -> bool:
    """A supervisor shared this call with this agent (agent-supervisor spec, scope item 7)."""
    if scope.everything or not scope.agent_ext or not scope.user_id:
        return False
    return session.scalar(select(CallShare.id).where(CallShare.call_id == call_id,
                                                     CallShare.to_user_id == scope.user_id).limit(1)) is not None


def may_hear(session: Session, call: Call, scope: Scope) -> bool:
    """Staff hear every recording; an agent only the calls a supervisor shared with them (2026-11-15)."""
    return scope.everything or is_shared_with(session, call.id, scope)


def call_detail(session: Session, call_id: int, scope: Scope) -> dict | None:
    call = visible_call(session, call_id, scope)
    if call is None:
        return None
    ext = call.agent_ext or call.missed_agent_ext
    name = session.get(Agent, ext).name if ext and session.get(Agent, ext) else None
    legs = session.scalars(select(CallLeg).where(CallLeg.call_id == call_id).order_by(CallLeg.id)).all()
    if scope.is_agent:   # an agent never sees which colleague rang before them
        legs = [leg for leg in legs if leg.kind not in ("extension", "voicemail") or leg.to_number == scope.agent_ext]
    row = _row(call, name)
    row["has_recording"] = row["has_recording"] and may_hear(session, call, scope)
    return {**row, "legs": [{
        "id": leg.id, "kind": leg.kind, "start_time": leg.start_time.isoformat(), "to_number": leg.to_number,
        "disposition": leg.disposition, "duration": leg.duration, "ring_duration": leg.ring_duration,
        "talk_duration": leg.talk_duration, "reason": leg.reason} for leg in legs]}


def team_agents(session: Session, scope: Scope) -> list[dict]:
    stmt = select(Agent).where(Agent.in_team).order_by(Agent.ext)
    if not scope.everything:
        stmt = stmt.where(Agent.ext == scope.agent_ext) if scope.agent_ext else stmt.where(false())
    return [{"ext": a.ext, "name": a.name, "team": a.team} for a in session.scalars(stmt)]
