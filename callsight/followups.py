"""Call follow-ups (spec 2026-11-15-agent-supervisor): which calls need a form, the form deadline, the callback check
and the follow-up KPIs. One place for these rules: the API lists, the form, the KPI page and the PDF all use it.

Decisions (spec): a form is needed for answered inbound team calls with talk > 20 s from `followup_start` on (3);
form on time = by 23:00 Riyadh on the call's day, or 2 h after the call end when that is later (11); a not-solved
form's callback is done by the first saved outbound call to the same number (last 9 digits) after the call ended,
by any team member, credited to the form's owner (2), when it is a proper try (answered, ended by the customer
side / busy, or rang >= 30 s; spec 2026-11-15-ring-time); on time =
callback time + 60 min (4). "Came back" = an inbound call from the same number <= 72 h after a call marked solved
(show-only). Calls whose content an admin erased are left out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from .config import RIYADH
from .db.models import (AppSetting, AppUser, Call, CallFollowup, CallFollowupNote, ForwardEvent, ForwardHop,
                        ForwardMessage, ForwardPerson, norm_number)
from .ingest.service import day_bounds

MIN_TALK_SECONDS = 20          # talk must be MORE than this
GRACE = timedelta(minutes=60)
EARLY_HANGUP_SECONDS = 30      # spec 2026-11-15-ring-time: the agent hung up before this many ring seconds
DAY_END_HOUR = 23
LATE_CALL_EXTRA = timedelta(hours=2)
FORWARD_HOP = timedelta(hours=24)    # a forwarded problem: each person's time (= forward.HOP_TIME); sets expected_at
# spec 2026-11-16-priority-no-followup: fixed English codes (another system reads them), high -> low
PRIORITIES = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
CAME_BACK = timedelta(hours=72)
SETTING_START = "followup_start"
SETTING_KPI = "followup_kpi_since"
SETTINGS = (SETTING_START, SETTING_KPI)
RESPONSIBLE_KINDS = ("user", "customer", "other_department", "forward")   # forward: part 4, first form only
CALLBACK_STATES = ("waiting", "on_time", "late", "missed", "no_number")
# steps of one call (spec 2026-11-15-followup-stages), computed on read, never stored
STEPS = ("required", "overdue", "no_followup", "solved_first", "waiting_callback", "callback_missed",
         "note_needed", "solved_after", "no_number", "waiting_solution", "solution_overdue",
         "forwarded", "forward_overdue", "forward_failed", "forward_solved", "forward_unconfirmed")
# not solved WITHOUT a callback time (callback is optional): waiting until the expected solve time, then overdue
NO_CB_STEPS = ("waiting_solution", "solution_overdue")
# forwarded to a branch supervisor (part 4): with them until the expected solve time, then late with them
FORWARD_STEPS = ("forwarded", "forward_overdue")
# forward_failed + notes: any answer other than another supervisor is written as a note (review D1)
NOTE_STEPS = ("note_needed", *NO_CB_STEPS, *FORWARD_STEPS, "forward_failed", "forward_unconfirmed")   # steps where a note may be added
# the send failed (refused, or not sure it went through): the agent must pick again in the form (review D1)
# D2: the supervisor closed the ticket -> forward_solved when the proof call is good, else forward_unconfirmed (open)
OPEN_STEPS = ("waiting_callback", "callback_missed", "note_needed", "no_number", *NO_CB_STEPS,
              *FORWARD_STEPS, "forward_failed", "forward_unconfirmed")   # list view "open"
# KPI: a ticket closed there is not "open", confirmed or not (llm_score is for the solved check only, decision 10)
KPI_OPEN_STEPS = ("waiting_callback", "callback_missed", "note_needed", *NO_CB_STEPS, *FORWARD_STEPS,
                  "forward_failed")
SOLVED_STEPS = ("solved_first", "solved_after", "forward_solved")
FORWARD_NOTE_MAX = 500
ACTION_STEPS = ("required", "overdue", "note_needed", "callback_missed", "solution_overdue", "forward_failed")
# + waiting_callback / waiting_solution due by today


def proper_try_sql(o):
    """SQL: an outbound call that counts as a real callback try (spec 2026-11-15-ring-time): every call except an
    early hang-up (see early_hangup_sql). Unknown who hung up is not blamed on the agent."""
    return or_(o.status == "answered", func.coalesce(o.hangup_by, "") != "agent",
               o.wait_seconds >= EARLY_HANGUP_SECONDS)


def early_hangup_sql(o):
    """SQL: not answered, the agent hung up and it rang < 30 s = the try does not count (spec requirement 2)."""
    return and_(o.status != "answered", o.hangup_by == "agent", o.wait_seconds < EARLY_HANGUP_SECONDS)


def is_proper_try(c: Call) -> bool:
    return c.status == "answered" or c.hangup_by != "agent" or (c.wait_seconds or 0) >= EARLY_HANGUP_SECONDS


def ring_info(c: Call) -> dict | None:
    """Outbound call: ring seconds + who ended it (agent / customer / busy) + early (did not count)."""
    if c.direction != "outbound":
        return None
    return {"ring_seconds": c.wait_seconds or 0, "hangup_by": c.hangup_by, "answered": c.status == "answered",
            "early_hangup": not is_proper_try(c)}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- settings ----------

def get_setting(s: Session, key: str) -> date | None:
    row = s.get(AppSetting, key)
    if row is None or not row.value:
        return None
    try:
        return date.fromisoformat(row.value)
    except ValueError:
        return None


def settings(s: Session) -> dict[str, date | None]:
    return {k: get_setting(s, k) for k in SETTINGS}


def set_setting(s: Session, key: str, value: date | None, user_id: int) -> None:
    if key not in SETTINGS:
        raise ValueError(f"unknown setting {key!r}")
    row = s.get(AppSetting, key)
    if value is None:
        if row is not None:
            s.delete(row)
        return
    if row is None:
        s.add(AppSetting(key=key, value=value.isoformat(), updated_by=user_id, updated_at=_utcnow()))
    else:
        row.value, row.updated_by, row.updated_at = value.isoformat(), user_id, _utcnow()


# ---------- rules ----------

def form_deadline(call: Call) -> datetime:
    return after_deadline(call.end_time)


def after_deadline(end_time: datetime) -> datetime:
    """Form after a call / note after a callback: by 23:00 Riyadh that day, or 2 h after when that is later."""
    end = end_time.astimezone(RIYADH)
    day_end = end.replace(hour=DAY_END_HOUR, minute=0, second=0, microsecond=0)
    return max(day_end, end + LATE_CALL_EXTRA)


def needed_conditions(exts, start: date):
    lo = day_bounds(start)[0]
    return (Call.direction == "inbound", Call.status == "answered", Call.agent_ext.in_(list(exts)),
            Call.talk_seconds > MIN_TALK_SECONDS, Call.start_time >= lo, Call.content_deleted_at.is_(None))


def is_needed(call: Call, exts, start: date | None) -> bool:
    return (start is not None and call.direction == "inbound" and call.status == "answered"
            and call.agent_ext in set(exts) and (call.talk_seconds or 0) > MIN_TALK_SECONDS
            and call.start_time >= day_bounds(start)[0] and call.content_deleted_at is None)


def _has_number(number: str | None) -> bool:
    return len("".join(ch for ch in (number or "") if ch.isdigit())) > 0


def callback_state(callback_at: datetime | None, has_number: bool, cb_time: datetime | None,
                   now: datetime) -> str | None:
    if callback_at is None:
        return None
    if not has_number:
        return "no_number"
    due = callback_at + GRACE
    if cb_time is not None:
        return "on_time" if cb_time <= due else "late"
    return "missed" if now > due else "waiting"


def form_state(call: Call, form: CallFollowup | None, now: datetime) -> str:
    """missing (deadline not passed) / overdue (passed, no form) / on_time / late (filled after the deadline)."""
    deadline = form_deadline(call)
    if form is None:
        return "overdue" if now > deadline else "missing"
    return "on_time" if form.first_filled_at <= deadline else "late"


# ---------- queries ----------

def _callback_lateral(callers):
    """First outbound call by a team extension to the same number after the original call ended that really rang."""
    o = aliased(Call)
    return (select(o.id.label("cb_id"), o.start_time.label("cb_time"), o.agent_ext.label("cb_ext"))
            .where(o.direction == "outbound", o.id != Call.id, o.start_time > Call.end_time,
                   o.agent_ext.in_(list(callers)), norm_number(Call.customer_number) != "",
                   norm_number(o.customer_number) == norm_number(Call.customer_number),
                   proper_try_sql(o))
            .order_by(o.start_time, o.id).limit(1).correlate(Call).lateral("cb"))


def _callback_answered(callers):
    """Did the customer answer ANY team callback after the original call (PBX record, not a button)? A first try
    without answer followed by an answered one counts as answered."""
    o = aliased(Call)
    return exists().where(o.direction == "outbound", o.id != Call.id, o.start_time > Call.end_time,
                          o.agent_ext.in_(list(callers)), norm_number(Call.customer_number) != "",
                          norm_number(o.customer_number) == norm_number(Call.customer_number),
                          o.status == "answered")


def _came_back():
    i = aliased(Call)
    return exists().where(i.direction == "inbound", i.id != Call.id, i.start_time > Call.end_time,
                          norm_number(Call.customer_number) != "",
                          i.start_time <= Call.end_time + CAME_BACK,
                          norm_number(i.customer_number) == norm_number(Call.customer_number))


@dataclass
class Callback:
    """The callback call found for one round (the first outbound team call that really rang)."""
    id: int
    time: datetime
    end: datetime
    ext: str | None
    answered: bool | None = None      # did the customer answer ANY team callback of this round (PBX record)
    call: Call | None = None          # the callback call itself (ring seconds, who hung up)

    def as_dict(self) -> dict:
        d = {"call_id": self.id, "time": self.time.isoformat(), "by_ext": self.ext, "answered": self.answered}
        if self.call is not None:
            d.update(ring_seconds=self.call.wait_seconds or 0, hangup_by=self.call.hangup_by,
                     call_answered=self.call.status == "answered")
        return d


@dataclass
class Row:
    call: Call
    form: CallFollowup | None
    cb_id: int | None                 # round-1 callback (SQL lateral)
    cb_time: datetime | None
    cb_ext: str | None
    came_back: bool
    cb_answered: bool = False         # some team callback to this customer was answered (PBX record)
    notes: list[CallFollowupNote] = field(default_factory=list)       # rounds 2, 3, ... in order
    cb_calls: dict[int, Call] = field(default_factory=dict)          # round-1 cb + notes' cb_call_id -> call
    current_cb: Callback | None = None   # rounds >= 2: callback of the latest open note (looked up in rows())
    early: list[Call] = field(default_factory=list)   # team calls to this customer after the call that did NOT count
    person: ForwardPerson | None = None  # forwarded (part 4): the person the agent chose
    hops: list[ForwardHop] = field(default_factory=list)   # the forward chain: who held it, in order
    hop_people: dict[int, ForwardPerson] = field(default_factory=dict)
    fwd_calls: list[Call] = field(default_factory=list)   # D2: the branch supervisor's calls to the customer since
    scores: dict[int, int | None] = field(default_factory=dict)   # D2: llm_score of those calls
    events: list[ForwardEvent] = field(default_factory=list)      # D3: the ticket's timeline (read back)
    messages: list[ForwardMessage] = field(default_factory=list)  # D3: what we wrote on it (answers, notes...)

    def proof_hop(self) -> ForwardHop | None:
        """The last branch-supervisor hop (level 2): only his calls are tracked (user 2026-11-16)."""
        got = [h for h in self.hops if h.level == 2 and h.result != "pending"]
        return got[-1] if got else None

    def current_hops(self) -> list[ForwardHop]:
        """Who holds it now: the open hops of the highest level (several managers at level 3)."""
        open_ = [h for h in self.hops if h.result in ("open", "pending")]
        top = max((h.level for h in open_), default=0)
        return [h for h in open_ if h.level == top]

    def chain_open(self) -> bool:
        return bool(self.hops) and not any(h.result == "solved" for h in self.hops) and bool(self.current_hops())

    def questions(self) -> list[ForwardEvent]:
        """D3 (req 18): questions from the person holding it that wait for the agent's answer."""
        if not self.chain_open():
            return []
        from .forward import open_questions
        return open_questions(self.events, self.messages)

    def forward_proof(self) -> dict | None:
        """D2: the proof of solving: the branch supervisor's last call (when we know his ext)."""
        f = self.form
        if f is None or f.responsible_kind != "forward" or f.forward_status != "sent":
            return None
        h = self.proof_hop()
        if h is None:
            return None
        from .forward import proof
        p = self.hop_people.get(h.person_id)
        return proof(self.fwd_calls, self.scores, bool(p and p.ext))

    # ----- rounds -----

    def round1_cb(self) -> Callback | None:
        if self.cb_time is None:
            return None
        c = self.cb_calls.get(self.cb_id or -1)
        return Callback(self.cb_id, self.cb_time, c.end_time if c else self.cb_time, self.cb_ext,
                        bool(self.cb_answered), c)

    def open_entry(self):
        """The latest entry (form or note) when it is not solved and set a callback time, else None."""
        f = self.form
        if f is None or f.no_followup:
            return None
        last = self.notes[-1] if self.notes else f
        return last if last.solved is False and last.callback_at is not None else None

    def cb_of_round(self, rnd: int) -> Callback | None:
        """Callback call of round `rnd`: the next note's cb_call when that note is written, else the live lookup."""
        if rnd <= len(self.notes):                       # notes[rnd-1] = round rnd+1, reports on round rnd's callback
            nxt = self.notes[rnd - 1]
            c = self.cb_calls.get(nxt.cb_call_id or -1)
            return Callback(c.id, c.start_time, c.end_time, c.agent_ext, c.status == "answered", c) if c else None
        return self.round1_cb() if rnd == 1 else self.current_cb

    def early_tries(self, rnd: int, now: datetime) -> dict | None:
        """Early hang-ups (did not count) in round `rnd`: from the round's start until its counted callback (or the
        next note / now). None when there are none."""
        lo = self.call.end_time if rnd == 1 else self.notes[rnd - 2].created_at
        cb = self.cb_of_round(rnd)
        hi = cb.time if cb else (self.notes[rnd - 1].created_at if rnd <= len(self.notes) else now)
        got = [c for c in self.early if lo < c.start_time < hi]
        if not got:
            return None
        last = got[-1]
        return {"count": len(got), "last_ring_seconds": last.wait_seconds or 0,
                "last_time": last.start_time.isoformat(), "last_call_id": last.id, "last_by_ext": last.agent_ext}

    def step(self, now: datetime) -> str:
        f = self.form
        if f is None:
            return "overdue" if now > form_deadline(self.call) else "required"
        if f.no_followup:
            return "no_followup"
        last = self.notes[-1] if self.notes else f
        if last.solved is True:
            return "solved_after" if self.notes else "solved_first"
        if f.responsible_kind == "forward" and f.forward_status == "failed":
            return "forward_failed"
        if f.responsible_kind == "forward" and f.forward_status == "sent" and self.hops:
            # the chain decides (forward.advance): solved / the branch supervisor closed without a good call
            if any(h.result == "solved" for h in self.hops):
                return "forward_solved"
            from .forward import closed
            cur = self.current_hops()
            sup = cur[0] if cur and cur[0].level == 2 else None
            if sup is not None and closed(sup):     # until the chain step confirms it (proof decides there)
                return "forward_unconfirmed"
        # the agent's own callback steps come first (build decision); then the supervisor's time
        if f.responsible_kind == "forward" and last.callback_at is None:     # with the person (part 4)
            due = self.forward_due()
            return "forward_overdue" if due is not None and now > due else "forwarded"
        if last.callback_at is None:                     # no callback asked: wait for the expected solve time
            due = last.expected_at
            return "solution_overdue" if due is not None and now > due + GRACE else "waiting_solution"
        if not _has_number(self.call.customer_number):
            return "no_number"
        if self.cb_of_round(len(self.notes) + 1) is not None:
            return "note_needed"
        return "callback_missed" if now > last.callback_at + GRACE else "waiting_callback"

    def forward_due(self) -> datetime | None:
        """24 h after the current holder got it (the chain); before the chain existed: the expected solve time."""
        cur = [h for h in self.current_hops() if h.sent_at is not None]
        if cur:
            from .forward import HOP_TIME
            return min(h.sent_at for h in cur) + HOP_TIME
        f = self.form
        return f.expected_at + GRACE if f is not None and f.expected_at is not None and not self.hops else None

    def as_dict(self, now: datetime, names: dict[str, str] | None = None,
                users: dict[int, str] | None = None) -> dict:
        c, f = self.call, self.form
        rnd = len(self.notes) + 1
        open_ = self.open_entry()
        cur = self.cb_of_round(rnd)
        cb_state = callback_state(open_.callback_at if open_ is not None else None,
                                  _has_number(c.customer_number), cur.time if cur else None, now)
        step = self.step(now)
        return {
            "call_id": c.id, "start_time": c.start_time.isoformat(), "end_time": c.end_time.isoformat(),
            "customer_number": c.customer_number, "agent_ext": c.agent_ext,
            "agent_name": (names or {}).get(c.agent_ext or "", ""), "talk_seconds": c.talk_seconds,
            "deadline": form_deadline(c).isoformat(), "form_state": form_state(c, f, now),
            "form": form_dict(f) if f else None,
            "step": step, "round": rnd,
            "note_deadline": after_deadline(cur.end).isoformat() if step == "note_needed" and cur else None,
            "solve_by": _solve_by(self, step),
            "forward": (forward_dict(f, self.person, self.forward_proof(), self, now, names)
                        if f is not None else None),
            "history": self.history(now, users or {}),
            "callback": None if cb_state is None else {
                "at": open_.callback_at.isoformat(), "due": (open_.callback_at + GRACE).isoformat(),
                "status": cb_state, "round": rnd,
                "by_ext": cur.ext if cur else None, "call_id": cur.id if cur else None,
                # from the PBX record of the callback call, never from a button: did the customer answer?
                "answered": None if cur is None else bool(cur.answered),
                "time": cur.time.isoformat() if cur else None,
                "ring_seconds": (cur.call.wait_seconds or 0) if cur and cur.call else None,
                "hangup_by": cur.call.hangup_by if cur and cur.call else None,
                "early_tries": self.early_tries(rnd, now)},
            "came_back": bool(self.came_back and f and f.solved is True and not self.notes),
        }

    def history(self, now: datetime, users: dict[int, str]) -> list[dict]:
        """Form (round 1) + notes, each with its callback result, oldest first."""
        f = self.form
        if f is None:
            return []
        out = []
        entries = [(1, f, f.first_filled_at, f.filled_by, f.no_followup)]
        entries += [(n.round, n, n.created_at, n.created_by, False) for n in self.notes]
        for rnd, e, at_, by, nofu in entries:
            wants_cb = not nofu and e.solved is False and e.callback_at is not None
            cb = self.cb_of_round(rnd) if wants_cb else None
            out.append({
                "round": rnd, "what_happened": e.what_happened, "solved": e.solved, "no_followup": bool(nofu),
                **priority_fields(e.priority if rnd == 1 else None),       # set once, in the first form
                "responsible_kind": e.responsible_kind, "responsible_user_id": e.responsible_user_id,
                "responsible": (self.person.name if e.responsible_kind == "forward" and self.person
                                else users.get(e.responsible_user_id or -1)),
                "expected_at": e.expected_at.isoformat() if e.expected_at else None,
                "callback_at": e.callback_at.isoformat() if e.callback_at else None,
                "at": at_.isoformat(), "by": users.get(by),
                "callback_status": (callback_state(e.callback_at, _has_number(self.call.customer_number),
                                                   cb.time if cb else None, now) if wants_cb else None),
                "callback_call": None if cb is None else cb.as_dict(),
                "early_tries": self.early_tries(rnd, now) if wants_cb else None,
            })
        return out

    # ----- KPI pieces -----

    def callback_rounds(self, now: datetime) -> list[str]:
        """Callback status of every round that set a callback time (on_time / late / missed / waiting / no_number)."""
        f = self.form
        if f is None or f.no_followup:
            return []
        out = []
        for rnd, e in [(1, f)] + [(n.round, n) for n in self.notes]:
            if e.solved is False and e.callback_at is not None:
                cb = self.cb_of_round(rnd)
                out.append(callback_state(e.callback_at, _has_number(self.call.customer_number),
                                          cb.time if cb else None, now))
        return out

    def note_states(self, now: datetime) -> list[str]:
        """Each round whose callback call was found: the note after it is on_time / late / overdue / pending."""
        f = self.form
        if f is None or f.no_followup:
            return []
        out = []
        for rnd in range(1, len(self.notes) + 2):
            entry = f if rnd == 1 else self.notes[rnd - 2]
            if entry.solved is not False or entry.callback_at is None:
                continue
            cb = self.cb_of_round(rnd)
            if cb is None:
                continue
            deadline = after_deadline(cb.end)
            if rnd <= len(self.notes):
                out.append("on_time" if self.notes[rnd - 1].created_at <= deadline else "late")
            else:
                out.append("overdue" if now > deadline else "pending")
        return out


