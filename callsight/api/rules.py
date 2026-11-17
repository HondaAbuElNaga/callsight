"""Rules page (spec 2026-11-17-rules-editor): word lists and topics. Admin edits, supervisor reads.

Every edit writes a rule_change row (who, when, added / removed, reason; never deleted) and one admin_log row.
The nlp worker picks the change up within one minute (rulestore.cached). API keys (other systems) never edit:
only a person, logged in.
No `from __future__ import annotations`: the route signatures use Annotated types passed in at runtime.
"""
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis import rulestore as rs
from ..db.models import AdminLog, AppUser, RuleChange

HISTORY_LIMIT = 200
RECHECK_DAYS = 7
RECHECK_GAP = timedelta(minutes=5)       # one re-check at a time is plenty (the worker runs them one by one)


class WordsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    add: list[str] = Field(default_factory=list, max_length=rs.MAX_ITEMS)
    remove: list[str] = Field(default_factory=list, max_length=rs.MAX_ITEMS)
    reason: str = Field(max_length=300)


class TopicsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    set: dict[str, str] = Field(default_factory=dict, max_length=rs.MAX_TOPICS)
    remove: list[str] = Field(default_factory=list, max_length=rs.MAX_TOPICS)
    reason: str = Field(max_length=300)


class SentenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(app: FastAPI, *, DB, Manager, Admin, cfg) -> None:

    def by_hand(request: Request) -> None:
        if getattr(request.state, "api_key", False):
            raise HTTPException(403, "the rules are edited on the CallSight page only")

    def page(s: Session, user: AppUser) -> dict:
        rules, topics = rs.current(s)
        cr = rules.competitor_rule
        lists = {**{k: list(rules.flags.get(k, ())) for k in rs.FLAG_KINDS},
                 rs.OWN: list(cr.own) if cr else [], rs.IGNORE: list(cr.ignore_next) if cr else []}
        return {
            "can_edit": user.role == "admin",
            "lists": lists,
            "topics": [[k, v] for k, v in topics.items()],
            "competitor_word": cr.word if cr else None,
            "competitor_label": cr.label if cr else None,
            "limits": {"items": rs.MAX_ITEMS, "letters": rs.MAX_LETTERS, "topics": rs.MAX_TOPICS,
                       "label": rs.MAX_LABEL},
            "version": rules.version,
            "recheck_days": RECHECK_DAYS,
            "recheck_enabled": cfg.rules_recheck is not None,
        }

    def fail(s: Session, exc: Exception) -> HTTPException:
        s.rollback()
        return HTTPException(422, str(exc))

    @app.get("/api/rules")
    def get_rules(user: Manager, s: DB) -> dict:
        try:
            if rs.ensure_seeded(s):                  # first open on a DB the migration could not fill
                s.commit()
        except (rs.RuleError, ValueError, OSError):
            s.rollback()
        return page(s, user)

    @app.get("/api/rules/history")
    def history(user: Manager, s: DB) -> list[dict]:
        names = dict(s.execute(select(AppUser.id, AppUser.username)).all())
        rows = s.scalars(select(RuleChange).order_by(RuleChange.at.desc(), RuleChange.id.desc())
                         .limit(HISTORY_LIMIT))
        return [{"id": r.id, "list": r.list_name, "user": names.get(r.user_id, "") if r.user_id else None,
                 "added": r.added, "removed": r.removed, "reason": r.reason, "at": r.at.isoformat()} for r in rows]

    @app.post("/api/rules/lists/{name}")
    def change_list(name: str, body: WordsIn, request: Request, user: Admin, s: DB) -> dict:
        by_hand(request)
        if name not in rs.WORD_LISTS:
            raise HTTPException(404, "no such list")
        try:
            items = rs.change_words(s, name, user.id, body.add, body.remove, body.reason)
        except rs.RuleError as exc:
            raise fail(s, exc) from None
        s.add(AdminLog(user_id=user.id, action="rules_change",
                       target=f"{name} +{len(body.add)} -{len(body.remove)}", reason=body.reason.strip()[:300],
                       at=_now()))
        s.commit()
        return {"name": name, "items": items}

    @app.post("/api/rules/topics")
    def change_topics(body: TopicsIn, request: Request, user: Admin, s: DB) -> dict:
        by_hand(request)
        try:
            items = rs.change_topics(s, user.id, body.set, body.remove, body.reason)
        except rs.RuleError as exc:
            raise fail(s, exc) from None
        s.add(AdminLog(user_id=user.id, action="rules_change",
                       target=f"topics set {len(body.set)} -{len(body.remove)}", reason=body.reason.strip()[:300],
                       at=_now()))
        s.commit()
        return {"name": rs.TOPICS, "items": items}

    @app.post("/api/rules/test")
    def test_sentence(body: SentenceIn, user: Manager, s: DB) -> dict:
        rules, _ = rs.current(s)
        return {"hits": rs.match_sentence(body.text, rules)}     # the sentence is never stored or logged

    @app.post("/api/rules/recheck")
    def recheck(request: Request, user: Admin, s: DB) -> dict:
        by_hand(request)
        if cfg.rules_recheck is None:
            raise HTTPException(503, "the worker is not connected")
        last = s.scalar(select(AdminLog.at).where(AdminLog.action == "rules_recheck")
                        .order_by(AdminLog.at.desc()).limit(1))
        if last is not None and _now() - last < RECHECK_GAP:
            raise HTTPException(429, "a re-check was started a few minutes ago; wait 5 minutes")
        s.add(AdminLog(user_id=user.id, action="rules_recheck", target=f"last {RECHECK_DAYS} days", at=_now()))
        s.commit()
        try:
            cfg.rules_recheck(RECHECK_DAYS)
        except Exception:  # noqa: BLE001 - the log row stays; the admin may try again after the gap
            raise HTTPException(503, "the re-check could not be queued; try again in 5 minutes") from None
        return {"queued": True, "days": RECHECK_DAYS}

