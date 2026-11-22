"""Shared calls (spec 2026-11-22-shares-tab): the 24-hour reply rule and its KPI.

A supervisor shares a call with an agent; the agent must reply within REPLY_HOURS of the share being sent
(created_at). first_reply_at is the time of the first reply: an edit within the 24 h keeps it, an edit after them
moves it to the edit (late), removing the reply clears it.
Only shares sent on/after the app_setting `share_kpi_since` (written by migration 0026) count in the KPI.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import AppSetting, AppUser, CallShare
from .config import RIYADH

REPLY_HOURS = 24
SETTING_SINCE = "share_kpi_since"


def deadline(sh: CallShare) -> datetime:
    return sh.created_at + timedelta(hours=REPLY_HOURS)


def reply_state(sh: CallShare, now: datetime, since: datetime | None = None) -> str:
    """on_time / late (replied after 24 h) / missing (no reply and 24 h passed) / pending (still has time) /
    old (sent before the 24 h rule existed: `since`, never counted)."""
    if since is not None and sh.created_at < since:
        return "old"
    if sh.first_reply_at is not None:
        return "on_time" if sh.first_reply_at <= deadline(sh) else "late"
    return "missing" if now > deadline(sh) else "pending"


def rule_start(s: Session) -> datetime | None:
    row = s.get(AppSetting, SETTING_SINCE)
    if row is None or not row.value:
        return None
    try:
        d = datetime.fromisoformat(row.value)    # the moment migration 0026 ran, or a plain day (Riyadh)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=RIYADH)


def _empty() -> dict:
    return {"due": 0, "on_time": 0, "late": 0, "missing": 0, "rate": None}


def kpi(s: Session, lo: datetime, hi: datetime, exts: list[str], now: datetime | None = None) -> dict:
    """Per agent extension: shares sent in [lo, hi) whose 24 h ended or that were answered. rate = on_time / due."""
    now = now or datetime.now(timezone.utc)
    out = {"since": None, "hours": REPLY_HOURS, "summary": _empty(), "agents": {e: _empty() for e in exts}}
    if not exts:
        return out
    since = rule_start(s)
    out["since"] = since.astimezone(RIYADH).date().isoformat() if since else None
    lo = max(lo, since) if since else lo
    stmt = (select(CallShare, AppUser.ext).join(AppUser, AppUser.id == CallShare.to_user_id)
            .where(CallShare.created_at >= lo, CallShare.created_at < hi, AppUser.ext.in_(exts)))
    for sh, ext in s.execute(stmt):
        st = reply_state(sh, now)
        if st == "pending":
            continue
        for b in (out["summary"], out["agents"][ext]):
            b["due"] += 1
            b[st] += 1
    for b in (out["summary"], *out["agents"].values()):
        b["rate"] = b["on_time"] / b["due"] if b["due"] else None
    return out
