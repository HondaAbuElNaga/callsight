"""Forward a not-solved problem to a branch supervisor in the customer-service system (spec
2026-11-16-priority-no-followup, part 4 D1): the supervisors list (config/forward_people.yaml + sync with the other
system) and the send job (customer by phone -> ticket). The form side (validation, steps) is in followups.py.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .config import RIYADH, ROOT
from .cs_api import CsAuthError, CsClient, CsError, CsUnavailable, supervisors
from .db.models import Agent, Call, CallFollowup, ForwardPerson

log = logging.getLogger(__name__)

SEND_WINDOW = timedelta(hours=24)        # retried every 5 min for 24 h after the form, then "failed"
SEND_BATCH = 50
NOTES_MAX = 590                          # the other system's limit on `notes`
CS_PRIORITY = {"high": 3, "medium": 2, "low": 1}    # their 1 low / 2 normal / 3 high / 4 urgent (4 not used)
PRIORITY_AR = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
ERR_UNAVAILABLE = "النظام الآخر لا يرد، نحاول مرة أخرى"
ERR_NO_GUID = "المسؤول غير مربوط بالنظام الآخر بعد"
ERR_EXPIRED = "تعذّر الإرسال خلال 24 ساعة"
ERR_AUTH = "مفتاح الربط مرفوض، أبلغ المدير"
# the ticket request got no clear answer: it may exist there, so it is never sent again by itself (review D1)
ERR_UNSURE = "لم يتأكد إنشاء التذكرة: تحقق في نظام خدمة العملاء قبل الإعادة"


def people_file() -> Path:
    return Path(os.getenv("FORWARD_PEOPLE_FILE", str(ROOT / "config" / "forward_people.yaml")))


def load_people_yaml(path: Path) -> list[dict]:
    """[{branch, name, ext, guid}] from the yaml; a broken line is an error (never a silent half list)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out, seen = [], set()
    for i, p in enumerate(data.get("people") or [], 1):
        branch, name = str((p or {}).get("branch") or "").strip(), str((p or {}).get("name") or "").strip()
        if not branch or not name:
            raise ValueError(f"{path}: person {i} needs branch and name")
        if (branch, name) in seen:
            raise ValueError(f"{path}: {branch} / {name} is listed twice")
        seen.add((branch, name))
        ext = str(p.get("ext")).strip() if p.get("ext") not in (None, "") else None
        if ext is not None and not re.fullmatch(r"\d{1,6}", ext):
            raise ValueError(f"{path}: bad ext {ext!r} for {name}")
        guid = str(p.get("guid")).strip() if p.get("guid") else None
        out.append({"branch": branch, "name": name, "ext": ext, "guid": guid})
    return out


# ---------- name matching (yaml short name -> the other system's full name) ----------

_DIACRITICS = re.compile(r"[ً-ٰٟـ]")      # harakat + tatweel
_GENERIC = {"فرع", "رجال", "نساء", "حي", "ال"}