def _solve_by(r: Row, step: str) -> str | None:
    if step in FORWARD_STEPS and r.hops:
        due = r.forward_due()
        return due.isoformat() if due else None
    e = r.form if step in FORWARD_STEPS else (r.notes[-1] if r.notes else r.form)
    if step not in (*NO_CB_STEPS, *FORWARD_STEPS) or e is None or e.expected_at is None:
        return None
    return e.expected_at.isoformat()


def forwarded(f: CallFollowup | None) -> bool:
    """With the supervisor (or on its way): only they close it. A failed send is not forwarded."""
    return f is not None and f.responsible_kind == "forward" and f.forward_status in ("pending", "sent")


def forward_failed(f: CallFollowup | None) -> bool:
    return f is not None and f.responsible_kind == "forward" and f.forward_status == "failed"


def _hop_dict(h: ForwardHop, p: ForwardPerson | None) -> dict:
    return {"level": h.level, "name": p.name if p else None, "branch": p.branch if p else None,
            "result": h.result, "ticket_number": h.ticket_number,
            "sent_at": h.sent_at.isoformat() if h.sent_at else None,
            "closed_at": h.closed_at.isoformat() if h.closed_at else None,
            "ended_at": h.ended_at.isoformat() if h.ended_at else None, "error": h.error}


