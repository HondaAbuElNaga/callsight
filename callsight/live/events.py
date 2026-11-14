"""Parse Yeastar P-Series WebSocket events into small typed objects (tolerant of unknown / missing fields).

Written from the Yeastar docs; no real 30011 payload has been captured yet (NEEDS USER on the office LAN):
- 30011 Call State Changed: {"type": 30011, "sn": "...", "msg": <object or JSON string>} with msg =
  {"call_id": "...", "members": [{"extension": {"number", "channel_id", "member_status", "call_path"?}},
                                  {"inbound": {"from", "to", "trunk_name", "channel_id", "member_status"}}, ...]}
  member types extension / inbound / outbound / internal (we also accept {"type": "extension", ...} members);
  member_status ALERT / RING / ANSWERED / ANSWER / HOLD / BYE / EARLYMEDIA.
- 30012 Call End Details: msg = {"call_id": ..., ...CDR-like fields}; we only need call_id.

Caller numbers are masked HERE (last 4 digits), before anything reaches Redis, the API or a log line.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

CALL_STATE = 30011
CALL_END = 30012
TOPICS = (CALL_STATE, CALL_END)

MEMBER_TYPES = ("extension", "inbound", "outbound", "internal")
RINGING = {"ALERT", "RING", "EARLYMEDIA"}
TALKING = {"ANSWERED", "ANSWER"}
HOLD = {"HOLD"}
GONE = {"BYE"}
MAX_ID = 64
MAX_MEMBERS = 20


def mask_number(value: object) -> str | None:
    """'0551234567' -> '••••4567'. Short numbers (<= 4 digits) are fully hidden too: they are never shown raw."""
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not text:
        return None
    return "••••" + text[-4:] if len(text) > 4 else "••••"


def _ext(value: object) -> str | None:
    """Extension numbers are internal (1-6 digits) and shown as they are; anything else is not an extension."""
    text = str(value or "").strip()
    return text if text.isdigit() and 1 <= len(text) <= 6 else None


@dataclass(frozen=True)
class Member:
    kind: str                     # extension | inbound | outbound | internal | unknown
    channel_id: str
    status: str                   # upper-case member_status ('' when missing)
    ext: str | None = None        # extension members only
    party: str | None = None      # external party, ALREADY MASKED
    queue: str | None = None      # from call_path when it names a queue (best effort)


@dataclass(frozen=True)
class CallState:
    call_id: str
    members: tuple[Member, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CallEnd:
    call_id: str


def _load(raw: object) -> object:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _clean_id(value: object) -> str | None:
    text = str(value if value is not None else "").strip()
    return text[:MAX_ID] if text else None


def _queue_from_path(path: object) -> str | None:
    """call_path is only set for queue / IVR / ring group calls; its format is uncaptured. Best effort: the first
    'queue'-tagged number, else None."""
    if isinstance(path, list):
        for item in path:
            q = _queue_from_path(item)
            if q:
                return q
        return None
    if isinstance(path, dict):
        if str(path.get("type", "")).lower() == "queue":
            return _ext(path.get("number"))
        return None
    text = str(path or "")
    low = text.lower()
    if "queue" in low:
        digits = "".join(ch if ch.isdigit() else " " for ch in low.split("queue", 1)[1]).split()
        return _ext(digits[0]) if digits else None    # never truncate: a long run may be a caller number
    return None


def _member(item: object) -> Member | None:
    if not isinstance(item, dict):
        return None
    kind, body = "unknown", item
    if isinstance(item.get("type"), str) and item["type"].lower() in MEMBER_TYPES:
        kind = item["type"].lower()
    else:
        for k in MEMBER_TYPES:
            if isinstance(item.get(k), dict):
                kind, body = k, item[k]
                break
        else:
            if len(item) == 1 and isinstance(next(iter(item.values())), dict):   # a member type we don't know
                body = next(iter(item.values()))
    channel = _clean_id(body.get("channel_id"))
    if not channel:
        return None
    status = str(body.get("member_status") or "").strip().upper()[:20]
    queue = _queue_from_path(body.get("call_path"))
    if kind == "extension":
        return Member(kind, channel, status, ext=_ext(body.get("number")), queue=queue)
    if kind == "inbound":
        party = body.get("from")
    elif kind == "outbound":
        party = body.get("to")
    else:
        party = body.get("number") or body.get("from")
    # internal members may be extensions; an internal party that looks like an extension stays one
    ext = _ext(body.get("number")) if kind == "internal" else None
    return Member(kind, channel, status, ext=ext, party=None if ext else mask_number(party), queue=queue)


def parse(raw: object) -> CallState | CallEnd | None:
    """None for anything we don't use (other topics, heartbeat replies, broken JSON)."""
    data = _load(raw)
    if not isinstance(data, dict):
        return None
    try:
        topic = int(data.get("type") or 0)
    except (TypeError, ValueError):
        return None
    msg = _load(data.get("msg"))
    if not isinstance(msg, dict):
        return None
    call_id = _clean_id(msg.get("call_id"))
    if not call_id:
        return None
    if topic == CALL_END:
        return CallEnd(call_id)
    if topic != CALL_STATE:
        return None
    members = msg.get("members")
    parsed = [m for m in (_member(x) for x in (members if isinstance(members, list) else [])[:MAX_MEMBERS]) if m]
    return CallState(call_id, tuple(parsed))
