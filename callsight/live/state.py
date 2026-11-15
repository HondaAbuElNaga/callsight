"""In-memory board state built from 30011/30012 events (spec live-board).

No snapshot API is assumed (decision 6): on every (re)connect the state is cleared and the board shows
"syncing" until events arrive. Calls whose end event got lost are pruned after 4 h; at most 500 calls (decision 8).

Team only (user, 2026-11-16): with a TeamConfig, every call is still tracked (an IVR call may ring a team agent later),
but only calls that touched a team extension or a team queue are published; other calls never leave this process.
Once a call touched the team it stays on the board until it ends (a transfer out is still the team's call).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ingest.classify import TeamConfig
from .events import GONE, HOLD, RINGING, TALKING, CallEnd, CallState, Member

MAX_CALLS = 500
MAX_AGE = 4 * 3600


@dataclass
class LiveCall:
    call_id: str
    started_at: float
    updated_at: float
    members: dict[str, Member] = field(default_factory=dict)   # by channel_id
    answered_at: float | None = None
    queue: str | None = None
    team: bool = False          # touched a team extension / queue at some point (sticky)

    @property
    def state(self) -> str:
        """From the extension members: the trunk side of an inbound call is already ANSWERED by the PBX while
        the queue still rings the agents. No extension member yet = waiting (IVR / queue)."""
        ext = {m.status for m in self.members.values() if m.ext}
        if ext & HOLD or (ext & TALKING and {m.status for m in self.members.values()} & HOLD):
            return "holding"
        if ext & TALKING:
            return "talking"
        if ext & RINGING:
            return "ringing"
        return "waiting"

    @property
    def direction(self) -> str:
        kinds = {m.kind for m in self.members.values()}
        if "inbound" in kinds:
            return "inbound"
        if "outbound" in kinds:
            return "outbound"
        return "internal"

    def to_dict(self, team_exts=None) -> dict:
        """team_exts: only these extensions are named (a team call transferred to another department does not show
        that department's extension numbers)."""
        exts = [m for m in self.members.values() if m.ext and (team_exts is None or m.ext in team_exts)]
        talking = sorted({m.ext for m in exts if m.status in TALKING | HOLD})
        ringing = sorted({m.ext for m in exts if m.status in RINGING} - set(talking))
        party = next((m.party for m in self.members.values() if m.party), None)   # masked already
        return {"call_id": self.call_id, "state": self.state, "direction": self.direction,
                "agents": talking, "ringing": ringing, "caller": party, "queue": self.queue,
                "started_at": round(self.started_at, 3),
                "answered_at": round(self.answered_at, 3) if self.answered_at else None}


class LiveState:
    def __init__(self, max_calls: int = MAX_CALLS, max_age: float = MAX_AGE, team: TeamConfig | None = None):
        self.calls: dict[str, LiveCall] = {}
        self.max_calls, self.max_age = max_calls, max_age
        self.team = team            # None = every call is shown (tests, no teams.yaml)

    def _touches_team(self, m: Member) -> bool:
        return self.team is None or (m.ext is not None and m.ext in self.team.ext_team) \
            or (m.queue is not None and m.queue in self.team.queue_team)

    def reset(self) -> list[dict]:
        self.calls.clear()
        return [{"op": "reset"}]

    def snapshot(self) -> list[dict]:
        return [self._out(c) for c in sorted(self.calls.values(), key=lambda c: c.started_at) if c.team]

    def _out(self, call: LiveCall) -> dict:
        return call.to_dict(None if self.team is None else self.team.ext_team)

    def apply(self, event: CallState | CallEnd, now: float) -> list[dict]:
        """Returns the deltas to publish: {"op": "upsert", "call": {...}} / {"op": "remove", "call_id": ...}."""
        if isinstance(event, CallEnd):
            return self._remove(event.call_id)
        call = self.calls.get(event.call_id)
        out: list[dict] = []
        if call is None:
            if not any(m.status not in GONE for m in event.members):
                return []                       # only BYEs for a call we never saw
            out += self._make_room()
            call = self.calls[event.call_id] = LiveCall(event.call_id, started_at=now, updated_at=now)
        for m in event.members:
            if m.status in GONE:
                call.members.pop(m.channel_id, None)
            else:
                call.members[m.channel_id] = m
                if m.queue and not call.queue:
                    call.queue = m.queue
                if not call.team and self._touches_team(m):
                    call.team = True
        if not call.members:
            return out + self._remove(call.call_id)
        call.updated_at = now
        if call.state in ("talking", "holding") and call.answered_at is None:
            call.answered_at = now
        return out + ([{"op": "upsert", "call": self._out(call)}] if call.team else [])

    def prune(self, now: float) -> list[dict]:
        """Calls older than max_age (their end event got lost)."""
        old = [cid for cid, c in self.calls.items() if now - c.started_at > self.max_age]
        return [d for cid in old for d in self._remove(cid)]

    def _make_room(self) -> list[dict]:
        out: list[dict] = []
        while len(self.calls) >= self.max_calls:
            oldest = min(self.calls.values(), key=lambda c: c.updated_at)
            out += self._remove(oldest.call_id)
        return out

    def _remove(self, call_id: str) -> list[dict]:
        call = self.calls.pop(call_id, None)
        return [{"op": "remove", "call_id": call_id}] if call is not None and call.team else []
