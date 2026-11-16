"""Forward a not-solved problem to an employee of the customer-service system (spec
2026-11-16-priority-no-followup, part 4): the people (every employee there + config/forward_people.yaml for the
supervisors' ext and the managers), the send job (customer by phone -> ticket), the read-back, and the forward chain
(employee -> 24 h -> his branch supervisor -> 24 h -> the managers). The form side (validation, steps) is in
followups.py.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .config import RIYADH, ROOT
from .cs_api import CsAuthError, CsClient, CsError, CsUnavailable, employees
from .db.models import Agent, Call, CallAnalysis, CallFollowup, ForwardHop, ForwardPerson

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
    """[{branch, name, ext, guid, manager}] from the yaml: `people:` (branch supervisors, branch + name required) and
    `managers:` (the forward chain's last level, name required). A broken line is an error (never a half list)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out, seen = [], set()
    for section, manager in (("people", False), ("managers", True)):
        for i, p in enumerate(data.get(section) or [], 1):
            branch, name = str((p or {}).get("branch") or "").strip(), str((p or {}).get("name") or "").strip()
            if not name or (not branch and not manager):
                raise ValueError(f"{path}: {section} {i} needs " + ("a name" if manager else "branch and name"))
            if (branch, name) in seen:
                raise ValueError(f"{path}: {branch} / {name} is listed twice")
            seen.add((branch, name))
            if len(branch) > 100 or len(name) > 100:
                raise ValueError(f"{path}: {section} {i}: branch and name must be 100 characters or less")
            ext = str(p.get("ext")).strip() if p.get("ext") not in (None, "") else None
            if ext is not None and not re.fullmatch(r"\d{1,6}", ext):
                raise ValueError(f"{path}: bad ext {ext!r} for {name}")
            guid = str(p.get("guid")).strip() if p.get("guid") else None
            if guid is not None and len(guid) > 64:
                raise ValueError(f"{path}: bad guid for {name}")
            if manager and ext is not None:
                raise ValueError(f"{path}: managers have no ext (their calls are not checked)")
            out.append({"branch": branch, "name": name, "ext": ext, "guid": guid, "manager": manager})
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
    """forward_person from the other system + the yaml (forward chain). With a client: EVERY employee of
    forward-targets is stored (any can be chosen; is_supervisor from isBranchSupervisor); the yaml people / managers
    are matched to them by name (a yaml guid wins) and give the short name, the PBX ext (proof calls) and the manager
    flag. Someone gone from the other system turns inactive (old problems keep the name). Without a client (link
    off) only the yaml lines are written. Returns report lines (CLI / log). Commits."""
    now = now or datetime.now(timezone.utc)
    people = load_people_yaml(path or people_file())
    all_rows = list(s.scalars(select(ForwardPerson)))
    by_key = {(r.branch, r.name): r for r in all_rows if r.guid is None or r.ext or r.is_manager}
    by_key_free = {k: r for k, r in by_key.items() if r.guid is None}     # a yaml-only row, not linked yet
    report: list[str] = []
    if client is None:
        keep = set()
        for p in people:
            key = (p["branch"], p["name"])
            keep.add(key)
            r = by_key.get(key)
            if r is None:
                r = ForwardPerson(branch=p["branch"], name=p["name"], active=True)
                s.add(r)
                by_key[key] = r
            r.ext, r.is_manager, r.is_supervisor = p["ext"], p["manager"], not p["manager"]
            if p["guid"]:
                r.guid = p["guid"]
            r.active = True
        for key, r in by_key.items():
            if key not in keep and r.active and (r.ext or r.is_manager):
                r.active, r.ext, r.is_manager = False, None, False
                report.append(f"removed {r.branch} / {r.name}")
        s.commit()
        return report

    emps = employees(client.forward_targets())
    linked_now = sum(1 for r in all_rows if r.guid and r.active)
    if not emps or len(emps) < linked_now // 2:     # outage answer: change nothing (review DC)
        return [f"SKIPPED: the other system gave {len(emps)} employees (we have {linked_now}); nothing changed"]
    by_guid_emp = {e["guid"]: e for e in emps}
    sups = [e for e in emps if e["is_supervisor"]]
    # 1. the yaml lines -> an employee there (never a guess between two people)
    links: dict[str, dict] = {}                 # guid -> yaml line
    dup: set[str] = set()
    for p in people:
        if p["guid"]:
            hit, why = by_guid_emp.get(p["guid"]), "not in the other system"
            if hit is not None and not p["manager"] and not hit["is_supervisor"]:
                hit, why = None, "not a supervisor there"
        else:
            hit, why = match_person(p, emps if p["manager"] else sups)
        if hit is None:
            report.append(f"NOT LINKED {p['branch'] or '-'} / {p['name']}: {why}")
            continue
        if hit["guid"] in links:
            dup.add(hit["guid"])
        links.setdefault(hit["guid"], p)
        report.append(f"ok {p['branch'] or '-'} / {p['name']} -> {hit['name']} ({hit['branch']})")
    for g in dup:
        report.append(f"NOT LINKED {links[g]['name']} and another line: same person there")
        links.pop(g)
    # 2. every employee there -> a row (a yaml-only row of the same person is reused, so old forms keep it)
    rows_by_guid = {r.guid: r for r in all_rows if r.guid}
    seen_rows: set[int] = set()
    for e in emps:
        p = links.get(e["guid"])
        r = rows_by_guid.get(e["guid"])
        if r is None and p is not None:
            r = by_key_free.pop((p["branch"], p["name"]), None)
        if r is None:
            r = ForwardPerson(guid=e["guid"], name="", branch="")
            s.add(r)
        r.guid, r.full_name, r.branch, r.active, r.synced_at = e["guid"], e["name"], e["branch"], True, now
        r.is_supervisor, r.grp = e["is_supervisor"], e["group"] or None
        r.name = (p["name"] if p else e["name"])[:100]
        r.ext = p["ext"] if p else None
        r.is_manager = bool(p and p["manager"])
        s.flush()
        seen_rows.add(r.id)
    # 3. everyone else: gone there, or a yaml line that is not linked -> inactive
    for r in all_rows:
        if r.id not in seen_rows and r.active:
            r.active = False
            if r.guid:
                report.append(f"removed {r.branch} / {r.name}")
        if r.id not in seen_rows:
            r.ext, r.is_manager = None, False
    s.commit()
    n_sup = sum(e["is_supervisor"] for e in emps)
    n_man = sum(1 for p in links.values() if p["manager"])
    report.insert(0, f"employees: {len(emps)} ({n_sup} branch supervisors, {n_man} managers linked)")
    return report


def options(s: Session) -> list[dict]:
    """Drop-down of the form: every active employee linked to the other system (a guid), by branch."""
    rows = s.scalars(select(ForwardPerson).where(ForwardPerson.active.is_(True), ForwardPerson.guid.is_not(None))
                     .order_by(ForwardPerson.branch, ForwardPerson.is_supervisor.desc(), ForwardPerson.name))
    return [{"id": r.id, "name": r.name, "branch": r.branch, "ext": r.ext, "supervisor": r.is_supervisor,
             "manager": r.is_manager} for r in rows]


def choosable(s: Session, person_id) -> ForwardPerson | None:
    r = s.get(ForwardPerson, person_id) if isinstance(person_id, int) else None
    return r if r is not None and r.active and r.guid else None


def level_of(p: ForwardPerson) -> int:
    """The chain starts at the chosen person's level: 1 employee, 2 branch supervisor, 3 manager."""
    return 3 if p.is_manager else 2 if p.is_supervisor else 1


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
        s.add(ForwardHop(call_id=f.call_id, level=level_of(person), person_id=person.id, ticket_guid=guid,
                         ticket_number=number, sent_at=now, result="open"))      # the chain starts (24 h each)
        done["sent"] += 1
        s.commit()
        log.info("forward: call %s -> ticket %s", f.call_id, number)
    return done


# ---------- D2: status back from the ticket (one read per open hop) ----------

POLL_DAYS = 90                 # safety cut: an open hop's ticket is read back for at most 90 days
POLL_BATCH = 100
CLOSE_GRACE = timedelta(days=1)  # a supervisor's calls are still kept 1 day after he closed (the closing call)
PROOF_MIN_TALK = 60            # answered, MORE than 60 s talk, satisfaction MORE than 7 (user, 2026-11-16)
PROOF_MIN_SCORE = 7
HOP_TIME = timedelta(hours=24)   # each person has 24 clock hours (user, 2026-11-16)
WATCH_MAX = HOP_TIME + CLOSE_GRACE + timedelta(hours=12)   # a supervisor's calls: never kept longer after he got it
ADVANCE_BATCH = 500
COPY_MAX_TRIES = 288             # a copy ticket that cannot be made in 24 h (5-min runs) -> failed
FRESH = timedelta(minutes=10)    # the chain moves only on a ticket read back this recently
ERR_NO_NEXT = "لا يوجد من تنتقل إليه (مشرف الفرع / الإدارة غير مربوطين)"


def parse_time(v) -> datetime | None:
    """The other system's ISO time; a time without a zone is Saudi time (their system runs in Riyadh)."""
    if not v or not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=RIYADH)


