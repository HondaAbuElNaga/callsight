"""Turn the CDR legs of one call (same `uid`) into one classified call.

Pure functions only (no DB, no network), so every rule is unit-tested with real call shapes.
See specs/2026-11-12-call-ingest/spec.md, requirement 4 (status rules).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# Leg kinds
IVR, QUEUE, VOICEMAIL, EXTENSION, EXTERNAL = "ivr", "queue", "voicemail", "extension", "external"
# Call statuses (requirement 4)
ANSWERED, VOICEMAIL_S, ABANDONED_QUEUE, ABANDONED_IVR, BUSY, NO_ANSWER = (
    "answered", "voicemail", "abandoned_queue", "abandoned_ivr", "busy", "no_answer")

_BRACKET_EXT = re.compile(r"<(\d{1,6})>\s*$")
_SHORT_NUM = re.compile(r"^\d{1,6}$")


@dataclass(frozen=True)
class TeamConfig:
    """Which extensions / queues belong to the team, and their team name."""
    ext_team: dict[str, str] = field(default_factory=dict)    # "119" -> "customer_service"
    queue_team: dict[str, str] = field(default_factory=dict)  # "6401" -> "customer_service"

    def team_of(self, number: str | None) -> str | None:
        if not number:
            return None
        return self.ext_team.get(number) or self.queue_team.get(number)


@dataclass
class CallFields:
    uid: str
    direction: str                  # inbound / outbound / internal
    entry: str                      # ivr / queue / direct
    customer_number: str | None
    did_number: str | None
    queue_number: str | None
    team: str | None
    agent_ext: str | None           # who handled it (inbound: first answering ext; outbound: caller ext)
    missed_agent_ext: str | None    # direct call that rang this ext and wasn't answered
    start_time: datetime
    end_time: datetime
    ivr_seconds: int
    wait_seconds: int
    talk_seconds: int
    hold_seconds: int | None        # not in the Yeastar CDR -> None (spec open question 3)
    total_seconds: int
    status: str
    recording_file: str | None
    hangup_by: str | None = None    # outbound only: agent / customer / busy (spec 2026-11-15-ring-time)


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def leg_kind(row: dict) -> str:
    to = (row.get("call_to") or "").strip()
    if to.startswith("IVR"):
        return IVR
    if to.startswith("Queue"):
        return QUEUE
    if to.startswith("Voicemail"):
        return VOICEMAIL
    # Extensions always appear as "Name<ext>"; external numbers (and dialled codes like 51119) don't.
    if _BRACKET_EXT.search(to):
        return EXTENSION
    return EXTERNAL


def to_number(row: dict) -> str | None:
    num = (row.get("call_to_number") or "").strip()
    if num:
        return num
    m = _BRACKET_EXT.search(row.get("call_to") or "")
    return m.group(1) if m else None


def from_ext(row: dict) -> str | None:
    """Caller extension, if the call came from an internal extension."""
    num = (row.get("call_from_number") or "").strip()
    return num if _SHORT_NUM.match(num) else None


def is_team_call(legs: Iterable[dict], team: TeamConfig) -> bool:
    """A call is kept if any leg is from/to a team extension or reaches a team queue."""
    for r in legs:
        if from_ext(r) in team.ext_team:
            return True
        kind, num = leg_kind(r), to_number(r)
        if kind in (EXTENSION, VOICEMAIL) and num in team.ext_team:
            return True
        if kind == QUEUE and num in team.queue_team:
            return True
    return False


FORWARD_TEAM = "forward"      # a branch supervisor's call kept as proof (part 4 D2): never in team views / KPIs


def is_forward_call(legs: Iterable[dict], watch: dict[str, list[tuple[str, datetime]]]) -> bool:
    """Part 4 D2: an outbound call from a watched supervisor ext to a customer whose problem was forwarded to that
    supervisor, made after the forward. watch = ext -> [(customer number's last 9 digits, forwarded_at)]."""
    legs = sorted(legs, key=lambda r: _int(r.get("id")))
    if not legs or not watch or (legs[0].get("call_type") or "").lower() != "outbound":
        return False
    ext = from_ext(legs[0])
    if ext not in watch:
        return False
    ext_legs = [r for r in legs if leg_kind(r) == EXTERNAL]
    number = to_number(ext_legs[0]) if ext_legs else to_number(legs[0])
    d = "".join(ch for ch in (number or "") if ch.isdigit())[-9:]
    start = datetime.fromtimestamp(min(_ts(r) for r in legs), tz=timezone.utc)
    return bool(d) and any(d == want and start > since for want, since in watch[ext])


# who ended an outbound call (spec 2026-11-15-ring-time), from the PBX leg's `reason`
HANGUP_AGENT, HANGUP_CUSTOMER, HANGUP_BUSY = "agent", "customer", "busy"


def hangup_by(direction: str, status: str, leg: dict | None) -> str | None:
    """`src hangup` = our agent ended it, `dst ...busy...` = busy, other `dst ...` = the customer side
    (declined, or the network closed the line after ~62 s). Outbound calls only; unknown reason -> None."""
    if direction != "outbound" or leg is None:
        return None
    if status == BUSY:
        return HANGUP_BUSY
    reason = (leg.get("reason") or "").strip().lower()
    if reason.startswith("src"):
        return HANGUP_AGENT
    if reason.startswith("dst"):
        return HANGUP_BUSY if "busy" in reason else HANGUP_CUSTOMER
    return None


def _ts(row: dict) -> int:
    return _int(row.get("timestamp"))


def classify(legs: list[dict], team: TeamConfig) -> CallFields:
    if not legs:
        raise ValueError("classify() needs at least one leg")
    legs = sorted(legs, key=lambda r: _int(r.get("id")))
    uids = {r.get("uid") for r in legs}
    if len(uids) != 1:
        raise ValueError(f"legs belong to several calls: {uids}")
    first = legs[0]
    kinds = [leg_kind(r) for r in legs]

    call_type = (first.get("call_type") or "").lower()
    direction = call_type if call_type in ("inbound", "outbound", "internal") else "inbound"

    ring_legs = [(r, k) for r, k in zip(legs, kinds) if k in (EXTENSION, EXTERNAL)]
    answered = [(r, k) for r, k in ring_legs if (r.get("disposition") or "").upper() == "ANSWERED"]
    queue_legs = [r for r, k in zip(legs, kinds) if k == QUEUE]
    queue_number = to_number(queue_legs[0]) if queue_legs else None

    # --- status (first match wins) ---
    if answered:
        status = ANSWERED
    elif VOICEMAIL in kinds:
        status = VOICEMAIL_S
    elif queue_legs:
        status = ABANDONED_QUEUE
    elif direction == "inbound" and all(k == IVR for k in kinds):
        status = ABANDONED_IVR
    elif ring_legs and (ring_legs[-1][0].get("disposition") or "").upper() == "BUSY":
        status = BUSY
    else:
        status = NO_ANSWER

    # --- who handled / who missed it ---
    agent_ext = missed_agent = None
    if direction == "outbound":
        agent_ext = from_ext(first)
    elif answered:
        r, k = answered[0]
        agent_ext = to_number(r) if k == EXTENSION else from_ext(r)
    if status != ANSWERED and direction != "outbound":
        missed = [r for r, k in ring_legs if k == EXTENSION]
        if missed:
            missed_agent = to_number(missed[0])
        elif VOICEMAIL in kinds:
            missed_agent = to_number(legs[kinds.index(VOICEMAIL)])

    # --- customer number ---
    if direction == "inbound":
        customer = (first.get("call_from_number") or "").strip() or None
    elif direction == "outbound":
        ext_legs = [r for r, k in ring_legs if k == EXTERNAL]
        customer = to_number(ext_legs[0]) if ext_legs else to_number(first)
    else:
        customer = None

    # --- timings ---
    start_ts = min(_ts(r) for r in legs)
    end_ts = max(_ts(r) + _int(r.get("duration")) for r in legs)
    ivr_seconds = sum(_int(r.get("duration")) for r, k in zip(legs, kinds) if k == IVR)
    talk_seconds = _int(answered[0][0].get("talk_duration")) if answered else 0
    if answered and queue_legs:
        agent_leg = answered[0][0]
        if agent_leg.get("ring_duration") is not None:
            wait_seconds = _int(agent_leg.get("ring_duration"))
        else:
            wait_seconds = max(0, _ts(agent_leg) - _ts(queue_legs[0]))
    elif queue_legs:
        wait_seconds = _int(queue_legs[0].get("duration"))
    elif answered:
        wait_seconds = _int(answered[0][0].get("ring_duration"))
    elif ring_legs and direction == "outbound":     # ring-time spec: ring seconds only, missing = 0
        wait_seconds = _int(ring_legs[0][0].get("ring_duration"))
    elif ring_legs:
        wait_seconds = _int(ring_legs[0][0].get("ring_duration")) or _int(ring_legs[0][0].get("duration"))
    else:
        wait_seconds = 0

    recording = next((r.get("record_file") for r in legs if r.get("record_file")), None)
    team_name = (team.team_of(agent_ext) or team.team_of(missed_agent) or team.team_of(queue_number)
                 or team.team_of(from_ext(first)))
    entry = {IVR: "ivr", QUEUE: "queue"}.get(kinds[0], "direct")

    return CallFields(
        uid=first["uid"],
        direction=direction,
        entry=entry,
        customer_number=customer,
        did_number=(first.get("did_number") or None),
        queue_number=queue_number,
        team=team_name,
        agent_ext=agent_ext,
        missed_agent_ext=missed_agent,
        start_time=datetime.fromtimestamp(start_ts, tz=timezone.utc),
        end_time=datetime.fromtimestamp(end_ts, tz=timezone.utc),
        ivr_seconds=ivr_seconds,
        wait_seconds=wait_seconds,
        talk_seconds=talk_seconds,
        hold_seconds=None,
        total_seconds=max(0, end_ts - start_ts),
        status=status,
        recording_file=recording,
        hangup_by=hangup_by(direction, status,
                            answered[0][0] if answered else (ring_legs[-1][0] if ring_legs else None)),
    )