def forward_dict(f: CallFollowup, person: ForwardPerson | None, proof: dict | None = None,
                 row: "Row | None" = None, now: datetime | None = None,
                 names: dict[str, str] | None = None) -> dict | None:
    """Part 4: who the problem went to, whether the ticket was made, and (D2) what came back: closed time, solution
    text, the supervisor's calls and the proof verdict. Never the ticket's guid or the key."""
    if f.responsible_kind != "forward":
        return None
    return {"person_id": f.forward_person_id, "name": person.name if person else None,
            "branch": person.branch if person else None, "ext": person.ext if person else None,
            "note": f.forward_note, "status": f.forward_status, "ticket_number": f.forward_ticket_number,
            "forwarded_at": f.forwarded_at.isoformat() if f.forwarded_at else None, "error": f.forward_error,
            "closed_at": f.forward_closed_at.isoformat() if f.forward_closed_at else None,
            "solution": f.forward_solution,
            "checked_at": f.forward_checked_at.isoformat() if f.forward_checked_at else None,
            "proof": proof, **_chain_dict(row), **_talk_dict(row, now or _utcnow(), names)}


LEVEL_AR = {1: "موظف الفرع", 2: "مشرف الفرع", 3: "الإدارة"}
PROOF_REASON_AR = {"no_call": "لم يتصل بالعميل", "not_answered": "لم يرد العميل", "short": "المكالمة 60 ثانية أو أقل",
                   "waiting_score": "بانتظار تقييم الرضا", "low_score": "الرضا 7 أو أقل", "no_ext": "لا توجد تحويلة"}