def _event_key(guid: str, item: dict) -> str:
    import hashlib
    raw = "|".join([guid, *(str(item.get(k) or "") for k in ("kind", "type", "at", "actorName", "targetName",
                                                             "note"))])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def closed(h: ForwardHop) -> bool:
    """Closed there while THIS person held it (a close from before he got the ticket does not count)."""
    return h.closed_at is not None and (h.sent_at is None or h.closed_at >= h.sent_at - timedelta(minutes=5))


def store_ticket(s: Session, h: ForwardHop, data: dict, now: datetime) -> int:
    """Save what the ticket says on the hop: closedAt, the last solution text, new timeline items (shared by the
    call; a copy ticket's items are keyed by its own guid). The first ticket is mirrored on the form. -> new items."""
    from .db.models import ForwardEvent
    t = data.get("ticket") if isinstance(data.get("ticket"), dict) else {}
    timeline = [x for x in (data.get("timeline") or []) if isinstance(x, dict)]
    h.closed_at = parse_time(t.get("closedAt"))
    closes = sorted((x for x in timeline if x.get("type") == "followup_close"),
                    key=lambda x: parse_time(x.get("at")) or datetime.min.replace(tzinfo=timezone.utc))
    h.solution = ((str(closes[-1].get("note") or "")[:1000] or None)
                  if closes and h.closed_at is not None else None)       # reopened: no old solution
    h.checked_at = now
    f = s.scalar(select(CallFollowup).where(CallFollowup.call_id == h.call_id))
    if f is not None and f.forward_ticket_guid == h.ticket_guid:
        f.forward_closed_at, f.forward_solution, f.forward_checked_at = h.closed_at, h.solution, now
    have = set(s.scalars(select(ForwardEvent.ext_key).where(ForwardEvent.call_id == h.call_id)))
    new = 0
    for x in timeline:
        key = _event_key(h.ticket_guid or "", x)
        if key in have:
            continue
        have.add(key)
        res = s.execute(pg_insert(ForwardEvent).values(
            call_id=h.call_id, ext_key=key, kind=str(x.get("kind") or "")[:20] or "event",
            type=str(x.get("type") or "")[:30] or "unknown", note=(str(x.get("note") or "")[:1000] or None),
            at=parse_time(x.get("at")), actor=(str(x.get("actorName") or "")[:200] or None), seen_at=now)
            .on_conflict_do_nothing(index_elements=["call_id", "ext_key"])   # another poll run got it first
            .returning(ForwardEvent.id))
        new += res.scalar() is not None
    return new


