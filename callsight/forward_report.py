"""The supervisors' forwards page (spec 2026-11-16-priority-no-followup, "Part 4: forwards page"): every forwarded
problem kept after it closes (the archive), the full story of one problem (what the agent sent, what each person
wrote on the ticket, his calls, solved or not), and the numbers for a period: who solves, who lets the 24 h run out,
how long it takes. Staff only (the API checks the role). Read-only: nothing here changes a ticket.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import RIYADH
from .db.models import Agent, Call, CallFollowup, ForwardEvent, ForwardHop, ForwardPerson
from .forward import WATCH_MAX, _sup_calls, closed, covered, digits9, proof

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
    end = (s_hop.decided_at or done_at(s_hop)) if s_hop else None     # our supervisor's close ends the problem
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
            end = h.decided_at or done_at(h)
            if end and f.forwarded_at:
                took.append(max(0.0, (end - f.forwarded_at).total_seconds()))
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
    from .db.models import AppUser
    from .forward import decidable
    deciders = {u.id: u.username for u in s.scalars(select(AppUser).where(
        AppUser.id.in_({h.decided_by for h in hs if h.decided_by})))} if any(h.decided_by for h in hs) else {}
    chain_solved = any(h.result == "solved" for h in hs)
    d9 = digits9(c.customer_number)
    pairs = [(people[h.person_id].ext, d9, h.sent_at) for h in hs
             if h.level == 2 and h.sent_at and h.person_id in people and people[h.person_id].ext]
    calls, scores = _sup_calls(s, pairs)
    out_hops = []
    for h in hs:
        p = people.get(h.person_id)
        mine_items = by_hop.get(h.id, [])
        item = {"hop_id": h.id, "level": h.level, "name": p.name if p else None, "branch": bkey(p),
                "decided_by": deciders.get(h.decided_by) if h.decided_by else None,
                "decided_at": _iso(h.decided_at), "erp_close": h.erp_close,
                "can_decide": not chain_solved and decidable(h),
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


# ---------- D4 (req 20): the KPI page's table of the people problems were forwarded to ----------

MISTAKES_MAX = 200          # mistake lines kept per person / agent (the count stays exact)
LATE_REASON = {"passed_on": "لم تُحل خلال 24 ساعة فانتقلت لمن بعده", "open": "ما زالت عنده بعد 24 ساعة",
               "solved": "حلّها بعد مرور 24 ساعة", "closed_by_other": "لم يحلّها خلال 24 ساعة (حلّها غيره بعد ذلك)"}
PROOF_AR = {"no_call": "لم يتصل بالعميل", "not_answered": "لم يرد العميل", "short": "المكالمة 60 ثانية أو أقل",
            "waiting_score": "بانتظار تقييم الرضا", "low_score": "الرضا 7 أو أقل", "no_ext": "لا توجد تحويلة"}
ANSWERED = ("pending", "waiting", "sent", "done")     # a failed answer never reached the ticket: not an answer


def hop_end(h: ForwardHop, now: datetime, other_end: datetime | None = None) -> datetime | None:
    """Until when this person held it: his own close when he solved it, when it moved on, the solver's own close
    when another manager solved it (not when our run noticed it), or now while he still has it."""
    if h.result == "solved":
        return done_at(h)
    if h.result == "closed_by_other":
        return other_end or h.ended_at
    if h.result == "passed_on":
        return h.ended_at
    return now if h.result == "open" else None


def _overlap(spans: list[tuple[datetime, datetime]], lo: datetime, hi: datetime) -> float:
    """Seconds of [lo, hi] covered by the spans (overlapping spans counted once)."""
    cut = sorted((max(a, lo), min(b, hi)) for a, b in spans if b > lo and a < hi)
    total, cur_a, cur_b = 0.0, None, None
    for a, b in cut:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += (cur_b - cur_a).total_seconds()
    return total


def held_seconds(h: ForwardHop, now: datetime, waits: list[tuple[datetime, datetime]],
                 other_end: datetime | None = None) -> float | None:
    """His own time (req 20 "fair time split"): from getting it to the end of his turn, minus the time a question
    of the ticket waited for OUR agent's answer."""
    end = hop_end(h, now, other_end)
    if h.sent_at is None or end is None:
        return None
    return max(0.0, (end - h.sent_at).total_seconds() - _overlap(waits, h.sent_at, end))


def late_reason(h: ForwardHop, now: datetime, waits: list[tuple[datetime, datetime]] | None = None,
                other_end: datetime | None = None) -> str | None:
    """A late hop (spec D4: "each hop that ran out of 24 h counts as a late hop for that person"): his own time
    (without our agent's answer waits) went over 24 h without his solve. None = not late."""
    from .forward import HOP_TIME
    own = held_seconds(h, now, waits or [], other_end)
    if own is None or own < HOP_TIME.total_seconds() or (h.result == "open" and own == HOP_TIME.total_seconds()):
        return None
    return LATE_REASON.get(h.result)