MSG_LABEL = {"answer": "ردّ موظف المكالمة", "note": "تحديث من موظف المكالمة على التذكرة",
             "summary": "ملخص المكالمة (آلي) على التذكرة", "seen": "اطّلع موظف المكالمة على السؤال (لا يحتاج رداً)"}
SOLVED_END = "حُلّت ✓"


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _talk_dict(row: "Row | None", now: datetime, names: dict[str, str] | None = None) -> dict:
    """D3: the questions waiting for the agent, what we wrote on the ticket, and the tracker (req 17). The AI
    summary's text is never repeated here (it is read, audited, on the call's summary panel)."""
    if row is None:
        return {"questions": [], "messages": [], "tracker": []}
    return {"questions": [{"id": e.id, "from": e.actor, "text": e.note, "at": _iso(e.at or e.seen_at)}
                          for e in row.questions()],
            "messages": [{"id": m.id, "kind": m.kind, "text": None if m.kind == "summary" else m.text,
                          "status": m.status, "error": m.error, "at": _iso(m.created_at),
                          "sent_at": _iso(m.sent_at)} for m in row.messages],
            "tracker": tracker(row, now, names)}


def tracker(row: "Row", now: datetime, names: dict[str, str] | None = None) -> list[dict]:
    """Req 17, "like a delivery tracker": the forwarded problem step by step, oldest first. Each item: kind (step /
    call / question / answer / note / summary / seen), label, at, state (done / current / late / bad / future),
    detail. Questions and our messages sit at their time; the current step turns "late" after the 24 h."""
    f = row.form
    if f is None or f.responsible_kind != "forward" or f.forward_status is None:
        return []
    from .forward import closed, hop_calls, is_question, our_actors, ours, proof
    items: list[dict] = []

    def add(kind: str, label: str, at_: datetime | None, state: str, detail: str | None = None, **kw) -> dict:
        it = {"kind": kind, "label": label, "at": at_, "state": state, "detail": detail, **kw}
        items.append(it)
        return it

    agent = (names or {}).get(row.call.agent_ext or "") or row.call.agent_ext or "–"
    add("step", "سُجّلت المشكلة", f.first_filled_at, "done",
        f"موظف المكالمة: {agent} · الأولوية: {PRIORITIES.get(f.priority or '', '–')}")
    who = f"«{row.person.name}»" if row.person else "المسؤول"
    if f.forward_status == "failed":
        add("step", f"لم تُحوَّل إلى {who}", f.updated_at, "bad", f.forward_error)
        return _finish(items)
    if f.forward_status != "sent":
        add("step", f"حُوّلت إلى {who}", None, "current", "لم يُرسل بعد، نحاول مرة أخرى")
        add("step", SOLVED_END, None, "future", end=True)
        return _finish(items)
    add("step", f"حُوّلت إلى {who}", f.forwarded_at, "done",
        f"تذكرة رقم {f.forward_ticket_number}" if f.forward_ticket_number else None)
    us = our_actors(row.events)
    sup_hop = row.proof_hop()
    current: dict | None = None
    solved = False
    for i, h in enumerate(row.hops):
        p = row.hop_people.get(h.person_id)
        holder = f"«{p.name if p else '–'}» ({LEVEL_AR.get(h.level, '')})"
        if h.result == "failed":
            add("step", f"تعذّر إرسال نسخة إلى {holder}", h.ended_at, "bad", h.error)
            continue
        if h.result == "pending" or h.sent_at is None:
            continue
        if i > 0:
            add("step", f"انتقلت إلى {holder}", h.sent_at, "done")
        mine = [e for e in row.events if e.hop_id == h.id or (
            e.hop_id is None and e.at is not None and e.at >= h.sent_at and (h.ended_at is None or e.at <= h.ended_at))]
        acts = [e.at or e.seen_at for e in mine if e.kind == "followup" and not ours(e, us)]
        if closed(h) and h.closed_at:
            acts.append(h.closed_at)
        got = min(acts, default=None)
        recv = add("step", f"استلمها {holder}", got, "done" if got else "future")
        from .forward import HOP_TIME
        if got is None and (h.result == "passed_on" or (h.result == "closed_by_other" and h.ended_at
                                                         and h.ended_at > h.sent_at + HOP_TIME)):
            recv["state"], recv["detail"] = "bad", "لم يتخذ أي إجراء على التذكرة"
        elif got is None and h.result == "closed_by_other":
            recv["detail"] = "حلّها غيره قبل أن يتصرف"
        if got is None and h.result == "open" and current is None:
            recv["state"] = "current"
            current = recv
        if h is sup_hop:
            for c in row.fwd_calls:
                sc = row.scores.get(c.id)
                ok = c.status == "answered"
                add("call", f"اتصل {holder} بالعميل", c.start_time, "done" if ok else "bad",
                    (f"ردّ العميل · {c.talk_seconds or 0} ث"
                     + (f" · الرضا {sc}/10" if sc is not None
                        else " · بانتظار تقييم الرضا" if (c.talk_seconds or 0) > 60 else ""))
                    if ok else "لم يرد العميل")
        if h.result == "solved":
            solved = True
            if h.decided_at is not None:            # our supervisor's close (2026-11-22)
                add("step", f"حلّها {holder} ✓ (بقرار المشرف)", h.decided_at, "done", h.solution)
            else:
                add("step", f"حلّها {holder} ✓", h.closed_at if closed(h) and h.closed_at else h.ended_at, "done",
                    h.solution)
        elif h.result == "passed_on":
            why = None
            if h.level == 2 and closed(h) and p is not None and p.ext:
                got_c, sc = hop_calls(row.fwd_calls, row.scores, p.ext, row.call.customer_number, h.sent_at)
                pr = proof([c for c in got_c if h.ended_at is None or c.start_time <= h.ended_at], sc, True)
                why = f"أغلقها دون تأكيد: {PROOF_REASON_AR.get(pr['reason'] or '', '–')}"
            add("step", f"لم تُحل خلال 24 ساعة عند {holder}", h.ended_at, "bad", why)
        elif h.result == "closed_by_other":
            add("step", f"أُغلقت عند {holder}: حلّها غيره", h.ended_at, "done")
        elif h.result == "open" and closed(h):
            pr = row.forward_proof() if h is sup_hop else None
            reason = PROOF_REASON_AR.get(pr["reason"] or "", "") if pr and not pr["ok"] else ""
            add("step", f"حُلّت عند {holder} (غير مؤكد)", h.closed_at, "bad",
                f"غير مؤكد: {reason}" if reason else None, solution=h.solution)
    for e in row.events:
        if is_question(e, us):
            add("question", f"سؤال من «{e.actor or '–'}»", e.at or e.seen_at, "done", e.note)
    for m in row.messages:
        add(m.kind, MSG_LABEL.get(m.kind, m.kind), m.created_at,
            "bad" if m.status == "failed" else "done", m.text if m.kind in ("answer", "note") else None,
            status=m.status, error=m.error)
    if not solved:
        last = add("step", SOLVED_END, None, "future", end=True)
        if row.chain_open() and current is None:
            last["state"] = "current"
            current = last
    due = row.forward_due() if row.chain_open() else None
    if current is not None and due is not None and now > due:
        current["state"] = "late"
        current["detail"] = "متأخر" + (f" · {current['detail']}" if current.get("detail") else "")
    return _finish(items)