def poll_tickets(s: Session, client: CsClient, now: datetime | None = None) -> dict:
    """Read back the ticket of every open hop (the least recently read first). CsAuthError stops the run (raised
    after saving what was done); network / busy skips the rest of this run; one broken ticket never stops the
    others."""
    now = now or datetime.now(timezone.utc)
    done = {"read": 0, "new_items": 0, "missing": 0}
    hops = list(s.scalars(select(ForwardHop).join(Call, Call.id == ForwardHop.call_id).where(
        ForwardHop.result == "open", ForwardHop.ticket_guid.is_not(None),
        ForwardHop.sent_at >= now - timedelta(days=POLL_DAYS), Call.content_deleted_at.is_(None))
        .order_by(ForwardHop.checked_at.asc().nulls_first(), ForwardHop.id).limit(POLL_BATCH)))
    for h in hops:
        try:
            data = client.get_ticket(h.ticket_guid)
        except CsAuthError:
            s.commit()
            raise
        except CsUnavailable as exc:
            if exc.status is None or exc.status in (408, 429):
                break                               # network / busy: try the rest next run
            h.checked_at, h.error = now, str(exc)[:300]   # 5xx / not JSON for this ticket: go on with the others
            s.commit()
            continue
        except CsError as exc:                      # 404: the ticket is gone there
            h.checked_at, h.error = now, exc.message[:300]
            done["missing"] += 1
            s.commit()
            continue
        done["new_items"] += store_ticket(s, h, data, now)
        h.error = None
        done["read"] += 1
        s.commit()
    return done


