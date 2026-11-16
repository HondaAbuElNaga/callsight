"""Agent + supervisor work routes (spec 2026-11-15-agent-supervisor): follow-up form, follow-ups list, shared calls,
the agent's summary of a call. Registered by create_app (main.py), which passes its dependency types.

No `from __future__ import annotations` here: the route signatures use the Annotated types passed in at runtime.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import followups as fu
from ..config import RIYADH
from ..db.models import Agent, AdminLog, AppUser, Call, CallFollowup, CallFollowupNote, CallPrivateNote, CallShare
from ..ingest.service import day_bounds
from . import auth
from .calls import visible_call, visible_scope

MAX_LIST_DAYS = 62
LIST_LIMIT = 500
STATES = ("", "action", "forms", "notes", "callbacks_today", "missed", "open", "solved", "todo", "callbacks",
          "open_callbacks", "came_back", "no_followup")
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


class MyNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=300)


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=1000)


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
                "forward_options": forward.options(s) if on else []}

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
        if not fu.forward_failed(form) and \
                s.scalar(select(CallFollowupNote.id).where(CallFollowupNote.call_id == c.id).limit(1)) is not None:
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

    def action_due(i: dict, today: date) -> bool:
        if i["step"] in fu.ACTION_STEPS:
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
               "counts": {"forms": 0, "notes": 0, "callbacks_today": 0, "missed": 0, "no_followup": 0},
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
        if priority and state != "no_followup":   # those forms have no priority: the filter would empty them
            items = [i for i in items if i["form"] and i["form"]["priority"] == priority]
        out["older_action"] = sum(1 for i in recent if datetime.fromisoformat(i["start_time"]) < lo
                                  and action_due(i, today))
        if state == "action":
            items = sorted((i for i in items if action_due(i, today)),
                           key=lambda i: (URGENCY.get(i["step"], 9), i["start_time"]))
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
            items = [i for i in items if i["step"] in ("solved_first", "solved_after")]
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
        # "تحتاج حل" (requirement 11): every open problem of these agents, never limited by من/إلى or the state
        # filter; only "who" (agent / الموظف filter) and the priority filter apply
        got, out["unsolved_truncated"] = fu.unsolved(s, exts, start, now, callers=team_exts(), priority=priority,
                                                      limit=LIST_LIMIT)
        unsolved = [r.as_dict(now, names, users) for r in got]
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
        topics, pv = cfg_analysis()
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

    def cfg_analysis() -> tuple[dict, str]:
        import os
        from pathlib import Path

        from ..analysis.llm import prompt_version
        from ..analysis.settings import load_topics
        from ..config import ROOT
        topics = cfg.topics() if cfg.topics else load_topics(Path(os.getenv("TOPICS_FILE",
                                                                            str(ROOT / "config" / "topics.yaml"))))
        return topics, prompt_version(topics)

    # ---------- shared calls (decision 10: one-way note, agent marks seen) ----------

    def share_dict(s: Session, sh: CallShare, users: dict[int, str], user: AppUser) -> dict:
        """An agent gets the note text only after clicking it (seen_at): until then `note` is None."""
        c = s.get(Call, sh.call_id)
        hidden = not staff(user) and sh.seen_at is None
        return {"id": sh.id, "call_id": sh.call_id, "note": None if hidden else sh.note,
                "from": users.get(sh.from_user_id, ""),
                "reply": sh.reply, "reply_at": sh.reply_at.isoformat() if sh.reply_at else None,
                "to": users.get(sh.to_user_id, ""), "to_user_id": sh.to_user_id,
                "created_at": sh.created_at.isoformat(), "seen_at": sh.seen_at.isoformat() if sh.seen_at else None,
                "call_start": c.start_time.isoformat() if c else None, "agent_ext": c.agent_ext if c else None}

    def usernames(s: Session) -> dict[int, str]:
        return dict(s.execute(select(AppUser.id, AppUser.username)).all())

    def targets(s: Session, c: Call) -> list[dict]:
        """Active agents of the call's team (teams.yaml)."""
        ext_team = cfg.teams().ext_team
        users = s.scalars(select(AppUser).where(AppUser.active.is_(True), AppUser.role == "agent")
                          .order_by(AppUser.username))
        return [{"id": u.id, "username": u.username, "ext": u.ext} for u in users
                if u.ext and c.team and ext_team.get(u.ext) == c.team]

    @app.get("/api/shares")
    def list_shares(user: User, s: DB,
                    agent: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None) -> dict:
        stmt = select(CallShare).order_by(CallShare.created_at.desc(), CallShare.id.desc()).limit(LIST_LIMIT)
        if not staff(user):
            stmt = stmt.where(CallShare.to_user_id == user.id)
        elif agent:     # staff picked one agent on the follow-ups page: only the calls shared with that agent
            stmt = stmt.where(CallShare.to_user_id.in_(select(AppUser.id).where(AppUser.ext == agent)))
        names = usernames(s)
        items = [share_dict(s, sh, names, user) for sh in s.scalars(stmt)]
        return {"items": items, "unseen": sum(1 for i in items if i["seen_at"] is None)}

    @app.get("/api/calls/{call_id}/shares")
    def call_shares(call_id: int, user: User, s: DB) -> dict:
        c = owned_call(s, call_id, user)
        stmt = select(CallShare).where(CallShare.call_id == c.id).order_by(CallShare.id)
        if not staff(user):
            stmt = stmt.where(CallShare.to_user_id == user.id)
        shares = list(s.scalars(stmt))      # opening the call does NOT mark the note seen: clicking it does
        names = usernames(s)
        return {"items": [share_dict(s, sh, names, user) for sh in shares],
                "targets": targets(s, c) if staff(user) else []}

    @app.post("/api/calls/{call_id}/shares")
    def add_share(call_id: int, body: ShareIn, user: Manager, s: DB) -> dict:
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
        sh = CallShare(call_id=c.id, from_user_id=user.id, to_user_id=body.to_user_id, note=note, created_at=_now())
        s.add(sh)
        s.add(AdminLog(user_id=user.id, action="share_call", target=f"call:{c.id} user:{body.to_user_id}", at=_now()))
        s.commit()
        return share_dict(s, sh, usernames(s), user)

    @app.post("/api/shares/{share_id}/seen")
    def share_seen(share_id: int, user: User, s: DB) -> dict:
        sh = s.get(CallShare, share_id)
        if sh is None or sh.to_user_id != user.id:
            raise HTTPException(404, "no such share")
        if sh.seen_at is None:
            sh.seen_at = _now()
            s.commit()
        return share_dict(s, sh, usernames(s), user)

    @app.put("/api/shares/{share_id}/reply")
    def share_reply(share_id: int, body: ReplyIn, user: User, s: DB) -> dict:
        """The agent answers the supervisor (one reply, may be changed; empty text removes it). Only after the note
        was opened, and never on a call whose content was deleted."""
        sh = s.get(CallShare, share_id)
        if sh is None or sh.to_user_id != user.id:
            raise HTTPException(404, "no such share")
        if sh.seen_at is None:
            raise HTTPException(409, "open the note first")
        c = s.get(Call, sh.call_id)
        if c is None or c.content_deleted_at is not None:
            raise HTTPException(409, "this call's content was deleted")
        text_ = body.text.strip()
        sh.reply, sh.reply_at = (text_, _now()) if text_ else (None, None)
        s.add(AdminLog(user_id=user.id, action="share_reply" if text_ else "share_reply_removed",
                       target=f"call:{sh.call_id} share:{sh.id}", at=_now()))      # never the text itself
        s.commit()
        return share_dict(s, sh, usernames(s), user)

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
