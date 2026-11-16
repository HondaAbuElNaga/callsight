"""The supervisors' forwards page (spec 2026-11-16-priority-no-followup, "Part 4: forwards page"): every forwarded
problem kept after it closes (the archive), the full story of one problem (what the agent sent, what each person
wrote on the ticket, his calls, solved or not), and the numbers for a period: who solves, who lets the 24 h run out,
how long it takes. Staff only (the API checks the role). Read-only: nothing here changes a ticket.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import RIYADH
from .db.models import Agent, Call, CallFollowup, ForwardEvent, ForwardHop, ForwardPerson
from .forward import WATCH_MAX, _sup_calls, closed, digits9, proof

LIST_LIMIT = 500
MAX_DAYS = 366
STATES = ("open", "solved", "unsolved")
GROUP_LABEL = {"men": "رجال", "women": "نساء"}
EVENT_MAX = 200          # timeline items shown per hop


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    lo = datetime(start.year, start.month, start.day, tzinfo=RIYADH)
    hi = datetime(end.year, end.month, end.day, tzinfo=RIYADH) + timedelta(days=1)
    return lo.astimezone(timezone.utc), hi.astimezone(timezone.utc)


def _load(s: Session, start: date, end: date):
    """Forms sent in the period (by forwarded_at, Saudi days) + their hops + the people."""
    lo, hi = _bounds(start, end)
    rows = s.execute(select(CallFollowup, Call).join(Call, Call.id == CallFollowup.call_id).where(
        CallFollowup.responsible_kind == "forward", CallFollowup.forward_status == "sent",
        CallFollowup.forwarded_at >= lo, CallFollowup.forwarded_at < hi, Call.content_deleted_at.is_(None))
        .order_by(CallFollowup.forwarded_at.desc(), Call.id.desc())).all()
    ids = [c.id for _, c in rows]
    hops: dict[int, list[ForwardHop]] = defaultdict(list)
    if ids:
        for h in s.scalars(select(ForwardHop).where(ForwardHop.call_id.in_(ids)).order_by(ForwardHop.id)):
            hops[h.call_id].append(h)
    pids = {h.person_id for hs in hops.values() for h in hs}
    people = {p.id: p for p in s.scalars(select(ForwardPerson).where(ForwardPerson.id.in_(pids)))} if pids else {}
    return rows, hops, people


def bkey(p: ForwardPerson | None) -> str | None:
    """A branch with its group: men's and women's branches can share a name."""
    if p is None or not p.branch:
        return None
    g = GROUP_LABEL.get(p.grp or "", p.grp)
    return f"{p.branch} · {g}" if g and g not in p.branch else p.branch


def done_at(h: ForwardHop) -> datetime | None:
    """When he solved it: his close on the ticket (not when our run noticed it, e.g. after the score came)."""
    return h.closed_at if closed(h) and h.closed_at else h.ended_at


def _state(hs: list[ForwardHop]) -> str:
    if any(h.result == "solved" for h in hs):
        return "solved"
    if not hs or any(h.result in ("open", "pending") for h in hs):
        return "open"
    return "unsolved"


def _line(f: CallFollowup, c: Call, hs: list[ForwardHop], people: dict, names: dict) -> dict:
    solved = [h for h in hs if h.result == "solved"]
    s_hop = solved[-1] if solved else None
    first = people.get(hs[0].person_id) if hs else None
    open_ = [h for h in hs if h.result in ("open", "pending")]
    top = max((h.level for h in open_), default=0)
    end = done_at(s_hop) if s_hop else None
    took = (end - f.forwarded_at).total_seconds() if end and f.forwarded_at else None
    return {
        "call_id": c.id, "customer_number": c.customer_number, "agent_ext": c.agent_ext,
        "agent_name": names.get(c.agent_ext or "", ""), "priority": f.priority, "ticket_number": f.forward_ticket_number,
        "forwarded_at": _iso(f.forwarded_at), "state": _state(hs),
        "level": max((h.level for h in hs), default=None),
        "first": {"name": first.name if first else None, "branch": bkey(first)},
        "holders": [people[h.person_id].name for h in open_ if h.level == top and h.person_id in people],
        "solved_by": ({"name": people[s_hop.person_id].name if s_hop.person_id in people else None,
                       "level": s_hop.level} if s_hop else None),
        "solved_at": _iso(end), "seconds_to_solve": took,
    }


def listing(s: Session, start: date, end: date, *, state: str | None = None, level: int | None = None,
            branch: str | None = None, person_id: int | None = None) -> dict:
    rows, hops, people = _load(s, start, end)
    names = dict(s.execute(select(Agent.ext, Agent.name)).all())
    items = []
    for f, c in rows:
        hs = hops.get(c.id, [])
        if state and _state(hs) != state:
            continue
        if level and max((h.level for h in hs), default=0) != level:
            continue
        if branch and not (hs and bkey(people.get(hs[0].person_id)) == branch):   # the branch it was sent to
            continue
        if person_id and not any(h.person_id == person_id for h in hs):
            continue
        items.append(_line(f, c, hs, people, names))
    return {"items": items[:LIST_LIMIT], "truncated": len(items) > LIST_LIMIT,
            "insights": insights(rows, hops, people),
            "filters": {"branches": sorted({bkey(p) for p in people.values() if bkey(p)}),
                        "people": sorted(({"id": p.id, "name": p.name, "branch": bkey(p) or ""}
                                          for p in people.values()),
                                         key=lambda x: (x["branch"], x["name"]))}}


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs)) if xs else None


def insights(rows, hops: dict[int, list[ForwardHop]], people: dict) -> dict:
    """The period's numbers. Per person and level: received, solved, not solved in 24 h (passed on), closed without
    a good call (supervisors), closed by another manager, average time from getting it to solving it."""
    tot = {"forwarded": len(rows), "open": 0, "unsolved": 0, "solved_1": 0, "solved_2": 0, "solved_3": 0}
    took: list[float] = []
    per: dict[tuple[int, int], dict] = {}
    branch: dict[str, dict] = defaultdict(lambda: {"received": 0, "solved_in_branch": 0, "went_to_managers": 0})
    for f, c in rows:
        hs = hops.get(c.id, [])
        st = _state(hs)
        if st == "solved":
            h = [x for x in hs if x.result == "solved"][-1]
            tot[f"solved_{h.level}"] += 1
            if done_at(h) and f.forwarded_at:
                took.append(max(0.0, (done_at(h) - f.forwarded_at).total_seconds()))
        else:
            tot[st] += 1
        if hs and hs[0].person_id in people:
            b = branch[bkey(people[hs[0].person_id]) or "-"]
            b["received"] += 1
            b["solved_in_branch"] += any(x.result == "solved" and x.level <= 2 for x in hs)
            b["went_to_managers"] += any(x.level == 3 for x in hs)
        for h in hs:
            if h.sent_at is None or h.result in ("pending", "failed"):     # never reached him
                continue
            p = people.get(h.person_id)
            d = per.setdefault((h.level, h.person_id), {
                "person_id": h.person_id, "name": p.name if p else None, "branch": bkey(p),
                "received": 0, "solved": 0, "not_solved_24h": 0, "closed_no_good_call": 0, "closed_by_other": 0,
                "open": 0, "_t": []})
            d["received"] += 1
            if h.result == "solved":
                d["solved"] += 1
                if done_at(h) and h.sent_at:
                    d["_t"].append(max(0.0, (done_at(h) - h.sent_at).total_seconds()))
            elif h.result == "passed_on":
                d["not_solved_24h"] += 1
            if h.level == 2 and h.result in ("passed_on", "open") and closed(h):
                d["closed_no_good_call"] += 1          # he closed it, the call check said no (or not yet)
            elif h.result == "closed_by_other":
                d["closed_by_other"] += 1
            elif h.result == "open":
                d["open"] += 1
    tables = {1: [], 2: [], 3: []}
    for (lvl, _), d in per.items():
        d["avg_seconds_to_solve"] = _avg(d.pop("_t"))
        tables[lvl].append(d)
    for t in tables.values():
        t.sort(key=lambda d: (-d["received"], d["name"] or ""))
    tot["avg_seconds_to_solve"] = _avg(took)
    return {"totals": tot, "employees": tables[1], "supervisors": tables[2], "managers": tables[3],
            "branches": sorted(({"branch": k, **v} for k, v in branch.items()), key=lambda x: -x["received"])}


def story(s: Session, call_id: int) -> dict | None:
    """One problem from start to end: what the agent sent, then each person in order: what he wrote on the ticket
    (timeline items read while he held it), his calls to the customer (branch supervisors only), solved or not."""
    f = s.scalar(select(CallFollowup).where(CallFollowup.call_id == call_id,
                                            CallFollowup.responsible_kind == "forward"))
    c = s.get(Call, call_id)
    if f is None or c is None or c.content_deleted_at is not None or f.forward_status != "sent":
        return None
    hs = list(s.scalars(select(ForwardHop).where(ForwardHop.call_id == call_id).order_by(ForwardHop.id)))
    pids = {h.person_id for h in hs} | ({f.forward_person_id} if f.forward_person_id else set())
    people = {p.id: p for p in s.scalars(select(ForwardPerson).where(ForwardPerson.id.in_(pids)))} if pids else {}
    events = list(s.scalars(select(ForwardEvent).where(ForwardEvent.call_id == call_id)
                            .order_by(ForwardEvent.at.asc().nulls_last(), ForwardEvent.id)
                            .limit(EVENT_MAX * (len(hs) + 1))))
    by_hop: dict[int, list[ForwardEvent]] = defaultdict(list)
    other: list[ForwardEvent] = []
    for e in events:
        hid = e.hop_id
        if hid is None:                      # read before the chain: the hop whose time holds it
            got = [h for h in hs if h.sent_at and e.at and e.at >= h.sent_at and (h.ended_at is None
                                                                                    or e.at <= h.ended_at)]
            hid = got[-1].id if got else None
        if hid is not None:
            by_hop[hid].append(e)
        else:
            other.append(e)                  # never credited to someone who may not have written it
    names = dict(s.execute(select(Agent.ext, Agent.name)).all())
    d9 = digits9(c.customer_number)
    pairs = [(people[h.person_id].ext, d9, h.sent_at) for h in hs
             if h.level == 2 and h.sent_at and h.person_id in people and people[h.person_id].ext]
    calls, scores = _sup_calls(s, pairs)
    out_hops = []
    for h in hs:
        p = people.get(h.person_id)
        mine_items = by_hop.get(h.id, [])
        item = {"level": h.level, "name": p.name if p else None, "branch": bkey(p),
                "ticket_number": h.ticket_number, "result": h.result, "sent_at": _iso(h.sent_at),
                "closed_at": _iso(h.closed_at if closed(h) else None), "ended_at": _iso(h.ended_at),
                "solution": h.solution, "error": h.error,
                "items": [{"kind": e.kind, "type": e.type, "note": e.note, "at": _iso(e.at), "actor": e.actor}
                          for e in mine_items[:EVENT_MAX]], "items_cut": len(mine_items) > EVENT_MAX,
                "calls": None, "proof": None}
        if h.level == 2 and p is not None and p.ext and h.sent_at:
            # the verdict the chain made: calls up to when it acted (open: up to now, within the watch time)
            end = h.ended_at or min(h.sent_at + WATCH_MAX, datetime.now(timezone.utc))
            mine = [x for x in calls if x.agent_ext == p.ext and h.sent_at < x.start_time <= end]
            item["proof"] = proof(mine, {x.id: scores.get(x.id) for x in mine}, True)
            item["calls"] = item["proof"]["calls"]
        out_hops.append(item)
    agent = names.get(c.agent_ext or "", "")
    return {"call_id": c.id, "customer_number": c.customer_number, "priority": f.priority,
            "state": _state(hs),
            "agent": {"ext": c.agent_ext, "name": agent, "what_happened": f.what_happened, "note": f.forward_note,
                      "forwarded_at": _iso(f.forwarded_at), "ticket_number": f.forward_ticket_number,
                      "call_time": _iso(c.start_time)},
            "hops": out_hops,
            "other_items": [{"kind": e.kind, "type": e.type, "note": e.note, "at": _iso(e.at), "actor": e.actor}
                            for e in other[:EVENT_MAX]]}