# ---------- the forward chain: solved? 24 h over? -> the next person ----------

def advance(s: Session, client: CsClient, now: datetime | None = None) -> dict:
    """For every problem with an open hop (row lock on its form, SKIP LOCKED: two runs never move one twice):
    make waiting copy tickets, mark a close as solved (a branch supervisor with a known ext also needs the good
    last call), close the other managers' copies, and after 24 h pass the ticket on: employee -> his branch's
    supervisor -> the managers (all of them: the first on the same ticket, a copy ticket for each other one)."""
    now = now or datetime.now(timezone.utc)
    done = {"solved": 0, "passed_on": 0, "copies": 0, "errors": 0}
    ids = list(s.scalars(select(ForwardHop.call_id).join(Call, Call.id == ForwardHop.call_id).where(
        ForwardHop.result.in_(("open", "pending")), Call.content_deleted_at.is_(None),
        or_(ForwardHop.sent_at.is_(None), ForwardHop.sent_at >= now - timedelta(days=POLL_DAYS)))
        .group_by(ForwardHop.call_id).order_by(func.min(ForwardHop.checked_at).desc().nulls_last())
        .limit(ADVANCE_BATCH)))
    for cid in ids:
        f = s.scalars(select(CallFollowup).where(CallFollowup.call_id == cid)
                      .with_for_update(skip_locked=True)).first()
        if f is None:
            s.rollback()
            continue
        call = s.get(Call, cid)
        hops = list(s.scalars(select(ForwardHop).where(ForwardHop.call_id == cid).order_by(ForwardHop.id)))
        try:
            _advance_one(s, client, f, call, hops, now, done)
        except CsAuthError:
            s.commit()
            raise
        except Exception:  # noqa: BLE001 - one broken problem must not stop the others; ids only in the log
            s.rollback()
            log.exception("forward chain: call %s not moved", cid)
            done["errors"] += 1
            continue
        s.commit()
    return done


def _person(s: Session, h: ForwardHop) -> ForwardPerson:
    return s.get(ForwardPerson, h.person_id)


def _advance_one(s, client, f, call, hops, now, done) -> None:
    for h in [h for h in hops if h.result == "pending"]:
        _send_copy(s, client, f, call, h, now, done)
    open_ = [h for h in hops if h.result == "open"]
    for h in open_:
        if not closed(h):
            continue
        person = _person(s, h)
        if h.level == 2 and person.ext and not hop_proof(s, call, h, person)["ok"]:
            continue                                # closed without a good call: not solved (24 h still runs)
        h.result, h.ended_at = "solved", now
        done["solved"] += 1
        for o in hops:
            if o is not h and o.result in ("open", "pending"):
                was_open = o.result == "open"
                o.result, o.ended_at = "closed_by_other", now
                if was_open and o.ticket_guid and o.ticket_guid != h.ticket_guid:   # another manager's copy
                    try:
                        client.followup(o.ticket_guid, f"حُلّت عند «{person.name}»", close=True)
                    except (CsError, CsUnavailable) as exc:
                        if isinstance(exc, CsAuthError):
                            raise
                        o.error = str(exc)[:300]
        return
    for h in open_:
        if h.level == 3 or h.sent_at is None or now < h.sent_at + HOP_TIME:
            continue
        if h.ticket_guid and (h.checked_at is None or h.checked_at < now - FRESH):
            continue                                # not read back in this run: wait for a fresh read (review DC)
        person = _person(s, h)
        if (h.level == 2 and person.ext and closed(h) and now < h.sent_at + 2 * HOP_TIME
                and hop_proof(s, call, h, person)["reason"] == "waiting_score"):
            continue                                # the score is late on OUR side: up to 24 h more, then on
        level, nxt = next_people(s, h, person)
        if not nxt:
            if h.error != ERR_NO_NEXT:
                h.error = ERR_NO_NEXT
            continue
        _pass_on(s, client, h, person, level, nxt, now, done)


