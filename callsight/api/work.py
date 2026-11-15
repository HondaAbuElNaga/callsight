"""Agent + supervisor work routes (spec 2026-11-15-agent-supervisor): follow-up form, follow-ups list, shared calls,
the agent's summary of a call. Registered by create_app (main.py), which passes its dependency types.

No `from __future__ import annotations` here: the route signatures use the Annotated types passed in at runtime.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import followups as fu
from ..config import RIYADH
from ..db.models import Agent, AdminLog, AppUser, Call, CallFollowup, CallShare
from ..ingest.service import day_bounds
from . import auth
from .calls import visible_call, visible_scope

MAX_LIST_DAYS = 62
LIST_LIMIT = 500
STATES = ("", "action", "open", "solved", "todo", "callbacks", "open_callbacks", "came_back")
ACTION_LOOKBACK_DAYS = 62      # "needs action" + the counters ignore the من date (old unfinished work stays visible)
# most urgent first in "needs action"
URGENCY = {"callback_missed": 0, "note_needed": 1, "overdue": 2, "waiting_callback": 3, "required": 4}


class FollowupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_followup: StrictBool = False
    what_happened: str | None = Field(default=None, max_length=500)
    solved: StrictBool | None = None
    responsible_kind: str | None = Field(default=None, max_length=20)
    responsible_user_id: StrictInt | None = None
    expected_at: datetime | None = None
    callback_at: datetime | None = None


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
        return {"needed": True, "enabled": True, **d, "can_edit": may_edit(row.form, c, user, now) is None,
                "lock_reason": may_edit(row.form, c, user, now), "options": fu.responsible_options(s)}

    def may_edit(form, c: Call, user: AppUser, now: datetime) -> str | None:
        """None = may save. Agents: own call, before the form deadline, and never once the callback time passed."""
        if staff(user):
            return None
        if c.agent_ext != user.ext:
            return "not your call"
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
        why = may_edit(form, c, user, now)        # a late FIRST form is allowed (it counts as late)
        if why:
            raise HTTPException(403, why)
        try:
            fu.save(s, c, body.model_dump(), user, now)
        except fu.FormError as exc:
            s.rollback()
            raise HTTPException(422, str(exc)) from None
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

    # ---------- follow-ups list ----------

    def action_due(i: dict, today: date) -> bool:
        if i["step"] in fu.ACTION_STEPS:
            return True
        cb = i["callback"]
        return (i["step"] == "waiting_callback" and cb is not None
                and datetime.fromisoformat(cb["at"]).astimezone(RIYADH).date() <= today)

    def counts(items: list[dict], today: date) -> dict:
        def today_cb(i):
            cb = i["callback"]
            return (i["step"] == "waiting_callback" and cb is not None
                    and datetime.fromisoformat(cb["at"]).astimezone(RIYADH).date() == today)
        return {"forms": sum(i["step"] in ("required", "overdue") for i in items),
                "notes": sum(i["step"] == "note_needed" for i in items),
                "callbacks_today": sum(today_cb(i) for i in items),
                "missed": sum(i["step"] == "callback_missed" for i in items)}

    @app.get("/api/followups")
    def list_followups(
        user: User, s: DB,
        date_from: Annotated[date | None, Query(alias="from")] = None,
        date_to: Annotated[date | None, Query(alias="to")] = None,
        agent: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None,
        state: Annotated[str, Query()] = "action",
    ) -> dict:
        if state not in STATES:
            raise HTTPException(422, "unknown state")
        today = datetime.now(RIYADH).date()
        d_to = date_to or today
        d_from = date_from or (d_to - timedelta(days=6))
        if d_to < d_from or (d_to - d_from).days > MAX_LIST_DAYS:
            raise HTTPException(422, "bad date range")
        st = fu.settings(s)
        out = {"settings": {k: v.isoformat() if v else None for k, v in st.items()}, "items": [],
               "linked": staff(user) or bool(user.ext and user.ext in team_exts()),
               "counts": {"forms": 0, "notes": 0, "callbacks_today": 0, "missed": 0}}
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
        recent = load(today - timedelta(days=ACTION_LOOKBACK_DAYS), today)
        out["counts"] = counts(recent, today)
        if state == "action":
            items = sorted((i for i in recent if action_due(i, today)),
                           key=lambda i: (URGENCY.get(i["step"], 9), i["start_time"]))
        else:
            items = load(d_from, d_to)
        if state == "open":
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
        out["items"] = items[:LIST_LIMIT]
        out["truncated"] = len(items) > LIST_LIMIT
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

    def share_dict(s: Session, sh: CallShare, users: dict[int, str]) -> dict:
        c = s.get(Call, sh.call_id)
        return {"id": sh.id, "call_id": sh.call_id, "note": sh.note, "from": users.get(sh.from_user_id, ""),
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
    def list_shares(user: User, s: DB) -> dict:
        stmt = select(CallShare).order_by(CallShare.created_at.desc(), CallShare.id.desc()).limit(LIST_LIMIT)
        if not staff(user):
            stmt = stmt.where(CallShare.to_user_id == user.id)
        names = usernames(s)
        items = [share_dict(s, sh, names) for sh in s.scalars(stmt)]
        return {"items": items, "unseen": sum(1 for i in items if i["seen_at"] is None)}

    @app.get("/api/calls/{call_id}/shares")
    def call_shares(call_id: int, user: User, s: DB) -> dict:
        c = owned_call(s, call_id, user)
        stmt = select(CallShare).where(CallShare.call_id == c.id).order_by(CallShare.id)
        if not staff(user):
            stmt = stmt.where(CallShare.to_user_id == user.id)
        shares = list(s.scalars(stmt))
        if not staff(user) and any(sh.seen_at is None for sh in shares):
            for sh in shares:          # the agent opened the call: the supervisor's note is seen (fix G)
                sh.seen_at = sh.seen_at or _now()
            s.commit()
        names = usernames(s)
        return {"items": [share_dict(s, sh, names) for sh in shares],
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
        return share_dict(s, sh, usernames(s))

    @app.post("/api/shares/{share_id}/seen")
    def share_seen(share_id: int, user: User, s: DB) -> dict:
        sh = s.get(CallShare, share_id)
        if sh is None or sh.to_user_id != user.id:
            raise HTTPException(404, "no such share")
        if sh.seen_at is None:
            sh.seen_at = _now()
            s.commit()
        return share_dict(s, sh, usernames(s))

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