def norm_ar(text: str) -> str:
    t = _DIACRITICS.sub("", text or "")
    t = re.sub("[أإآ]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    t = re.sub(r"\bعبد\s+", "عبد", t)          # "عبد المنعم" == "عبدالمنعم"
    t = re.sub(r"\bابو\s+", "ابو", t)
    return " ".join(t.split())


def _tokens(text: str) -> list[str]:
    return norm_ar(text).split()


def _branch_words(text: str) -> set[str]:
    return {w[2:] if w.startswith("ال") and len(w) > 3 else w for w in _tokens(text)} - _GENERIC


def match_person(p: dict, sups: list[dict]) -> tuple[dict | None, str]:
    """The supervisor whose name holds every word of the yaml name; several -> the one whose branch shares a word
    with the yaml branch. (match, why-not) — never a guess between two people."""
    want = _tokens(p["name"])
    named = [x for x in sups if want and all(w in _tokens(x["name"]) for w in want)]
    hits = named
    if len(named) > 1:
        bw = _branch_words(p["branch"])
        hits = [x for x in named if bw & _branch_words(x["branch"])]
    if len(hits) == 1:
        return hits[0], ""
    return None, "not found" if not named else f"{len(named)} people match"


def sync_people(s: Session, client: CsClient | None, path: Path | None = None,
                now: datetime | None = None) -> list[str]:
    """Yaml -> forward_person (add / update ext, inactive when removed). With a client: fill each guid from the
    other system's supervisors (a yaml guid wins) and make people who are not supervisors there inactive.
    Returns report lines (for the CLI / log). Commits."""
    now = now or datetime.now(timezone.utc)
    people = load_people_yaml(path or people_file())
    rows = {(r.branch, r.name): r for r in s.scalars(select(ForwardPerson))}
    sups = supervisors(client.forward_targets()) if client is not None else None
    by_guid = {x["guid"]: x for x in sups or []}
    report, keep = [], set()
    for p in people:
        key = (p["branch"], p["name"])
        keep.add(key)
        r = rows.get(key)
        if r is None:
            r = ForwardPerson(branch=p["branch"], name=p["name"], active=True)
            s.add(r)
            rows[key] = r
        r.ext = p["ext"]
        if sups is None:
            if p["guid"]:
                r.guid = p["guid"]
            r.active = True
            continue
        hit, why = (by_guid.get(p["guid"]), "not a supervisor there") if p["guid"] else match_person(p, sups)
        if hit is None:
            r.active = False
            report.append(f"NOT LINKED {p['branch']} / {p['name']}: {why}")
        else:
            r.guid, r.full_name, r.active = hit["guid"], hit["name"][:200], True
            report.append(f"ok {p['branch']} / {p['name']} -> {hit['name']} ({hit['branch']})")
        r.synced_at = now
    for key, r in rows.items():
        if key not in keep and r.active:
            r.active = False
            report.append(f"removed {r.branch} / {r.name}")
    dups: dict[str, list[ForwardPerson]] = {}
    for r in rows.values():
        if r.guid:
            dups.setdefault(r.guid, []).append(r)
    for same in dups.values():                 # two yaml lines on one person there: link neither (unique guid)
        if len(same) > 1:
            for r in same:
                r.guid, r.active = None, False
            report.append("NOT LINKED " + " and ".join(r.name for r in same) + ": same person there")
    s.commit()
    return report


def options(s: Session) -> list[dict]:
    """Drop-down of the form: active supervisors already linked to the other system (a guid), by branch."""
    rows = s.scalars(select(ForwardPerson).where(ForwardPerson.active.is_(True), ForwardPerson.guid.is_not(None))
                     .order_by(ForwardPerson.branch, ForwardPerson.name))
    return [{"id": r.id, "name": r.name, "branch": r.branch, "ext": r.ext} for r in rows]


def choosable(s: Session, person_id) -> ForwardPerson | None:
    r = s.get(ForwardPerson, person_id) if isinstance(person_id, int) else None
    return r if r is not None and r.active and r.guid else None


# ---------- the ticket ----------

def _t(dt: datetime | None) -> str:
    return dt.astimezone(RIYADH).strftime("%Y-%m-%d %H:%M") if dt else "-"


def ticket_notes(call: Call, f: CallFollowup, agent_name: str) -> str:
    """The ticket text (<= 590 characters): fixed facts first, then the agent's text and note share what is left."""
    head = [f"مشكلة محوّلة من خدمة العملاء - الأولوية: {PRIORITY_AR.get(f.priority or '', '-')}",
            f"رقم العميل: {call.customer_number or '-'}",
            f"وقت المكالمة: {_t(call.start_time)}",
            f"الحل المتوقع قبل: {_t(f.expected_at)}",
            f"الموظف: {agent_name or '-'} ({call.agent_ext or '-'}) - رقم المكالمة عندنا: {call.id}"]
    fixed = "\n".join(head)
    what, note = (f.what_happened or "").strip(), (f.forward_note or "").strip()
    lw, ln = "\nما حدث: ", "\nملاحظة للمسؤول: "
    room = NOTES_MAX - len(fixed) - len(lw) - (len(ln) if note else 0)
    if note:
        n_room = max(room // 3, min(len(note), room - min(len(what), room // 2)))
        note = _cut(note, n_room)
        room -= len(note)
    out = fixed + lw + _cut(what, room) + (ln + note if note else "")
    return out[:NOTES_MAX]


def _cut(text: str, n: int) -> str:
    if n <= 0:
        return ""
    return text if len(text) <= n else text[:max(n - 1, 0)] + "…"


def send_pending(s: Session, client: CsClient, now: datetime | None = None) -> dict:
    """Send every pending forward (oldest first). Each one: customer by phone -> ticket. Row locks (SKIP LOCKED) so
    two runs never send the same form twice. Returns counts; raises CsAuthError (after saving what was done) so the
    job shows as failed on the health page (job.forward_send, listed while the link is on)."""
    now = now or datetime.now(timezone.utc)
    done = {"sent": 0, "failed": 0, "retry": 0}
    names = dict(s.execute(select(Agent.ext, Agent.name)).all())
    seen: list[int] = []
    while len(seen) < SEND_BATCH:
        stmt = select(CallFollowup).where(CallFollowup.forward_status == "pending")
        if seen:
            stmt = stmt.where(CallFollowup.id.not_in(seen))
        f = s.scalars(stmt.order_by(CallFollowup.id).limit(1).with_for_update(skip_locked=True)).first()
        if f is None:
            break
        seen.append(f.id)
        call = s.get(Call, f.call_id)
        person = s.get(ForwardPerson, f.forward_person_id) if f.forward_person_id else None
        if call is None or call.content_deleted_at is not None or f.responsible_kind != "forward" or person is None:
            f.forward_status, f.forward_error = None, None          # nothing to send any more
            s.commit()
            continue
        if now - f.updated_at > SEND_WINDOW:
            f.forward_status, f.forward_error = "failed", ERR_EXPIRED
            done["failed"] += 1
            s.commit()
            continue
        if not person.guid or not person.active:
            f.forward_error = ERR_NO_GUID
            f.forward_tries += 1
            done["retry"] += 1
            s.commit()
            continue
        step = "lookup"
        try:
            account = client.customer_by_phone(call.customer_number or "")
            step = "ticket"
            guid, number = client.create_ticket(account, ticket_notes(call, f, names.get(call.agent_ext or "", "")),
                                                CS_PRIORITY.get(f.priority or "", 2), person.guid)
        except CsAuthError:
            f.forward_error = ERR_AUTH
            f.forward_tries += 1
            s.commit()
            raise
        except CsError as exc:                      # 400: bad number, person not allowed, ...: the agent must act
            f.forward_status, f.forward_error = "failed", exc.message[:300]
            f.forward_tries += 1
            done["failed"] += 1
            s.commit()
            continue
        except CsUnavailable as exc:
            if step == "ticket" and exc.status not in (408, 429):   # maybe made there: a retry could double it
                f.forward_status, f.forward_error = "failed", ERR_UNSURE
                f.forward_tries += 1
                done["failed"] += 1
                s.commit()
                continue
            f.forward_error = ERR_UNAVAILABLE
            f.forward_tries += 1
            done["retry"] += 1
            s.commit()
            continue
        f.forward_status, f.forward_error = "sent", None
        f.forward_ticket_guid, f.forward_ticket_number, f.forwarded_at = guid, number, now
        f.forward_tries += 1
        done["sent"] += 1
        s.commit()
        log.info("forward: call %s -> ticket %s", f.call_id, number)
    return done


# ---------- D2: status back from the ticket ----------

POLL_DAYS = 30                 # a ticket is read back for 30 days after it was made
POLL_BATCH = 100
WATCH_DAYS = 30                # supervisors' calls to the customer are kept for 30 days after the forward
PROOF_MIN_TALK = 60            # answered, MORE than 60 s talk, satisfaction MORE than 7 (user, 2026-11-16)
PROOF_MIN_SCORE = 7


def parse_time(v) -> datetime | None:
    """The other system's ISO time; a time without a zone is Saudi time (their system runs in Riyadh)."""
    if not v or not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=RIYADH)


def _event_key(item: dict) -> str:
    import hashlib
    raw = "|".join(str(item.get(k) or "") for k in ("kind", "type", "at", "actorName", "targetName", "note"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def store_ticket(s: Session, f: CallFollowup, data: dict, now: datetime) -> int:
    """Save what the ticket says: closedAt, the last solution text, new timeline items. Returns new items."""
    from .db.models import ForwardEvent
    t = data.get("ticket") if isinstance(data.get("ticket"), dict) else {}
    timeline = [x for x in (data.get("timeline") or []) if isinstance(x, dict)]
    f.forward_closed_at = parse_time(t.get("closedAt"))
    closes = sorted((x for x in timeline if x.get("type") == "followup_close"),
                    key=lambda x: parse_time(x.get("at")) or datetime.min.replace(tzinfo=timezone.utc))
    f.forward_solution = ((str(closes[-1].get("note") or "")[:1000] or None)
                          if closes and f.forward_closed_at is not None else None)   # reopened: no old solution
    f.forward_checked_at = now
    have = set(s.scalars(select(ForwardEvent.ext_key).where(ForwardEvent.call_id == f.call_id)))
    new = 0
    for x in timeline:
        key = _event_key(x)
        if key in have:
            continue
        have.add(key)
        res = s.execute(pg_insert(ForwardEvent).values(
            call_id=f.call_id, ext_key=key, kind=str(x.get("kind") or "")[:20] or "event",
            type=str(x.get("type") or "")[:30] or "unknown", note=(str(x.get("note") or "")[:1000] or None),
            at=parse_time(x.get("at")), actor=(str(x.get("actorName") or "")[:200] or None), seen_at=now)
            .on_conflict_do_nothing(index_elements=["call_id", "ext_key"])   # another poll run got it first
            .returning(ForwardEvent.id))
        new += res.scalar() is not None
    return new


def poll_tickets(s: Session, client: CsClient, now: datetime | None = None) -> dict:
    """Read back every sent ticket of the last 30 days, the least recently read first. CsAuthError stops the run
    (raised after saving what was done); an unreachable system skips this run."""
    now = now or datetime.now(timezone.utc)
    done = {"read": 0, "new_items": 0, "missing": 0}
    forms = list(s.scalars(select(CallFollowup).join(Call, Call.id == CallFollowup.call_id).where(
        CallFollowup.forward_status == "sent", CallFollowup.forward_ticket_guid.is_not(None),
        CallFollowup.forwarded_at >= now - timedelta(days=POLL_DAYS), Call.content_deleted_at.is_(None))
        .order_by(CallFollowup.forward_checked_at.asc().nulls_first(), CallFollowup.id).limit(POLL_BATCH)))
    for f in forms:
        try:
            data = client.get_ticket(f.forward_ticket_guid)
        except CsAuthError:
            s.commit()
            raise
        except CsUnavailable as exc:
            if exc.status is None or exc.status in (408, 429):
                break                               # network / busy: try the rest next run
            f.forward_checked_at = now              # 5xx / not JSON for this ticket: go on with the others
            f.forward_error = str(exc)[:300]
            s.commit()
            continue
        except CsError as exc:                      # 404: the ticket is gone there
            f.forward_checked_at = now
            f.forward_error = exc.message[:300]
            done["missing"] += 1
            s.commit()
            continue
        done["new_items"] += store_ticket(s, f, data, now)
        if f.forward_error:
            f.forward_error = None
        done["read"] += 1
        s.commit()
    return done


# ---------- D2: supervisors' calls to the customer (proof of solving) ----------

def digits9(number: str | None) -> str:
    return "".join(ch for ch in (number or "") if ch.isdigit())[-9:]


def watch_list(s: Session, now: datetime | None = None) -> dict[str, list[tuple[str, datetime]]]:
    """ext -> [(customer number's last 9 digits, forwarded_at)] for problems with a supervisor (sent in the last 30
    days). Ingest keeps an outbound call of such an ext to such a number after that time (team "forward")."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, list[tuple[str, datetime]]] = {}
    q = (select(ForwardPerson.ext, Call.customer_number, CallFollowup.forwarded_at)
         .join(CallFollowup, CallFollowup.forward_person_id == ForwardPerson.id)
         .join(Call, Call.id == CallFollowup.call_id)
         .where(CallFollowup.forward_status == "sent", CallFollowup.forwarded_at.is_not(None),
                CallFollowup.forwarded_at >= now - timedelta(days=WATCH_DAYS), ForwardPerson.ext.is_not(None),
                or_(CallFollowup.forward_closed_at.is_(None),
                    CallFollowup.forward_closed_at >= now - timedelta(days=1)),   # open there (user's "ok")
                Call.content_deleted_at.is_(None)))
    for ext, number, at_ in s.execute(q):
        d = digits9(number)
        if d:
            out.setdefault(ext, []).append((d, at_))
    return out


def linked_calls(s: Session, call: Call) -> list[Call]:
    """The supervisor's kept calls (team "forward") that belong to this forwarded call: same ext, same customer,
    after the forward. Erased together with the call's content (PDPL: no list shows them, review D2)."""
    from .ingest.classify import FORWARD_TEAM
    row = s.execute(select(CallFollowup.forwarded_at, ForwardPerson.ext)
                    .join(ForwardPerson, ForwardPerson.id == CallFollowup.forward_person_id)
                    .where(CallFollowup.call_id == call.id, CallFollowup.forwarded_at.is_not(None))).first()
    d = digits9(call.customer_number)
    if row is None or not row.ext or not d:
        return []
    return [c for c in s.scalars(select(Call).where(
        Call.team == FORWARD_TEAM, Call.agent_ext == row.ext, Call.start_time > row.forwarded_at,
        Call.content_deleted_at.is_(None))) if digits9(c.customer_number) == d]


def proof(calls: list[Call], scores: dict[int, int | None], has_ext: bool) -> dict:
    """The LAST supervisor call to the customer after the forward decides (user, Q9): answered, > 60 s, score > 7.
    -> {ok, reason, calls: [...]}. reason: no_ext / no_call / not_answered / short / waiting_score / low_score."""
    shown = [{"call_id": c.id, "time": c.start_time.isoformat(), "answered": c.status == "answered",
              "talk_seconds": c.talk_seconds or 0, "score": scores.get(c.id)} for c in calls]
    if not has_ext:
        return {"ok": False, "reason": "no_ext", "calls": shown}
    if not calls:
        return {"ok": False, "reason": "no_call", "calls": shown}
    last = calls[-1]
    score = scores.get(last.id)
    if last.status != "answered":
        reason = "not_answered"
    elif (last.talk_seconds or 0) <= PROOF_MIN_TALK:
        reason = "short"
    elif score is None:
        reason = "waiting_score"
    elif score <= PROOF_MIN_SCORE:
        reason = "low_score"
    else:
        reason = None
    return {"ok": reason is None, "reason": reason, "calls": shown}