def next_people(s: Session, h: ForwardHop, person: ForwardPerson) -> tuple[int, list[ForwardPerson]]:
    """Level 1 -> the supervisor of his branch (or the managers when there is none / he is the supervisor);
    level 2 -> the managers (at most 4)."""
    usable = (ForwardPerson.active.is_(True), ForwardPerson.guid.is_not(None), ForwardPerson.id != person.id)
    if h.level == 1:
        sup = s.scalars(select(ForwardPerson).where(*usable, ForwardPerson.is_supervisor.is_(True),
                                                    ForwardPerson.is_manager.is_(False),
                                                    ForwardPerson.branch == person.branch,
                                                    ForwardPerson.grp.is_not_distinct_from(person.grp))
                        .order_by(ForwardPerson.ext.is_(None), ForwardPerson.id).limit(1)).first()
        if sup is not None:
            return 2, [sup]
    return 3, list(s.scalars(select(ForwardPerson).where(*usable, ForwardPerson.is_manager.is_(True))
                             .order_by(ForwardPerson.id).limit(4)))


def _pass_on(s, client, h, person, level, nxt, now, done) -> None:
    note = f"لم تُحل خلال 24 ساعة عند «{person.name}»"
    try:
        was = h.closed_at
        store_ticket(s, h, client.get_ticket(h.ticket_guid), now)   # the latest state: a close just now wins
        if closed(h) and was is None:
            return                                  # closed in the meantime: the next run checks it as a solve
        if h.closed_at is not None:                 # closed (not solved, or stale): open it again first
            client.reopen(h.ticket_guid, note)
            store_ticket(s, h, client.get_ticket(h.ticket_guid), now)
            if h.closed_at is not None:
                raise CsError(409, "تعذّر إعادة فتح التذكرة")
        client.forward_ticket(h.ticket_guid, nxt[0].guid, note)
    except CsAuthError:
        h.error = ERR_AUTH
        raise
    except (CsError, CsUnavailable) as exc:         # tried again next run (forwarding twice is harmless)
        h.error, h.tries = str(exc)[:300] if isinstance(exc, CsUnavailable) else exc.message[:300], h.tries + 1
        done["errors"] += 1
        return
    h.result, h.ended_at, h.error = "passed_on", now, None
    s.add(ForwardHop(call_id=h.call_id, level=level, person_id=nxt[0].id, ticket_guid=h.ticket_guid,
                     ticket_number=h.ticket_number, sent_at=now, result="open"))
    for p in nxt[1:]:                               # the other managers: a copy ticket each (made next)
        s.add(ForwardHop(call_id=h.call_id, level=level, person_id=p.id, result="pending"))
    done["passed_on"] += 1
    s.flush()


def _send_copy(s, client, f, call, h, now, done) -> None:
    """A copy ticket for a manager (their API forwards a ticket to one person only). Same rules as the first send:
    a ticket request without a clear answer is never sent again by itself (it may exist there)."""
    person = _person(s, h)
    names = dict(s.execute(select(Agent.ext, Agent.name)).all())
    notes = "نسخة للإدارة: لم تُحل عند الموظف ومشرف الفرع خلال 24 ساعة لكل منهما\n" + ticket_notes(
        call, f, names.get(call.agent_ext or "", ""))
    step = "lookup"
    try:
        account = client.customer_by_phone(call.customer_number or "")
        step = "ticket"
        guid, number = client.create_ticket(account, notes[:NOTES_MAX], CS_PRIORITY.get(f.priority or "", 2),
                                            person.guid)
    except CsAuthError:
        h.error = ERR_AUTH
        raise
    except CsError as exc:
        h.result, h.error, h.ended_at = "failed", exc.message[:300], now
        done["errors"] += 1
        return
    except CsUnavailable as exc:
        h.tries += 1
        if step == "ticket" and exc.status not in (408, 429):
            h.result, h.error, h.ended_at = "failed", ERR_UNSURE, now
        elif h.tries >= COPY_MAX_TRIES:
            h.result, h.error, h.ended_at = "failed", ERR_EXPIRED, now
        else:
            h.error = ERR_UNAVAILABLE
        done["errors"] += 1
        return
    h.ticket_guid, h.ticket_number, h.sent_at, h.result, h.error = guid, number, now, "open", None
    done["copies"] += 1


# ---------- D2: supervisors' calls to the customer (proof of solving) ----------

def digits9(number: str | None) -> str:
    return "".join(ch for ch in (number or "") if ch.isdigit())[-9:]


