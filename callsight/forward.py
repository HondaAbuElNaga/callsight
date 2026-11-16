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
from sqlalchemy import select
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
        except CsUnavailable:
            if step == "ticket":                    # maybe made there: a retry could make a second ticket
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