def _finish(items: list[dict]) -> list[dict]:
    """Oldest first; a step without a time stays after the item before it; the open end ("حُلّت ✓") is last."""
    end = datetime.max.replace(tzinfo=timezone.utc)
    key: datetime | None = None
    keyed = []
    for i, it in enumerate(items):
        if it.pop("end", False):
            k = end
        else:
            k = it["at"] or key or end
            key = k
        keyed.append((k, i, it))
    keyed.sort(key=lambda x: (x[0], x[1]))
    for _, _, it in keyed:
        it["at"] = _iso(it["at"])
    return [it for _, _, it in keyed]


def _chain_dict(row: "Row | None") -> dict:
    """The forward chain for the page: every hop, the level now, who holds it, until when (24 h). closed_at /
    solution = the solving hop's (else the current one's) so the counters and the line read one place."""
    if row is None or not row.hops:
        return {"hops": [], "level": None, "holders": [], "due_at": None}
    solved = [h for h in row.hops if h.result == "solved"]
    cur = row.current_hops()
    pick = solved[-1] if solved else (cur[0] if cur else row.hops[-1])
    due = row.forward_due()
    return {"hops": [_hop_dict(h, row.hop_people.get(h.person_id)) for h in row.hops],
            "level": None if solved else (cur[0].level if cur else None),
            "holders": [row.hop_people[h.person_id].name for h in cur if h.person_id in row.hop_people],
            "due_at": due.isoformat() if due and not solved else None,
            "closed_at": pick.closed_at.isoformat() if pick.closed_at else None, "solution": pick.solution,
            "solved_at": solved[-1].ended_at.isoformat() if solved and solved[-1].ended_at else None}


def priority_fields(code: str | None) -> dict:
    return {"priority": code, "priority_ar": PRIORITIES.get(code or "")}


def form_dict(f: CallFollowup) -> dict:
    return {"no_followup": f.no_followup, "what_happened": f.what_happened, "solved": f.solved,
            **priority_fields(f.priority),
            "responsible_kind": f.responsible_kind, "responsible_user_id": f.responsible_user_id,
            "forward_person_id": f.forward_person_id, "forward_note": f.forward_note,
            "expected_at": f.expected_at.isoformat() if f.expected_at else None,
            "callback_at": f.callback_at.isoformat() if f.callback_at else None,
            "first_filled_at": f.first_filled_at.isoformat(), "updated_at": f.updated_at.isoformat()}


def _digits9(number: str | None) -> str:
    return "".join(ch for ch in (number or "") if ch.isdigit())[-9:]


def find_callback(s: Session, call: Call, after: datetime, callers) -> Callback | None:
    """Rounds >= 2: the first outbound team call to the same number that starts after `after` and is a proper try
    (see proper_try_sql). answered = any answered team call after `after` (PBX record)."""
    num = _digits9(call.customer_number)
    if not num:
        return None
    base = (Call.direction == "outbound", Call.id != call.id, Call.start_time > after,
            Call.agent_ext.in_(list(callers)), norm_number(Call.customer_number) == num)
    first = s.execute(select(Call).where(*base, proper_try_sql(Call))
                      .order_by(Call.start_time, Call.id).limit(1)).scalar_one_or_none()
    if first is None:
        return None
    answered = s.execute(select(exists().where(*base, Call.status == "answered"))).scalar()
    return Callback(first.id, first.start_time, first.end_time, first.agent_ext, bool(answered), first)


