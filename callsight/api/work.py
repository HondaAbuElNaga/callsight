"""Agent + supervisor work routes (spec 2026-11-15-agent-supervisor): follow-up form, follow-ups list, shared calls,
the agent's summary of a call. Registered by create_app (main.py), which passes its dependency types.

No `from __future__ import annotations` here: the route signatures use the Annotated types passed in at runtime.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import followups as fu
from .. import shares as shr
from ..config import RIYADH
from ..db.models import (Agent, AdminLog, AppUser, Call, CallFollowup, CallFollowupNote, CallPrivateNote, CallShare,
                         ShareMessage)
from ..ingest.service import day_bounds
from . import auth
from .calls import visible_call, visible_scope

MAX_LIST_DAYS = 62
LIST_LIMIT = 500
STATES = ("", "action", "forms", "notes", "callbacks_today", "missed", "open", "solved", "todo", "callbacks",
          "open_callbacks", "came_back", "no_followup", "forward_solved_today", "questions")
ACTION_LOOKBACK_DAYS = 62      # unfinished work before من is counted apart ("older_action"), never forgotten
# most urgent first in "needs action"
URGENCY = {"forward_failed": 0, "callback_missed": 0, "solution_overdue": 1, "note_needed": 2, "overdue": 3, "waiting_callback": 4,
           "waiting_solution": 5, "required": 6}


class FollowupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_followup: StrictBool = False
    what_happened: str | None = Field(default=None, max_length=500)
    solved: StrictBool | None = None
    priority: str | None = Field(default=None, max_length=10)     # high / medium / low (first form only)
    responsible_kind: str | None = Field(default=None, max_length=20)
    responsible_user_id: StrictInt | None = None
    expected_at: datetime | None = None
    callback_at: datetime | None = None
    forward_person_id: StrictInt | None = None                    # part 4: responsible_kind "forward"
    forward_note: str | None = Field(default=None, max_length=500)


class NoteIn(BaseModel):
    """A note after a callback (follow-up stages spec): `round` = the round the page showed (stops double saves)."""
    model_config = ConfigDict(extra="forbid")
    round: StrictInt
    what_happened: str | None = Field(default=None, max_length=500)
    solved: StrictBool | None = None
    responsible_kind: str | None = Field(default=None, max_length=20)
    responsible_user_id: StrictInt | None = None
    expected_at: datetime | None = None
    callback_at: datetime | None = None


class ForwardMsgIn(BaseModel):
    """D3: the agent answers the question on the forwarded ticket (answer), or marks it seen with no answer."""
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern=r"^(answer|seen)$")
    text: str | None = Field(default=None, max_length=500)
    upto: StrictInt | None = None     # the newest question id on the agent's screen (later ones stay open)


class MyNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=300)


class StudentSearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(max_length=40)        # the student's national ID or registered phone


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)


REPLY_MIN = 10      # the agent's 24 h reply: the first message of at least 10 letters ("ok" / "تم" is not an answer)


class ShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_user_id: StrictInt
    note: str = Field(min_length=1, max_length=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _names(s: Session) -> dict[str, str]:
    return dict(s.execute(select(Agent.ext, Agent.name)).all())


def register(app: FastAPI, *, DB, User, Manager, cfg, audit_analysis) -> None:
    """audit_analysis(s, user, call_id) writes the 'analysis' audit row (deduplicated) when a summary is read."""

    def team_exts() -> list[str]:
        return sorted(cfg.teams().ext_team)

    def staff(user: AppUser) -> bool:
        return user.role in auth.STAFF_ROLES

    def owned_call(s: Session, call_id: int, user: AppUser) -> Call:
        c = visible_call(s, call_id, visible_scope(user))
        if c is None:
            raise HTTPException(404, "no such call")
        return c

    # ---------- follow-up form ----------

    def form_payload(s: Session, c: Call, user: AppUser, now: datetime) -> dict:
        start = fu.get_setting(s, fu.SETTING_START)
        own = staff(user) or (user.ext is not None and c.agent_ext == user.ext)
        row = fu.one(s, c, team_exts(), start) if own else None
        if row is None:
            return {"needed": False, "enabled": start is not None}
        d = row.as_dict(now, _names(s), usernames(s))
        from .. import forward
        on = cfg.forward_enabled()
        sent = row.form is not None and row.form.forward_status == "sent"
        why = "forwarded to the branch supervisor: the form can no longer change" if sent else None
        why = why or may_edit(row.form, c, user, now)
        return {"needed": True, "enabled": True, **d, "can_edit": why is None, "lock_reason": why,
                "options": fu.responsible_options(s), "forward_enabled": on,
                "forward_options": forward.options(s) if on else [],
                "training_enabled": cfg.training_lookup is not None}

    def may_edit(form, c: Call, user: AppUser, now: datetime) -> str | None:
        """None = may save. Agents: own call, before the form deadline, and never once the callback time passed."""
        if staff(user):
            return None
        if c.agent_ext != user.ext:
            return "not your call"
        if fu.forward_failed(form):         # the send failed: the agent picks again, whatever the time (review D1)
            return None
        if now > fu.form_deadline(c) and form is not None:
            return "after the form deadline only a supervisor can change it"
        if form is not None and form.callback_at is not None and form.callback_at <= now:
            return "the callback time has passed: only a supervisor can change it"
        return None

    @app.get("/api/calls/{call_id}/followup")
    def get_followup(call_id: int, user: User, s: DB) -> dict:
        c = owned_call(s, call_id, user)
        return form_payload(s, c, user, _now())

    @app.get("/api/calls/{call_id}/student")
    def get_student(call_id: int, user: User, s: DB) -> dict:
        return _student(call_id, user, s, None)

    @app.post("/api/calls/{call_id}/student")
    def search_student(call_id: int, body: StudentSearchIn, user: User, s: DB) -> dict:
        """The same look-up with what the agent typed (in the body, never the URL: access logs keep URLs)."""
        return _student(call_id, user, s, body.q)

    def _student(call_id: int, user, s, q: str | None) -> dict:
        """The training look-up of the caller (spec "Part 4: the training lookup"): the student(s) with this phone
        and the suggested person for the forward form. Only for the call's agent or staff, only on a call that needs
        the follow-up form. Never stored; an outage only hides the box.
        q: the student's national ID or registered phone, typed by the agent when the caller's number finds nobody
        (the student called from another phone)."""
        c = owned_call(s, call_id, user)
        if cfg.training_lookup is None:
            return {"enabled": False, "students": [], "suggestion": None, "error": None}
        own = staff(user) or (user.ext is not None and c.agent_ext == user.ext)
        row = fu.one(s, c, team_exts(), fu.get_setting(s, fu.SETTING_START)) if own else None
        if row is None:
            raise HTTPException(404, "no follow-up form on this call")
        if may_edit(row.form, c, user, _now()) is not None:     # shown on an open form only (review)
            raise HTTPException(403, "the form is closed")
        from .. import training_api
        if q is not None:
            phone = training_api.search_key(q)
            if phone is None:
                return {"enabled": True, "students": [], "suggestion": None, "error": "bad_query"}
        else:
            phone = training_api.local_mobile(c.customer_number)
            if phone is None:
                return {"enabled": True, "students": [], "suggestion": None, "error": "not_mobile"}
        try:
            students = cfg.training_lookup(phone)
        except training_api.TrainingUnavailable:
            return {"enabled": True, "students": [], "suggestion": None, "error": "unavailable"}
        from .. import forward
        sug = forward.suggest(s, students) if students and cfg.forward_enabled() else None
        return {"enabled": True, "students": students, "suggestion": sug, "error": None}

    @app.put("/api/calls/{call_id}/followup")
    def put_followup(call_id: int, body: FollowupIn, user: User, s: DB) -> dict:
        c = owned_call(s, call_id, user)
        now = _now()
        start = fu.get_setting(s, fu.SETTING_START)
        if not fu.is_needed(c, team_exts(), start):
            raise HTTPException(409, "this call needs no follow-up form")
        if not staff(user) and c.agent_ext != user.ext:
            raise HTTPException(404, "no such call")       # a shared call of a colleague: not even confirmed
        form = s.scalars(select(CallFollowup).where(CallFollowup.call_id == c.id)).first()
        has_notes = s.scalar(select(CallFollowupNote.id).where(CallFollowupNote.call_id == c.id).limit(1)) is not None
        if has_notes and fu.forward_failed(form) and (body.solved is not False or body.responsible_kind != "forward"):
            # the notes decide the step: only another supervisor is picked in the form, the rest is a note
            raise HTTPException(422, "بعد الملاحظات: اختر مشرفًا آخر هنا، أو اكتب ملاحظة للإجابات الأخرى")
        if has_notes and not fu.forward_failed(form):
            raise HTTPException(409, "notes after a callback exist: the form can no longer change")
        why = may_edit(form, c, user, now)        # a late FIRST form is allowed (it counts as late)
        if why:
            raise HTTPException(403, why)
        try:
            f = fu.save(s, c, body.model_dump(), user, now, forward_ok=cfg.forward_enabled())
        except fu.FormError as exc:
            s.rollback()
            raise HTTPException(422, str(exc)) from None
        if f.forward_status == "pending" and cfg.forward_send is not None:
            try:
                cfg.forward_send()          # send now; the 5-minute job retries anyway if this fails
            except Exception:  # noqa: BLE001  the form is saved, the send is only sooner
                pass
        return form_payload(s, c, user, now)

    @app.post("/api/calls/{call_id}/followup/notes")
    def add_note(call_id: int, body: NoteIn, user: User, s: DB) -> dict:
        c = owned_call(s, call_id, user)
        if not staff(user) and c.agent_ext != user.ext:
            raise HTTPException(404, "no such call")       # a shared call of a colleague: not even confirmed
        now = _now()
        try:
            fu.add_note(s, c, body.model_dump(), user, team_exts(), now)
        except fu.NoteConflict as exc:
            s.rollback()
            raise HTTPException(409, str(exc)) from None
        except fu.FormError as exc:
            s.rollback()
            raise HTTPException(422, str(exc)) from None
        return form_payload(s, c, user, now)

    @app.post("/api/calls/{call_id}/forward/messages")
    def forward_message(call_id: int, body: ForwardMsgIn, user: User, s: DB) -> dict:
        """D3 (req 18): the call's agent answers the person holding the forwarded problem; the worker writes it on
        the ticket. "seen" closes the questions shown without writing anything there. Supervisors only watch."""
        from ..db.models import ForwardMessage
        c = owned_call(s, call_id, user)
        if staff(user):
            raise HTTPException(403, "the call's agent answers the question")
        if c.agent_ext != user.ext:
            raise HTTPException(404, "no such call")
        now = _now()
        row = fu.one(s, c, team_exts(), fu.get_setting(s, fu.SETTING_START))
        if row is None or row.form is None or row.form.forward_status != "sent" or not row.chain_open():
            raise HTTPException(409, "لا توجد تذكرة مفتوحة لهذه المشكلة (أعد تحميل الصفحة)")
        qs = row.questions()
        if not qs:                  # an answer belongs to a question (bounds what reaches the ticket)
            raise HTTPException(409, "لا توجد أسئلة تنتظر ردك (أعد تحميل الصفحة)")
        newest = max(q.id for q in qs)
        upto = newest if body.upto is None else body.upto
        if not any(q.id <= upto for q in qs) or upto > newest:
            raise HTTPException(409, "تغيّرت الأسئلة، أعد تحميل الصفحة")
        if body.kind == "seen":
            m = ForwardMessage(call_id=c.id, kind="seen", text=None, created_by=user.id, created_at=now,
                               status="done", upto_event_id=upto)
        else:
            text_ = (body.text or "").strip()
            if not text_:
                raise HTTPException(422, "اكتب الرد")
            m = ForwardMessage(call_id=c.id, kind="answer", text=text_, created_by=user.id, created_at=now,
                               status="pending", upto_event_id=upto)
        s.add(m)
        s.add(AdminLog(user_id=user.id, action=f"forward_{body.kind}", target=f"call:{c.id}", at=now))   # no text
        s.commit()
        if m.status == "pending" and cfg.forward_send is not None:
            try:
                cfg.forward_send()          # write it now; the 5-minute job does it anyway if this fails
            except Exception:  # noqa: BLE001
                pass
        return form_payload(s, c, user, now)

    @app.put("/api/calls/{call_id}/my-note")
    def put_my_note(call_id: int, body: MyNoteIn, user: User, s: DB) -> dict:
        """The user's own reminder on a call they can see; empty text deletes it. Nobody else ever reads it."""
        c = owned_call(s, call_id, user)
        text = body.text.strip()
        row = s.scalars(select(CallPrivateNote).where(CallPrivateNote.call_id == c.id,
                                                      CallPrivateNote.user_id == user.id)).first()
        if not text:
            if row is not None:
                s.delete(row)
                s.commit()
            return {"call_id": c.id, "text": None}
        if row is None:
            row = CallPrivateNote(call_id=c.id, user_id=user.id, text=text, updated_at=_now())
            s.add(row)
        else:
            row.text, row.updated_at = text, _now()
        try:
            s.commit()
        except IntegrityError:      # two first saves at once: keep the other one
            s.rollback()
            raise HTTPException(409, "saved a moment ago, reload the page") from None
        return {"call_id": c.id, "text": row.text}

    # ---------- follow-ups list ----------

    def asked(i: dict) -> bool:
        """D3: the person holding the forwarded problem asked something; the agent must answer."""
        return bool((i.get("forward") or {}).get("questions"))

    def action_due(i: dict, today: date) -> bool:
        if i["step"] in fu.ACTION_STEPS or asked(i):
            return True
        cb = i["callback"]
        if i["step"] == "waiting_solution" and i.get("solve_by"):
            return datetime.fromisoformat(i["solve_by"]).astimezone(RIYADH).date() <= today
        return (i["step"] == "waiting_callback" and cb is not None
                and datetime.fromisoformat(cb["at"]).astimezone(RIYADH).date() <= today)

    def today_cb(i: dict, today: date) -> bool:
        cb = i["callback"]
        return (i["step"] == "waiting_callback" and cb is not None
                and datetime.fromisoformat(cb["at"]).astimezone(RIYADH).date() == today)

    def counts(items: list[dict], today: date) -> dict:
        return {"forms": sum(i["step"] in ("required", "overdue") for i in items),
                "notes": sum(i["step"] in ("note_needed", "solution_overdue") for i in items),
                "callbacks_today": sum(today_cb(i, today) for i in items),
                "missed": sum(i["step"] == "callback_missed" for i in items),
                "no_followup": sum(i["step"] == "no_followup" for i in items)}

    def solved_today(i: dict, today: date) -> bool:
        """Part 4 D2 (req 19): the supervisor's solve was confirmed and the ticket closed today."""
        f = i.get("forward") or {}
        at_ = f.get("solved_at") or f.get("closed_at")      # the chain: the day it was confirmed solved
        return (i["step"] == "forward_solved" and bool(at_)
                and datetime.fromisoformat(at_).astimezone(RIYADH).date() == today)

    @app.get("/api/followups")
    def list_followups(
        user: User, s: DB,
        date_from: Annotated[date | None, Query(alias="from")] = None,
        date_to: Annotated[date | None, Query(alias="to")] = None,
        agent: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None,
        state: Annotated[str, Query()] = "action",
        priority: Annotated[str | None, Query(pattern=r"^(high|medium|low)$")] = None,
    ) -> dict:
        if state not in STATES:
            raise HTTPException(422, "unknown state")
        today = datetime.now(RIYADH).date()
        d_to = date_to or today
        d_from = date_from or d_to                     # the page opens on today (user, 2026-11-15)
        if d_to < d_from or (d_to - d_from).days > MAX_LIST_DAYS:
            raise HTTPException(422, "bad date range")
        st = fu.settings(s)
        out = {"settings": {k: v.isoformat() if v else None for k, v in st.items()}, "items": [],
               "linked": staff(user) or bool(user.ext and user.ext in team_exts()),
               "counts": {"forms": 0, "notes": 0, "callbacks_today": 0, "missed": 0, "no_followup": 0,
                          "forward_solved_today": 0, "questions": 0},
               "older_action": 0, "unsolved": []}
        start = st[fu.SETTING_START]
        if start is None:
            return out
        exts = team_exts()
        if not staff(user):
            if not out["linked"]:
                return out            # the page says: your account is not linked to a team extension
            exts = [user.ext]
        elif agent:
            if agent not in exts:
                raise HTTPException(422, "extension is not a team agent (teams.yaml)")
            exts = [agent]
        now = _now()
        names, users = _names(s), usernames(s)

        def load(lo: date, hi: date) -> list[dict]:
            return [r.as_dict(now, names, users) for r in fu.rows(s, exts, start, day_bounds(lo)[0],
                                                                   day_bounds(hi)[1], callers=team_exts())]
        # The list and the counters follow من/إلى. Work left in earlier days (62-day look-back) is counted apart,
        # so the page can say "N items need work in earlier days" and nothing old is forgotten.
        back = today - timedelta(days=ACTION_LOOKBACK_DAYS)
        lo, hi = day_bounds(d_from)[0], day_bounds(d_to)[1]
        recent = load(back, today)
        if d_from >= back and d_to <= today:     # inside the window already loaded: no second query
            items = [i for i in recent if lo <= datetime.fromisoformat(i["start_time"]) < hi]
        else:
            items = load(d_from, d_to)
        out["counts"] = counts(items, today)      # counters count every priority (spec requirement 13)
        # solved by the supervisor today, whatever day the call was (62-day look-back)
        out["counts"]["forward_solved_today"] = sum(solved_today(i, today) for i in recent)
        if state == "forward_solved_today":
            items = [i for i in recent if solved_today(i, today)]
        if priority and state != "no_followup":   # those forms have no priority: the filter would empty them
            items = [i for i in items if i["form"] and i["form"]["priority"] == priority]
        out["older_action"] = sum(1 for i in recent if datetime.fromisoformat(i["start_time"]) < lo
                                  and action_due(i, today))
        # "تحتاج حل" (requirement 11): every open problem of these agents, never limited by من/إلى or the state
        # filter; only "who" (agent / الموظف filter) and the priority filter apply
        # one query for every priority: the counter counts them all (requirement 13), the list follows the filter
        got, out["unsolved_truncated"] = fu.unsolved(s, exts, start, now, callers=team_exts(), limit=LIST_LIMIT)
        everyone = [r.as_dict(now, names, users) for r in got]
        unsolved = [i for i in everyone if not priority or (i["form"] and i["form"]["priority"] == priority)]
        # D3: questions waiting for an answer, whatever day the call was ("أسئلة تنتظر ردك")
        out["counts"]["questions"] = sum(asked(i) for i in everyone)
        if state == "questions":
            items = [i for i in unsolved if asked(i)]
        if state == "action":
            items = sorted((i for i in items if action_due(i, today)),
                           key=lambda i: (-1 if asked(i) else URGENCY.get(i["step"], 9), i["start_time"]))
        elif state == "forms":
            items = [i for i in items if i["step"] in ("required", "overdue")]
        elif state == "notes":
            items = [i for i in items if i["step"] in ("note_needed", "solution_overdue")]   # = the counter
        elif state == "callbacks_today":
            items = [i for i in items if today_cb(i, today)]
        elif state == "missed":
            items = [i for i in items if i["step"] == "callback_missed"]
        elif state == "open":
            items = [i for i in items if i["step"] in fu.OPEN_STEPS]
        elif state == "solved":
            items = [i for i in items if i["step"] in fu.SOLVED_STEPS]
        elif state == "todo":
            items = [i for i in items if i["form_state"] in ("missing", "overdue")]
        elif state == "callbacks":
            items = [i for i in items if i["callback"]]
        elif state == "open_callbacks":
            items = [i for i in items if i["callback"] and i["callback"]["status"] in ("waiting", "missed")]
        elif state == "came_back":
            items = [i for i in items if i["came_back"]]
        elif state == "no_followup":
            items = [i for i in items if i["step"] == "no_followup"]
        out["truncated"] = len(items) > LIST_LIMIT
        items = items[:LIST_LIMIT]
        shown = {i["call_id"] for i in items} | {i["call_id"] for i in unsolved}
        mine = dict(s.execute(select(CallPrivateNote.call_id, CallPrivateNote.text).where(
            CallPrivateNote.user_id == user.id, CallPrivateNote.call_id.in_(shown))).all()) if shown else {}
        for i in items + unsolved:
            i["my_note"] = mine.get(i["call_id"])      # the caller's own note only (private-note spec)
        out["items"] = items
        out["unsolved"] = unsolved
        return out

    # ---------- agent summary (decision 1: summary only, never the estimate / reason / flags) ----------

    @app.get("/api/calls/{call_id}/summary")
    def call_summary(call_id: int, user: User, s: DB) -> dict:
        from ..analysis import views
        owned_call(s, call_id, user)
        topics, pv = cfg_analysis(s)
        a = views.shown_analysis(s, call_id, cfg.llm_model, pv)
        content = views._content(a, topics)   # noqa: SLF001 - same shaping as the staff panel
        if not content:
            return {"call_id": call_id, "analysis": None}
        keep = {k: content.get(k) for k in ("status", "summary", "topic_label", "action_items")}
        if keep["status"] != "done":
            keep = {"status": keep["status"]}
        else:
            audit_analysis(s, user, call_id)
        return {"call_id": call_id, "analysis": keep}

    def cfg_analysis(s: Session) -> tuple[dict, str]:
        from ..analysis.llm import prompt_version
        from ..analysis.rulestore import current_topics
        topics = cfg.topics() if cfg.topics else current_topics(s)     # the rules page list (or topics.yaml)
        return topics, prompt_version(topics)

    # ---------- shared calls = chats (spec 2026-11-25-share-chat) ----------
    # A supervisor shares a call with an agent: that starts a chat (first message = the note). Staff archive it
    # (can reopen) or close it (gone for both sides, still in the 24 h KPI). Agents only see open chats.

    def is_agent_msg(sh: CallShare, m: ShareMessage) -> bool:
        return m.user_id == sh.to_user_id

    def chat_ctx(s: Session, shares: list[CallShare], user: AppUser) -> dict:
        """What chat_dict needs for many chats, read in a few queries (not one per chat)."""
        ids = [sh.id for sh in shares]
        call_ids = {sh.call_id for sh in shares}
        calls = {c.id: c for c in s.scalars(select(Call).where(Call.id.in_(call_ids)))} if call_ids else {}
        last, unread = {}, {}
        if ids:
            newest = select(func.max(ShareMessage.id)).where(ShareMessage.share_id.in_(ids)).group_by(ShareMessage.share_id)
            last = {m.share_id: m for m in s.scalars(select(ShareMessage).where(ShareMessage.id.in_(newest)))}
            mark = CallShare.staff_read_at if staff(user) else CallShare.agent_read_at
            other = (ShareMessage.user_id == CallShare.to_user_id) if staff(user) else (ShareMessage.user_id != CallShare.to_user_id)
            unread = dict(s.execute(
                select(ShareMessage.share_id, func.count()).join(CallShare, CallShare.id == ShareMessage.share_id)
                .where(ShareMessage.share_id.in_(ids), other, or_(mark.is_(None), ShareMessage.created_at > mark))
                .group_by(ShareMessage.share_id)).all())
        return {"names": usernames(s), "exts": dict(s.execute(select(AppUser.id, AppUser.ext)).all()), "calls": calls, "last": last, "unread": unread,
                "since": shr.rule_start(s), "now": _now()}

    def chat_dict(sh: CallShare, ctx: dict, user: AppUser) -> dict:
        c = ctx["calls"].get(sh.call_id)
        users = ctx["names"]
        iso = lambda t: t.isoformat() if t else None      # noqa: E731
        m = ctx["last"].get(sh.id)
        return {"id": sh.id, "call_id": sh.call_id, "note": sh.note,
                "from": users.get(sh.from_user_id, ""), "to": users.get(sh.to_user_id, ""), "to_user_id": sh.to_user_id,
                "to_ext": ctx["exts"].get(sh.to_user_id),
                "created_at": sh.created_at.isoformat(), "seen_at": iso(sh.seen_at),
                "status": sh.status, "archived_at": iso(sh.archived_at), "closed_at": iso(sh.closed_at),
                "first_reply_at": iso(sh.first_reply_at), "deadline": shr.deadline(sh).isoformat(),
                "reply_state": shr.reply_state(sh, ctx["now"], ctx["since"]),
                "last": None if m is None else {"text": m.text, "at": m.created_at.isoformat(),
                                                "who": users.get(m.user_id, ""), "agent": is_agent_msg(sh, m)},
                "last_at": (m.created_at if m else sh.created_at).isoformat(),
                "unread": ctx["unread"].get(sh.id, 0),
                "call_start": c.start_time.isoformat() if c else None, "agent_ext": c.agent_ext if c else None}

    def one_chat(s: Session, sh: CallShare, user: AppUser, messages: bool = False) -> dict:
        d = chat_dict(sh, chat_ctx(s, [sh], user), user)
        if messages:
            names = usernames(s)
            d["messages"] = [{"id": m.id, "text": m.text, "at": m.created_at.isoformat(), "who": names.get(m.user_id, ""),
                              "agent": is_agent_msg(sh, m), "mine": m.user_id == user.id}
                             for m in s.scalars(select(ShareMessage).where(ShareMessage.share_id == sh.id)
                                                .order_by(ShareMessage.id))]
        return d

    def get_chat(s: Session, share_id: int, user: AppUser, lock: bool = False) -> CallShare:
        """Staff: any chat that is not closed. Agent: only their own open chats (never archived / closed)."""
        sh = s.get(CallShare, share_id, with_for_update=lock)
        if sh is None or sh.status == "closed" or (not staff(user) and (sh.to_user_id != user.id or sh.status != "open")):
            raise HTTPException(404, "no such chat")
        return sh

    def usernames(s: Session) -> dict[int, str]:
        return dict(s.execute(select(AppUser.id, AppUser.username)).all())

    def targets(s: Session, c: Call) -> list[dict]:
        """Active agents of the call's team (teams.yaml)."""
        ext_team = cfg.teams().ext_team
        users = s.scalars(select(AppUser).where(AppUser.active.is_(True), AppUser.role == "agent")
                          .order_by(AppUser.username))
        return [{"id": u.id, "username": u.username, "ext": u.ext} for u in users
                if u.ext and c.team and ext_team.get(u.ext) == c.team]

    @app.get("/api/chats")
    def list_chats(user: User, s: DB,
                   agent: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None,
                   box: Annotated[str, Query(pattern=r"^(open|archive)$")] = "open",
                   from_: Annotated[date | None, Query(alias="from")] = None,
                   to: date | None = None) -> dict:
        """Open chats (newest activity first) or, staff only, the archive (من/إلى = the day the call was shared,
        Riyadh). Agents: only their own open chats. `unread` = chats with messages from the other side not read."""
        base = select(CallShare)
        if not staff(user):
            base = base.where(CallShare.to_user_id == user.id)
            if box == "archive":
                return {"items": [], "truncated": False, "open": 0, "unread": 0}
        elif agent:
            base = base.where(CallShare.to_user_id.in_(select(AppUser.id).where(AppUser.ext == agent)))
        stmt = base.where(CallShare.status == ("open" if box == "open" else "archived"))
        if box == "archive":
            if from_ and to and to < from_:
                raise HTTPException(422, "'to' is before 'from'")
            if from_:
                stmt = stmt.where(CallShare.created_at >= day_bounds(from_)[0])
            if to:
                stmt = stmt.where(CallShare.created_at < day_bounds(to)[1])
        stmt = stmt.order_by(CallShare.created_at.desc(), CallShare.id.desc()).limit(LIST_LIMIT + 1)
        rows = list(s.scalars(stmt))
        ctx = chat_ctx(s, rows[:LIST_LIMIT], user)
        items = [chat_dict(sh, ctx, user) for sh in rows[:LIST_LIMIT]]
        if box == "open":
            items.sort(key=lambda i: i["last_at"], reverse=True)
        return {"items": items, "truncated": len(rows) > LIST_LIMIT,
                "open": len(items) if box == "open" else 0, "unread": sum(1 for i in items if i["unread"])}

    @app.get("/api/chats/{share_id}")
    def read_chat(share_id: int, user: User, s: DB) -> dict:
        """The chat with its messages. Reading marks it read for this side (agent: the first read = "opened")."""
        sh = get_chat(s, share_id, user, lock=True)
        now = _now()
        # read up to the newest message that exists now (not "now": a message saved a moment ago but committed
        # after this read must still count as unread)
        newest = s.scalar(select(func.max(ShareMessage.created_at)).where(ShareMessage.share_id == sh.id))
        if staff(user):
            sh.staff_read_at = max(filter(None, (sh.staff_read_at, newest)), default=None)
        else:
            sh.agent_read_at = max(filter(None, (sh.agent_read_at, newest)), default=None)
            sh.seen_at = sh.seen_at or now
        s.commit()
        return one_chat(s, sh, user, messages=True)

    @app.post("/api/chats/{share_id}/messages")
    def chat_message(share_id: int, body: MessageIn, user: User, s: DB) -> dict:
        """Write in the chat. Agent: own open chat; the first message of at least REPLY_MIN letters is the 24 h
        reply (KPI). Staff: writing in an archived chat reopens it."""
        sh = get_chat(s, share_id, user, lock=True)
        c = s.get(Call, sh.call_id)
        if c is None or c.content_deleted_at is not None:
            raise HTTPException(409, "this call's content was deleted")
        text_ = body.text.strip()
        if not text_:
            raise HTTPException(422, "اكتب رسالة")
        now = _now()
        # the writer has read what their page showed, not "now" (see read_chat)
        newest = s.scalar(select(func.max(ShareMessage.created_at)).where(ShareMessage.share_id == sh.id))
        s.add(ShareMessage(share_id=sh.id, user_id=user.id, text=text_, created_at=now))
        if staff(user):
            sh.staff_read_at = max(filter(None, (sh.staff_read_at, newest)), default=None)
            if sh.status == "archived":
                sh.status, sh.archived_at = "open", None
                s.add(AdminLog(user_id=user.id, action="share_reopen", target=f"call:{sh.call_id} share:{sh.id}", at=now))
        else:
            sh.agent_read_at = max(filter(None, (sh.agent_read_at, newest)), default=None)
            sh.seen_at = sh.seen_at or now
            if sh.first_reply_at is None and len(text_) >= REPLY_MIN:
                sh.first_reply_at = now
                sh.reply, sh.reply_at = text_, now        # the old one-reply columns: the first counted reply
        s.add(AdminLog(user_id=user.id, action="share_message", target=f"call:{sh.call_id} share:{sh.id}", at=now))
        s.commit()                                         # never the text itself in the log
        return one_chat(s, sh, user, messages=True)

    @app.post("/api/chats/{share_id}/{action}")
    def chat_state(share_id: int, action: Annotated[str, Path(pattern=r"^(archive|close|reopen)$")],
                   user: Manager, s: DB) -> dict:
        """Staff: archive (open -> archive, can reopen), close (gone for both sides, still in the KPI), reopen."""
        sh = get_chat(s, share_id, user, lock=True)
        now = _now()
        if action == "archive":
            if sh.status != "open":
                raise HTTPException(409, "المحادثة ليست مفتوحة")
            sh.status, sh.archived_at = "archived", now
        elif action == "reopen":
            if sh.status != "archived":
                raise HTTPException(409, "المحادثة ليست في الأرشيف")
            sh.status, sh.archived_at = "open", None
            last_staff = s.scalar(select(func.max(ShareMessage.created_at)).where(
                ShareMessage.share_id == sh.id, ShareMessage.user_id != sh.to_user_id))
            if last_staff is not None and sh.agent_read_at is not None and sh.agent_read_at >= last_staff:
                sh.agent_read_at = last_staff - timedelta(microseconds=1)   # pops up for the agent again
        else:
            sh.status, sh.closed_at = "closed", now
        s.add(AdminLog(user_id=user.id, action=f"share_{action}", target=f"call:{sh.call_id} share:{sh.id}", at=now))
        s.commit()
        return one_chat(s, sh, user)

    @app.get("/api/calls/{call_id}/shares")
    def call_shares(call_id: int, user: User, s: DB) -> dict:
        """The chats of one call (closed ones are gone). Agents: only their own open ones."""
        c = owned_call(s, call_id, user)
        stmt = select(CallShare).where(CallShare.call_id == c.id, CallShare.status != "closed").order_by(CallShare.id)
        if not staff(user):
            stmt = stmt.where(CallShare.to_user_id == user.id, CallShare.status == "open")
        shares = list(s.scalars(stmt))
        ctx = chat_ctx(s, shares, user)
        return {"items": [chat_dict(sh, ctx, user) for sh in shares],
                "targets": targets(s, c) if staff(user) else []}

    @app.post("/api/calls/{call_id}/shares")
    def add_share(call_id: int, body: ShareIn, user: Manager, s: DB) -> dict:
        """Start a chat with an agent about this call; the note is its first message."""
        c = s.get(Call, call_id)
        if c is None:
            raise HTTPException(404, "no such call")
        if c.content_deleted_at is not None:
            raise HTTPException(409, "this call's content was deleted")
        if body.to_user_id not in {t["id"] for t in targets(s, c)}:
            raise HTTPException(422, "pick an active agent of this call's team")
        note = body.note.strip()
        if not note:
            raise HTTPException(422, "write a note")
        now = _now()
        sh = CallShare(call_id=c.id, from_user_id=user.id, to_user_id=body.to_user_id, note=note, created_at=now,
                       status="open", staff_read_at=now)
        s.add(sh)
        s.flush()
        s.add(ShareMessage(share_id=sh.id, user_id=user.id, text=note, created_at=now))
        s.add(AdminLog(user_id=user.id, action="share_call", target=f"call:{c.id} user:{body.to_user_id}", at=now))
        s.commit()
        return one_chat(s, sh, user, messages=True)

    @app.delete("/api/shares/{share_id}")
    def delete_share(share_id: int, user: Manager, s: DB) -> dict:
        sh = s.get(CallShare, share_id)
        if sh is None:
            raise HTTPException(404, "no such share")
        s.add(AdminLog(user_id=user.id, action="unshare_call", target=f"call:{sh.call_id} user:{sh.to_user_id}",
                       at=_now()))
        s.delete(sh)
        s.commit()
        return {"ok": True}
