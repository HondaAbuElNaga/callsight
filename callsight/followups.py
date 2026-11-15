"""Call follow-ups (spec 2026-11-15-agent-supervisor): which calls need a form, the form deadline, the callback check
and the follow-up KPIs. One place for these rules: the API lists, the form, the KPI page and the PDF all use it.

Decisions (spec): a form is needed for answered inbound team calls with talk > 20 s from `followup_start` on (3);
form on time = by 23:00 Riyadh on the call's day, or 2 h after the call end when that is later (11); a not-solved
form's callback is done by the first saved outbound call to the same number (last 9 digits) after the call ended,
by any team member, credited to the form's owner (2), when it was answered or lasted >= 10 s (5); on time =
callback time + 60 min (4). "Came back" = an inbound call from the same number <= 72 h after a call marked solved
(show-only). Calls whose content an admin erased are left out.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import exists, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from .config import RIYADH
from .db.models import AppSetting, AppUser, Call, CallFollowup, norm_number
from .ingest.service import day_bounds

MIN_TALK_SECONDS = 20          # talk must be MORE than this
GRACE = timedelta(minutes=60)
RANG_SECONDS = 10              # an unanswered callback attempt counts from this total time
DAY_END_HOUR = 23
LATE_CALL_EXTRA = timedelta(hours=2)
MAX_CALLBACK_DAYS = 14
CAME_BACK = timedelta(hours=72)
SETTING_START = "followup_start"
SETTING_KPI = "followup_kpi_since"
SETTINGS = (SETTING_START, SETTING_KPI)
RESPONSIBLE_KINDS = ("user", "customer", "other_department")
CALLBACK_STATES = ("waiting", "on_time", "late", "missed", "no_number")


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
    end = call.end_time.astimezone(RIYADH)
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
                   or_(o.status == "answered", o.total_seconds >= RANG_SECONDS))
            .order_by(o.start_time, o.id).limit(1).correlate(Call).lateral("cb"))


def _came_back():
    i = aliased(Call)
    return exists().where(i.direction == "inbound", i.id != Call.id, i.start_time > Call.end_time,
                          norm_number(Call.customer_number) != "",
                          i.start_time <= Call.end_time + CAME_BACK,
                          norm_number(i.customer_number) == norm_number(Call.customer_number))


@dataclass
class Row:
    call: Call
    form: CallFollowup | None
    cb_id: int | None
    cb_time: datetime | None
    cb_ext: str | None
    came_back: bool

    def as_dict(self, now: datetime, names: dict[str, str] | None = None) -> dict:
        c, f = self.call, self.form
        cb_state = callback_state(f.callback_at if f and not f.no_followup and f.solved is False else None,
                                  _has_number(c.customer_number), self.cb_time, now)
        return {
            "call_id": c.id, "start_time": c.start_time.isoformat(), "end_time": c.end_time.isoformat(),
            "customer_number": c.customer_number, "agent_ext": c.agent_ext,
            "agent_name": (names or {}).get(c.agent_ext or "", ""), "talk_seconds": c.talk_seconds,
            "deadline": form_deadline(c).isoformat(), "form_state": form_state(c, f, now),
            "form": form_dict(f) if f else None,
            "callback": None if cb_state is None else {
                "at": f.callback_at.isoformat(), "due": (f.callback_at + GRACE).isoformat(), "status": cb_state,
                "by_ext": self.cb_ext, "call_id": self.cb_id,
                "time": self.cb_time.isoformat() if self.cb_time else None},
            "came_back": bool(self.came_back and f and f.solved is True),
        }


def form_dict(f: CallFollowup) -> dict:
    return {"no_followup": f.no_followup, "what_happened": f.what_happened, "solved": f.solved,
            "responsible_kind": f.responsible_kind, "responsible_user_id": f.responsible_user_id,
            "expected_at": f.expected_at.isoformat() if f.expected_at else None,
            "callback_at": f.callback_at.isoformat() if f.callback_at else None,
            "first_filled_at": f.first_filled_at.isoformat(), "updated_at": f.updated_at.isoformat()}


def rows(s: Session, exts, start: date, lo: datetime, hi: datetime, call_ids: list[int] | None = None, *,
         callers) -> list[Row]:
    """Needed calls in [lo, hi) for these owner extensions, with form, first callback and came-back flag.
    callers = every team extension (teams.yaml): only their outbound calls count as a callback (decision 2)."""
    cb = _callback_lateral(callers)
    stmt = (select(Call, CallFollowup, cb.c.cb_id, cb.c.cb_time, cb.c.cb_ext, _came_back().label("came_back"))
            .outerjoin(CallFollowup, CallFollowup.call_id == Call.id)
            .outerjoin(cb, true())
            .where(*needed_conditions(exts, start), Call.start_time >= lo, Call.start_time < hi))
    if call_ids is not None:
        stmt = stmt.where(Call.id.in_(call_ids))
    stmt = stmt.order_by(Call.start_time.desc(), Call.id.desc())
    return [Row(c, f, a, b, e, bool(cb_)) for c, f, a, b, e, cb_ in s.execute(stmt).all()]


def one(s: Session, call: Call, exts, start: date | None) -> Row | None:
    """exts = every team extension (owners and callers alike)."""
    if not is_needed(call, exts, start):
        return None
    got = rows(s, exts, start, call.start_time, call.start_time + timedelta(microseconds=1), [call.id], callers=exts)
    return got[0] if got else None


def responsible_options(s: Session) -> list[dict]:
    users = s.scalars(select(AppUser).where(AppUser.active.is_(True), AppUser.role.in_(("supervisor", "agent")))
                      .order_by(AppUser.role.desc(), AppUser.username))
    return [{"id": u.id, "username": u.username, "role": u.role, "ext": u.ext} for u in users]


# ---------- KPIs ----------

def _empty() -> dict:
    return {"forms_due": 0, "forms_on_time": 0, "forms_rate": None, "callbacks_due": 0, "callbacks_on_time": 0,
            "callbacks_rate": None, "came_back": 0}


def kpi(s: Session, lo: datetime, hi: datetime, exts: list[str], now: datetime | None = None, *,
        callers) -> dict:
    """Follow-up KPIs of [lo, hi), counted in the period of the original call. `active` = the admin switched them
    on (followup_kpi_since): until then they are show-only (page, never PDF). Counting starts at the later of
    followup_start and followup_kpi_since (when set)."""
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
        d = r.as_dict(now)
        for bucket in (out["summary"], out["agents"].setdefault(r.call.agent_ext, _empty())):
            if d["form_state"] != "missing":          # deadline passed, or already filled
                bucket["forms_due"] += 1
                bucket["forms_on_time"] += d["form_state"] == "on_time"
            cb = d["callback"]
            if cb and cb["status"] in ("on_time", "late", "missed"):
                bucket["callbacks_due"] += 1
                bucket["callbacks_on_time"] += cb["status"] == "on_time"
            bucket["came_back"] += d["came_back"]
    for bucket in (out["summary"], *out["agents"].values()):
        bucket["forms_rate"] = bucket["forms_on_time"] / bucket["forms_due"] if bucket["forms_due"] else None
        bucket["callbacks_rate"] = (bucket["callbacks_on_time"] / bucket["callbacks_due"]
                                    if bucket["callbacks_due"] else None)
    return out


# ---------- form validation ----------

class FormError(ValueError):
    pass


def validate(s: Session, data: dict, call: Call, now: datetime) -> dict:
    """Server-side checks of a submitted form; returns the clean fields."""
    if data.get("no_followup"):
        return {"no_followup": True, "what_happened": None, "solved": None, "responsible_kind": None,
                "responsible_user_id": None, "expected_at": None, "callback_at": None}
    text = (data.get("what_happened") or "").strip()
    if not text:
        raise FormError("اكتب ما حدث في المكالمة")
    if len(text) > 500:
        raise FormError("النص أطول من 500 حرف")
    solved = data.get("solved")
    if solved not in (True, False):
        raise FormError("اختر: هل حُلّت المشكلة؟")
    clean = {"no_followup": False, "what_happened": text, "solved": solved, "responsible_kind": None,
             "responsible_user_id": None, "expected_at": None, "callback_at": None}
    if solved:
        return clean
    kind = data.get("responsible_kind")
    if kind not in RESPONSIBLE_KINDS:
        raise FormError("اختر المسؤول عن الحل")
    uid = data.get("responsible_user_id")
    if kind == "user":
        u = s.get(AppUser, uid) if isinstance(uid, int) else None
        if u is None or not u.active or u.role not in ("supervisor", "agent"):
            raise FormError("المسؤول غير موجود")
        clean["responsible_user_id"] = u.id
    expected, callback = data.get("expected_at"), data.get("callback_at")
    if not isinstance(expected, datetime) or not isinstance(callback, datetime):
        raise FormError("حدد وقت الحل المتوقع ووقت معاودة الاتصال")
    if expected.tzinfo is None or callback.tzinfo is None:
        raise FormError("الوقت يحتاج المنطقة الزمنية")
    if callback <= now:
        raise FormError("وقت معاودة الاتصال يجب أن يكون في المستقبل")
    if callback > now + timedelta(days=MAX_CALLBACK_DAYS) or expected > now + timedelta(days=60):
        raise FormError(f"وقت معاودة الاتصال خلال {MAX_CALLBACK_DAYS} يومًا على الأكثر")
    if expected < call.start_time:
        raise FormError("وقت الحل المتوقع قبل المكالمة")
    clean.update(responsible_kind=kind, expected_at=expected, callback_at=callback)
    return clean


def save(s: Session, call: Call, data: dict, user: AppUser, now: datetime | None = None) -> CallFollowup:
    now = now or _utcnow()
    f = s.scalars(select(CallFollowup).where(CallFollowup.call_id == call.id)).first()
    clean =validate(s, data, call, now) if not _same_callback(f, data) else _validate_keep(s, data, call, now, f)
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


def _same_callback(f: CallFollowup | None, data: dict) -> bool:
    return (f is not None and f.callback_at is not None and not data.get("no_followup")
            and data.get("solved") is False and data.get("callback_at") == f.callback_at)


def _validate_keep(s: Session, data: dict, call: Call, now: datetime, f: CallFollowup) -> dict:
    """Editing a form without moving its callback time: the old time may be in the past by now (still valid)."""
    return validate(s, data, call, min(now, f.callback_at - timedelta(seconds=1)))