def _early_calls(s: Session, rs: list[Row], callers) -> dict[str, list[Call]]:
    """Early hang-ups (spec 2026-11-15-ring-time) by team extensions to the customers of these rows, by number."""
    nums = {_digits9(r.call.customer_number) for r in rs} - {""}
    if not nums:
        return {}
    since = min(r.call.end_time for r in rs)
    out: dict[str, list[Call]] = {}
    for c in s.scalars(select(Call).where(Call.direction == "outbound", Call.agent_ext.in_(list(callers)),
                                          Call.start_time > since, norm_number(Call.customer_number).in_(nums),
                                          early_hangup_sql(Call)).order_by(Call.start_time, Call.id)):
        out.setdefault(_digits9(c.customer_number), []).append(c)
    return out


def _anchor(row: Row) -> datetime:
    """Round n >= 2: the callback search starts after the note was saved and after the previous callback call ended
    (a second call made before the note does not count for the new round)."""
    last = row.notes[-1]
    prev = row.cb_calls.get(last.cb_call_id or -1)
    return max(last.created_at, prev.end_time) if prev else last.created_at


def rows(s: Session, exts, start: date, lo: datetime, hi: datetime, call_ids: list[int] | None = None, *,
         callers) -> list[Row]:
    """Needed calls in [lo, hi) for these owner extensions, with form, notes, callbacks and came-back flag.
    callers = every team extension (teams.yaml): only their outbound calls count as a callback (decision 2)."""
    cb = _callback_lateral(callers)
    stmt = (select(Call, CallFollowup, cb.c.cb_id, cb.c.cb_time, cb.c.cb_ext, _came_back().label("came_back"),
                   _callback_answered(callers).label("cb_answered"))
            .outerjoin(CallFollowup, CallFollowup.call_id == Call.id)
            .outerjoin(cb, true())
            .where(*needed_conditions(exts, start), Call.start_time >= lo, Call.start_time < hi))
    if call_ids is not None:
        stmt = stmt.where(Call.id.in_(call_ids))
    stmt = stmt.order_by(Call.start_time.desc(), Call.id.desc())
    out = [Row(c, f, a, b, e, bool(cb_), bool(ans)) for c, f, a, b, e, cb_, ans in s.execute(stmt).all()]
    with_form = [r.call.id for r in out if r.form is not None and not r.form.no_followup]
    if not with_form:
        return out
    notes: dict[int, list[CallFollowupNote]] = {}
    for n in s.scalars(select(CallFollowupNote).where(CallFollowupNote.call_id.in_(with_form))
                       .order_by(CallFollowupNote.call_id, CallFollowupNote.round)):
        notes.setdefault(n.call_id, []).append(n)
    need = {r.cb_id for r in out if r.cb_id} | {n.cb_call_id for ns in notes.values() for n in ns if n.cb_call_id}
    calls = {c.id: c for c in s.scalars(select(Call).where(Call.id.in_(need)))} if need else {}
    early = _early_calls(s, [r for r in out if r.call.id in with_form], callers)
    pids = {r.form.forward_person_id for r in out if r.form is not None and r.form.forward_person_id}
    people = {p.id: p for p in s.scalars(select(ForwardPerson).where(ForwardPerson.id.in_(pids)))} if pids else {}
    for r in out:
        r.notes = notes.get(r.call.id, [])
        r.cb_calls = calls
        r.person = people.get(r.form.forward_person_id or -1) if r.form is not None else None
        r.early = [c for c in early.get(_digits9(r.call.customer_number), []) if c.start_time > r.call.end_time]
        f = r.form
        if (f.solved is False and r.cb_time is not None and r.cb_time <= f.first_filled_at) if f else False:
            # a call before the form was saved is not the callback promised in it: look again after the form
            cb1 = find_callback(s, r.call, f.first_filled_at, callers)
            r.cb_id, r.cb_time, r.cb_ext = (cb1.id, cb1.time, cb1.ext) if cb1 else (None, None, None)
            r.cb_answered = bool(cb1 and cb1.answered)
            if cb1 and cb1.id not in calls:
                calls[cb1.id] = s.get(Call, cb1.id)
        if r.notes and r.open_entry() is not None:
            r.current_cb = find_callback(s, r.call, _anchor(r), callers)
    _load_hops(s, [r for r in out if r.form is not None and r.form.forward_status == "sent"])
    return out


def _load_hops(s: Session, rs: list[Row]) -> None:
    """The forward chain of each forwarded row (hops + their people) and the branch supervisor's calls to the
    customer since he got it (team "forward", kept by ingest) with their newest llm_score (this check only)."""
    if not rs:
        return
    from .forward import _sup_calls, digits9, hop_calls
    hops: dict[int, list[ForwardHop]] = {}
    for h in s.scalars(select(ForwardHop).where(ForwardHop.call_id.in_([r.call.id for r in rs]))
                       .order_by(ForwardHop.call_id, ForwardHop.id)):
        hops.setdefault(h.call_id, []).append(h)
    pids = {h.person_id for hs in hops.values() for h in hs}
    people = {p.id: p for p in s.scalars(select(ForwardPerson).where(ForwardPerson.id.in_(pids)))} if pids else {}
    pairs = []
    for r in rs:
        r.hops, r.hop_people = hops.get(r.call.id, []), people
        h = r.proof_hop()
        p = people.get(h.person_id) if h else None
        if h is not None and p is not None and p.ext and h.sent_at:
            pairs.append((p.ext, digits9(r.call.customer_number), h.sent_at))
    calls, scores = _sup_calls(s, pairs)
    for r in rs:
        h = r.proof_hop()
        p = people.get(h.person_id) if h else None
        if h is not None and p is not None:
            r.fwd_calls, r.scores = hop_calls(calls, scores, p.ext, r.call.customer_number, h.sent_at)
    ids = [r.call.id for r in rs]
    events: dict[int, list[ForwardEvent]] = {}
    for e in s.scalars(select(ForwardEvent).where(ForwardEvent.call_id.in_(ids))
                       .order_by(ForwardEvent.call_id, ForwardEvent.at.asc().nulls_last(), ForwardEvent.id)):
        events.setdefault(e.call_id, []).append(e)
    msgs: dict[int, list[ForwardMessage]] = {}
    for m in s.scalars(select(ForwardMessage).where(ForwardMessage.call_id.in_(ids))
                       .order_by(ForwardMessage.call_id, ForwardMessage.id)):
        msgs.setdefault(m.call_id, []).append(m)
    for r in rs:
        r.events, r.messages = events.get(r.call.id, []), msgs.get(r.call.id, [])


def one(s: Session, call: Call, exts, start: date | None) -> Row | None:
    """exts = every team extension (owners and callers alike)."""
    if not is_needed(call, exts, start):
        return None
    got = rows(s, exts, start, call.start_time, call.start_time + timedelta(microseconds=1), [call.id], callers=exts)
    return got[0] if got else None