def _questions(s: Session, call_ids: set[int]) -> tuple[dict, dict, dict]:
    """For these calls: the questions (followup items by others, oldest first), the agent's answers/seen marks
    that reached the ticket, and when the chain was solved (it ends every question)."""
    from .db.models import ForwardMessage
    from .forward import is_question
    qs: dict[int, list[ForwardEvent]] = defaultdict(list)
    ans: dict[int, list] = defaultdict(list)
    solved_at: dict[int, datetime] = {}
    if not call_ids:
        return qs, ans, solved_at
    created: dict[int, set[str]] = defaultdict(set)
    events = list(s.scalars(select(ForwardEvent).where(ForwardEvent.call_id.in_(call_ids),
                                                       ForwardEvent.kind.in_(("event", "followup")))))
    for e in events:
        if e.type == "created" and e.actor:
            created[e.call_id].add(e.actor)
    for e in sorted(events, key=lambda x: (x.seen_at, x.id)):
        if is_question(e, created[e.call_id]):
            qs[e.call_id].append(e)
    for m in s.scalars(select(ForwardMessage).where(ForwardMessage.call_id.in_(call_ids),
                                                    ForwardMessage.kind.in_(("answer", "seen")),
                                                    ForwardMessage.status.in_(ANSWERED))
                       .order_by(ForwardMessage.created_at, ForwardMessage.id)):
        ans[m.call_id].append(m)
    for cid, at_ in s.execute(select(ForwardHop.call_id, func.coalesce(ForwardHop.decided_at, ForwardHop.ended_at))
                              .where(ForwardHop.call_id.in_(call_ids), ForwardHop.result == "solved")):
        if at_ is not None and (cid not in solved_at or at_ < solved_at[cid]):
            solved_at[cid] = at_
    return qs, ans, solved_at


def _waits(qs, ans, solved_at, now) -> dict[int, list[tuple[datetime, datetime]]]:
    """Per call: each question's wait for the agent (read -> his answer, or the solve / now)."""
    out: dict[int, list] = {}
    for cid, items in qs.items():
        spans = []
        for q in items:
            a = next((m.created_at for m in ans.get(cid, []) if covered(q, m)), None)
            end = min(x for x in (a, solved_at.get(cid), now) if x is not None)
            if end > q.seen_at:
                spans.append((q.seen_at, end))
        out[cid] = spans
    return out