def watch_list(s: Session, now: datetime | None = None) -> dict[str, list[tuple[str, datetime]]]:
    """ext -> [(customer number's last 9 digits, since)] while a branch supervisor with a known ext holds the
    problem (+1 day after his close). Ingest keeps an outbound call of such an ext to such a number after that time
    (team "forward"). Only supervisors: the user can track only their calls (2026-11-16)."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, list[tuple[str, datetime]]] = {}
    q = (select(ForwardPerson.ext, Call.customer_number, ForwardHop.sent_at)
         .join(ForwardHop, ForwardHop.person_id == ForwardPerson.id)
         .join(Call, Call.id == ForwardHop.call_id)
         .where(ForwardHop.level == 2, ForwardHop.sent_at.is_not(None), ForwardPerson.ext.is_not(None),
                ForwardPerson.is_manager.is_(False),
                or_(ForwardHop.result == "open",
                    ForwardHop.ended_at >= now - CLOSE_GRACE),     # the closing call may come just after
                or_(ForwardHop.closed_at.is_(None),
                    ForwardHop.closed_at >= now - CLOSE_GRACE),    # closed there: 1 more day, then stop
                ForwardHop.sent_at >= now - WATCH_MAX,             # his 24 h + 1 day at most (review DC)
                Call.content_deleted_at.is_(None)))
    for ext, number, at_ in s.execute(q):
        d = digits9(number)
        if d:
            out.setdefault(ext, []).append((d, at_))
    return out


def _sup_calls(s: Session, pairs: list[tuple[str, str, datetime]]) -> tuple[list[Call], dict[int, int | None]]:
    """Kept supervisor calls (team "forward", outbound) for [(ext, digits9, since)], oldest first, + the newest
    done analysis' llm_score of each (None = no score yet)."""
    from .ingest.classify import FORWARD_TEAM
    from .db.models import norm_number
    pairs = [x for x in pairs if x[0] and x[1]]
    if not pairs:
        return [], {}
    calls = [c for c in s.scalars(select(Call).where(
        Call.team == FORWARD_TEAM, Call.direction == "outbound", Call.agent_ext.in_({x[0] for x in pairs}),
        norm_number(Call.customer_number).in_({x[1] for x in pairs}),
        Call.start_time > min(x[2] for x in pairs)).order_by(Call.start_time, Call.id))
        if any(c.agent_ext == e and digits9(c.customer_number) == d and c.start_time > t for e, d, t in pairs)]
    scores: dict[int, int | None] = {}
    if calls:
        for cid, score in s.execute(select(CallAnalysis.call_id, CallAnalysis.llm_score).where(
                CallAnalysis.call_id.in_([c.id for c in calls]), CallAnalysis.status == "done")
                .order_by(CallAnalysis.call_id, CallAnalysis.id)):
            scores[cid] = score                     # the newest analysis wins
    return calls, scores


def hop_calls(calls: list[Call], scores: dict, ext: str | None, number: str | None, since: datetime | None):
    d = digits9(number)
    got = [c for c in calls if ext and since and c.agent_ext == ext and digits9(c.customer_number) == d
           and c.start_time > since]
    return got, {c.id: scores.get(c.id) for c in got}


def hop_proof(s: Session, call: Call, h: ForwardHop, person: ForwardPerson) -> dict:
    calls, scores = _sup_calls(s, [(person.ext or "", digits9(call.customer_number), h.sent_at)])
    got, sc = hop_calls(calls, scores, person.ext, call.customer_number, h.sent_at)
    return proof(got, sc, bool(person.ext))


def linked_calls(s: Session, call: Call) -> list[Call]:
    """The supervisors' kept calls (team "forward") that belong to this forwarded call: same ext, same customer,
    after he got it. Erased together with the call's content (PDPL: no list shows them, review D2)."""
    rows = s.execute(select(ForwardHop.sent_at, ForwardHop.ended_at, ForwardPerson.ext)
                     .join(ForwardPerson, ForwardPerson.id == ForwardHop.person_id)
                     .where(ForwardHop.call_id == call.id, ForwardHop.level == 2, ForwardHop.sent_at.is_not(None),
                            ForwardPerson.ext.is_not(None))).all()
    d = digits9(call.customer_number)
    calls, _ = _sup_calls(s, [(ext, d, at_) for at_, _end, ext in rows])
    # only inside each hop's watch window, so a later problem of the same customer keeps its calls (review DC)
    return [c for c in calls if c.content_deleted_at is None and any(
        c.agent_ext == ext and at_ < c.start_time <= min(at_ + WATCH_MAX, (end or c.start_time) + CLOSE_GRACE)
        for at_, end, ext in rows)]


def proof(calls: list[Call], scores: dict[int, int | None], has_ext: bool) -> dict:
    """The LAST supervisor call to the customer after he got it decides (user, Q9): answered, > 60 s, score > 7.
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