# "تحتاج حل" (spec 2026-11-16-priority-no-followup, requirement 11): open problems, whatever من/إلى say
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}          # no priority (old form) comes last
UNSOLVED_URGENCY = {"forward_failed": 0, "callback_missed": 0, "solution_overdue": 1, "forward_unconfirmed": 1, "forward_overdue": 1, "note_needed": 2,
                    "waiting_callback": 3, "waiting_solution": 3, "forwarded": 3, "no_number": 4}


def unsolved(s: Session, exts, start: date, now: datetime, *, callers, priority: str | None = None,
             limit: int = 500) -> tuple[list[Row], bool]:
    """Every needed call since followup_start (no date limit) whose latest entry (form, or the last note) says not
    solved, for these owner extensions. A small SQL pre-filter finds the ids, then rows() builds them, so the steps
    are exactly the main list's. Order: priority, then the most urgent step, then the oldest call.
    Bounded (review 2026-11-16): SQL keeps at most 2 x limit ids (by priority, oldest first), so a long history never
    makes the page slow; returns (the first `limit` rows, truncated)."""
    if not exts:
        return [], False
    last_note = (select(CallFollowupNote.solved).where(CallFollowupNote.call_id == CallFollowup.call_id)
                 .order_by(CallFollowupNote.round.desc()).limit(1).correlate(CallFollowup).scalar_subquery())
    rank = case(*[(CallFollowup.priority == k, v) for k, v in PRIORITY_ORDER.items()], else_=len(PRIORITY_ORDER))
    stmt = (select(Call.id).join(CallFollowup, CallFollowup.call_id == Call.id)
            .where(*needed_conditions(exts, start), CallFollowup.no_followup.is_(False),
                   func.coalesce(last_note, CallFollowup.solved).is_(False),
                   ~exists().where(ForwardHop.call_id == Call.id, ForwardHop.result == "solved"))   # chain solved
            .order_by(rank, Call.start_time, Call.id).limit(2 * limit + 1))
    if priority:
        stmt = stmt.where(CallFollowup.priority == priority)
    ids = list(s.scalars(stmt))
    if not ids:
        return [], False
    got = [r for r in rows(s, exts, start, day_bounds(start)[0], now + timedelta(days=1), ids[:2 * limit],
                           callers=callers) if r.step(now) in OPEN_STEPS]
    got.sort(key=lambda r: (PRIORITY_ORDER.get(r.form.priority or "", 3),
                            -1 if r.questions() else UNSOLVED_URGENCY.get(r.step(now), 9),   # D3: a question first
                            r.call.start_time, r.call.id))
    return got[:limit], len(got) > limit or len(ids) > 2 * limit


def responsible_options(s: Session) -> list[dict]:
    users = s.scalars(select(AppUser).where(AppUser.active.is_(True), AppUser.role.in_(("supervisor", "agent")))
                      .order_by(AppUser.role.desc(), AppUser.username))
    return [{"id": u.id, "username": u.username, "role": u.role, "ext": u.ext} for u in users]


# ---------- KPIs ----------

def _empty() -> dict:
    return {"forms_due": 0, "forms_on_time": 0, "forms_rate": None, "callbacks_due": 0, "callbacks_on_time": 0,
            "callbacks_rate": None, "came_back": 0,
            # follow-up stages spec
            "answered_forms": 0, "solved_first": 0, "first_rate": None, "solved_after": 0, "open": 0,
            "rounds_to_solve": 0, "avg_rounds": None, "notes_due": 0, "notes_on_time": 0, "notes_rate": None,
            # spec 2026-11-16-priority-no-followup (show-only): "no follow-up" forms and their share of filled forms
            "no_followup": 0, "no_followup_rate": None}


def _rate(a: int, b: int) -> float | None:
    return a / b if b else None


def kpi(s: Session, lo: datetime, hi: datetime, exts: list[str], now: datetime | None = None, *,
        callers) -> dict:
    """Follow-up KPIs of [lo, hi), counted in the period of the original call. `active` = the admin switched them
    on (followup_kpi_since): until then they are show-only (page, never PDF). Counting starts at the later of
    followup_start and followup_kpi_since (when set). Callbacks and notes count every round."""
    now = now or _utcnow()
    st = settings(s)
    start, since = st[SETTING_START], st[SETTING_KPI]
    out = {"enabled": start is not None, "active": since is not None,
           "start": start.isoformat() if start else None, "since": since.isoformat() if since else None,
           "summary": _empty(), "agents": {e: _empty() for e in exts}}
    if start is None or not exts:
        return out
    count_from = max(start, since) if since else start
    for r in rows(s, exts, count_from, lo, hi, callers=callers):
        fstate = form_state(r.call, r.form, now)
        step = r.step(now)
        cbs = r.callback_rounds(now)
        nts = r.note_states(now)
        back = bool(r.came_back and r.form and r.form.solved is True and not r.notes)
        for b in (out["summary"], out["agents"].setdefault(r.call.agent_ext, _empty())):
            if fstate != "missing":          # deadline passed, or already filled
                b["forms_due"] += 1
                b["forms_on_time"] += fstate == "on_time"
            for st_ in cbs:
                if st_ in ("on_time", "late", "missed"):
                    b["callbacks_due"] += 1
                    b["callbacks_on_time"] += st_ == "on_time"
            for st_ in nts:
                if st_ != "pending":
                    b["notes_due"] += 1
                    b["notes_on_time"] += st_ == "on_time"
            if r.form is not None and not r.form.no_followup:
                b["answered_forms"] += 1
            b["no_followup"] += step == "no_followup"
            b["solved_first"] += step == "solved_first"
            if step == "solved_after":
                b["solved_after"] += 1
                b["rounds_to_solve"] += len(r.notes)
            b["open"] += step in KPI_OPEN_STEPS        # no_number: no callback possible, not "open" (spec 7)
            b["came_back"] += back
    for b in (out["summary"], *out["agents"].values()):
        b["forms_rate"] = _rate(b["forms_on_time"], b["forms_due"])
        b["callbacks_rate"] = _rate(b["callbacks_on_time"], b["callbacks_due"])
        b["first_rate"] = _rate(b["solved_first"], b["answered_forms"])
        b["avg_rounds"] = _rate(b["rounds_to_solve"], b["solved_after"])
        b["notes_rate"] = _rate(b["notes_on_time"], b["notes_due"])
        b["no_followup_rate"] = _rate(b["no_followup"], b["no_followup"] + b["answered_forms"])
    return out


# ---------- notes after a callback (follow-up stages spec) ----------