def people_kpi(s: Session, start: date, end: date, now: datetime | None = None, agent: str | None = None) -> dict:
    """Req 20 with the forward chain, show-only. By the day each person GOT the problem (Saudi days):
    - people: per person and level: received, solved, solved in 24 h (+ % of the decided ones), average time to
      solve, late (mistakes) with one line each. His time = his own (a question waiting for our agent's answer is
      not on his clock). A branch supervisor's close that waits only for OUR satisfaction score is not his mistake.
    - agents: questions read in the period, answered on time / late / still waiting (the form rule: by 23:00 that
      day, or 2 h after a question read after 21:00); a solve before the deadline ends the question. "Seen, no
      answer needed" counts as an answer, a failed one does not.
    `agent` = an extension: the agents table shows only him (the people table stays whole)."""
    from .followups import after_deadline
    from .forward import HOP_TIME
    now = now or datetime.now(timezone.utc)
    lo, hi = _bounds(start, end)
    hs = list(s.scalars(select(ForwardHop).join(Call, Call.id == ForwardHop.call_id).where(
        ForwardHop.sent_at >= lo, ForwardHop.sent_at < hi, ForwardHop.result.not_in(("pending", "failed")),
        Call.content_deleted_at.is_(None)).order_by(ForwardHop.sent_at, ForwardHop.id)))
    pids = {h.person_id for h in hs}
    people = {p.id: p for p in s.scalars(select(ForwardPerson).where(ForwardPerson.id.in_(pids)))} if pids else {}
    calls = {c.id: c for c in s.scalars(select(Call).where(Call.id.in_({h.call_id for h in hs})))} if hs else {}
    names = dict(s.execute(select(Agent.ext, Agent.name)).all())
    qs, ans, solved_at = _questions(s, set(calls))
    waits = _waits(qs, ans, solved_at, now)
    day = HOP_TIME.total_seconds()

    solver_end: dict[int, datetime] = {}               # the solving hop's own close, per call
    for h in s.scalars(select(ForwardHop).where(ForwardHop.call_id.in_(set(calls)), ForwardHop.result == "solved")
                       ) if calls else []:
        if h.decided_at is not None:               # our supervisor's close: the others' turn ended then
            solver_end[h.call_id] = h.decided_at
        elif done_at(h):
            solver_end[h.call_id] = done_at(h)
    late = [(h, late_reason(h, now, waits.get(h.call_id), solver_end.get(h.call_id))) for h in hs]
    # the supervisors' calls only for the late closes that need a reason (review D4: smaller query)
    need = [h for h, why in late if why and h.level == 2 and h.result != "solved" and closed(h)
            and h.person_id in people and people[h.person_id].ext and h.call_id in calls]
    sup_calls, scores = _sup_calls(s, [(people[h.person_id].ext, digits9(calls[h.call_id].customer_number),
                                        h.sent_at) for h in need])
    per: dict[tuple[int, int], dict] = {}
    for h, why in late:
        p, c = people.get(h.person_id), calls.get(h.call_id)
        d = per.setdefault((h.level, h.person_id), {
            "person_id": h.person_id, "level": h.level, "name": p.name if p else None, "branch": bkey(p),
            "received": 0, "solved": 0, "solved_in_24h": 0, "decided": 0, "in_24h_rate": None,
            "avg_seconds_to_solve": None, "open": 0, "late": 0, "excused": 0, "mistakes": [], "_t": []})
        d["received"] += 1
        d["open"] += h.result == "open"
        end_ = hop_end(h, now, solver_end.get(h.call_id))
        own = held_seconds(h, now, waits.get(h.call_id, []), solver_end.get(h.call_id))
        clock = (end_ - h.sent_at).total_seconds() if end_ and h.sent_at else None
        if h.result == "solved":
            d["solved"] += 1
            if own is not None:
                d["_t"].append(own)
                d["solved_in_24h"] += own <= day
        if why is None:
            # over 24 h on the clock, but not on his own time: the wait was on our agent's side
            d["excused"] += bool(clock is not None and clock > day and h.result != "open")
            d["decided"] += h.result != "open"          # still running within his 24 h: not decided yet
            continue
        if h in need and p is not None and c is not None:
            stop = min(h.ended_at or now, h.sent_at + WATCH_MAX)       # the same window as the story
            mine = [x for x in sup_calls if x.agent_ext == p.ext and digits9(x.customer_number)
                    == digits9(c.customer_number) and h.sent_at < x.start_time <= stop]
            pr = proof(mine, {x.id: scores.get(x.id) for x in mine}, True)
            if pr["reason"] == "waiting_score":         # the delay is OUR scoring, not his
                d["excused"] += 1
                d["decided"] += h.result != "open"      # still open: may yet turn into his on-time solve
                continue
            if not pr["ok"]:
                why += f" · أغلقها دون مكالمة جيدة: {PROOF_AR.get(pr['reason'] or '', '–')}"
        d["late"] += 1
        d["decided"] += 1
        if len(d["mistakes"]) < MISTAKES_MAX:
            d["mistakes"].append({
                "call_id": h.call_id, "sent_at": _iso(h.sent_at), "due": _iso(h.sent_at + HOP_TIME),
                "ended_at": _iso(end_ if h.result != "open" else None), "reason": why,
                "ticket_number": h.ticket_number, "customer_number": c.customer_number if c else None,
                "agent_wait_seconds": round(_overlap(waits.get(h.call_id, []), h.sent_at, end_)) if end_ else 0,
                "agent": (names.get(c.agent_ext or "") or c.agent_ext) if c else None})
    rows = []
    for d in per.values():
        t = d.pop("_t")
        d["avg_seconds_to_solve"] = _avg(t)
        d["in_24h_rate"] = round(d["solved_in_24h"] / d["decided"], 4) if d["decided"] else None
        rows.append(d)
    rows.sort(key=lambda d: (d["level"], -d["late"], -d["received"], d["name"] or ""))

    # the agents' side: questions READ in the period on calls of their forwarded problems
    evs = list(s.scalars(select(ForwardEvent.call_id).join(Call, Call.id == ForwardEvent.call_id).where(
        ForwardEvent.seen_at >= lo, ForwardEvent.seen_at < hi, ForwardEvent.kind == "followup",
        Call.content_deleted_at.is_(None)).distinct()))
    more = set(evs) - set(calls)
    if more:
        q2, a2, s2 = _questions(s, more)
        qs.update(q2)
        ans.update(a2)
        solved_at.update(s2)
    owner = dict(s.execute(select(Call.id, Call.agent_ext).where(Call.id.in_(set(evs)))).all()) if evs else {}
    ag: dict[str, dict] = {}
    for cid in sorted(set(evs)):
        ext = owner.get(cid)
        if not ext or (agent and ext != agent):
            continue
        for q in qs.get(cid, []):
            if not (lo <= q.seen_at < hi):
                continue
            due = after_deadline(q.seen_at)
            a = next((m for m in ans.get(cid, []) if covered(q, m)), None)
            solved = solved_at.get(cid)
            if a is None and solved is not None and solved <= due:
                continue                                # solved before his deadline: nothing was owed
            d = ag.setdefault(ext, {"ext": ext, "name": names.get(ext, ""), "questions": 0, "on_time": 0,
                                    "late": 0, "waiting": 0, "mistakes": []})
            d["questions"] += 1
            if a is not None and a.created_at <= due:
                d["on_time"] += 1
            elif a is None and now <= due:
                d["waiting"] += 1
            else:
                d["late"] += 1
                if len(d["mistakes"]) < MISTAKES_MAX:
                    d["mistakes"].append({"call_id": cid, "asked_at": _iso(q.seen_at), "due": _iso(due),
                                          "answered_at": _iso(a.created_at) if a else None, "from": q.actor,
                                          "reason": "ردّ بعد الموعد" if a else "لم يرد حتى انتهى الموعد"})
    return {"from": start.isoformat(), "to": end.isoformat(), "people": rows,
            "agents": sorted(ag.values(), key=lambda d: (-d["late"], d["ext"]))}