def add_note(s: Session, call: Call, data: dict, user: AppUser, exts, now: datetime | None = None
             ) -> CallFollowupNote:
    """Append the note of the current round. Only when the step is note_needed; `data["round"]` must be the round
    the page showed (a second click or an old page gets 409 instead of a second note)."""
    now = now or _utcnow()
    row = one(s, call, exts, get_setting(s, SETTING_START))
    if row is None or row.step(now) not in NOTE_STEPS:
        raise NoteConflict("لا تحتاج هذه المكالمة ملاحظة الآن (أعد تحميل الصفحة)")
    rnd = len(row.notes) + 2                              # form = round 1, the note opens the next round
    if data.get("round") != rnd:
        raise NoteConflict("تغيّرت حالة المتابعة، أعد تحميل الصفحة")
    clean = validate(s, {**data, "no_followup": False}, call, now, priority=row.form.priority,
                     forwarded=forwarded(row.form))
    last = row.notes[-1] if row.notes else row.form
    cb = row.cb_of_round(rnd - 1) if last.callback_at is not None else None     # no callback asked: none to report
    n = CallFollowupNote(call_id=call.id, round=rnd, cb_call_id=cb.id if cb else None,
                         what_happened=clean["what_happened"], solved=clean["solved"],
                         responsible_kind=clean["responsible_kind"], responsible_user_id=clean["responsible_user_id"],
                         expected_at=clean["expected_at"], callback_at=clean["callback_at"],
                         created_by=user.id, created_at=now)
    s.add(n)
    if (forwarded(row.form) and (clean["what_happened"] or "").strip() and user.role == "agent" and user.ext
            and user.ext == call.agent_ext):
        # D3: the note is written on the ticket too (the person holding it sees the update)
        s.add(ForwardMessage(call_id=call.id, kind="note", text=clean["what_happened"].strip()[:600],
                             created_by=user.id, created_at=now, status="pending"))
    try:
        s.commit()
    except IntegrityError:          # the same round saved twice at once
        s.rollback()
        raise NoteConflict("حُفظت الملاحظة للتو من طلب آخر، أعد تحميل الصفحة") from None
    return n


class NoteConflict(Exception):
    pass


# ---------- form validation ----------

class FormError(ValueError):
    pass


FORM = object()      # validate(priority=FORM): the first form, whose own `priority` field is checked and kept


NO_FORWARD = {"forward_person_id": None, "forward_note": None}


def validate(s: Session, data: dict, call: Call, now: datetime, *, priority=FORM, forward_ok: bool = False,
             forwarded: bool = False) -> dict:
    """Server-side checks of a submitted form (priority=FORM) or note (priority = the first form's priority;
    a note has none of its own); returns the clean fields. A note's clean fields never carry a priority.
    Part 4: forward_ok = the link to the customer-service system is on (form only); forwarded = this note belongs to
    a forwarded problem (only the supervisor closes it; the note keeps kind "forward")."""
    is_form = priority is FORM
    if data.get("no_followup"):
        out = {"no_followup": True, "what_happened": None, "solved": None, "responsible_kind": None,
               "responsible_user_id": None, "expected_at": None, "callback_at": None}
        return {**out, "priority": None, **NO_FORWARD} if is_form else out    # nothing to rank (spec decision)
    text = (data.get("what_happened") or "").strip()
    if not text:
        raise FormError("اكتب ما حدث في المكالمة")
    if len(text) > 500:
        raise FormError("النص أطول من 500 حرف")
    if is_form:
        priority = data.get("priority")
        if priority not in PRIORITIES:
            raise FormError("اختر الأولوية")
    solved = data.get("solved")
    if solved not in (True, False):
        raise FormError("اختر: هل حُلّت المشكلة؟")
    if forwarded and solved is True:
        raise FormError("المشكلة محوّلة لمشرف الفرع: هو من يغلقها")
    clean = {"no_followup": False, "what_happened": text, "solved": solved, "responsible_kind": None,
             "responsible_user_id": None, "expected_at": None, "callback_at": None}
    if is_form:
        clean.update(priority=priority, **NO_FORWARD)
    if solved:
        return clean
    kind = data.get("responsible_kind")
    if kind not in RESPONSIBLE_KINDS:
        raise FormError("اختر المسؤول عن الحل")
    if forwarded and kind != "forward":
        raise FormError("المشكلة محوّلة لمشرف الفرع: لا يتغيّر المسؤول")
    if kind == "forward" and not forwarded:
        if not is_form:
            raise FormError("التحويل لمشرف فرع من النموذج الأول فقط")
        if not forward_ok:
            raise FormError("التحويل لمشرف فرع غير مفعّل")
        if not _has_number(call.customer_number):
            raise FormError("لا يوجد رقم للعميل: لا يمكن التحويل")
        from .forward import choosable
        person = choosable(s, data.get("forward_person_id"))
        if person is None:
            raise FormError("اختر مشرف الفرع")
        fnote = (data.get("forward_note") or "").strip()
        if len(fnote) > FORWARD_NOTE_MAX:
            raise FormError("ملاحظة المسؤول أطول من 500 حرف")
        clean.update(forward_person_id=person.id, forward_note=fnote or None)
    if kind == "forward":
        # the chain has its own times (24 h per person: employee -> branch supervisor -> management), so the agent
        # gives none (user, 2026-11-22). Expected = the first person's 24 h; no callback by the agent. Times sent by
        # an old page are ignored.
        clean.update(responsible_kind=kind, expected_at=now + FORWARD_HOP, callback_at=None)
        return clean
    uid = data.get("responsible_user_id")
    if kind == "user":
        u = s.get(AppUser, uid) if isinstance(uid, int) else None
        if u is None or not u.active or u.role not in ("supervisor", "agent"):
            raise FormError("المسؤول غير موجود")
        clean["responsible_user_id"] = u.id
    # no expected solve time and no callback time any more (user, 2026-11-23): the agent only says who solves it.
    # Times sent by an old page are ignored; old rows keep theirs.
    clean.update(responsible_kind=kind, expected_at=None, callback_at=None)
    return clean


def save(s: Session, call: Call, data: dict, user: AppUser, now: datetime | None = None, *,
         forward_ok: bool = False) -> CallFollowup:
    now = now or _utcnow()
    # locked: the send job (part 4) must not make a ticket while this edit changes the form
    # populate_existing: the caller may hold this row already; re-read it under the lock (review D1: a stale
    # "pending" let an edit through right after the job marked it "sent")
    f = s.scalars(select(CallFollowup).where(CallFollowup.call_id == call.id).with_for_update()
                  .execution_options(populate_existing=True)).first()
    if f is not None and f.forward_status == "sent":
        raise FormError("حُوّلت المشكلة لمشرف الفرع: لا يمكن تعديل النموذج")
    clean = validate(s, data, call, now, forward_ok=forward_ok)
    # forward: (re)send on every save while not sent; any other answer drops a waiting / failed send
    clean.update(forward_status="pending" if clean["responsible_kind"] == "forward" else None,
                 forward_error=None, forward_tries=0)
    if f is None:
        f = CallFollowup(call_id=call.id, owner_ext=call.agent_ext, filled_by=user.id, first_filled_at=now,
                         updated_by=user.id, updated_at=now, **clean)
        s.add(f)
    else:
        for k, v in clean.items():
            setattr(f, k, v)
        f.updated_by, f.updated_at = user.id, now
    try:
        s.commit()
    except IntegrityError:          # two first saves at once (double click): the other one won
        s.rollback()
        raise FormError("حُفظ النموذج للتو من طلب آخر، أعد فتح المكالمة") from None
    return f
