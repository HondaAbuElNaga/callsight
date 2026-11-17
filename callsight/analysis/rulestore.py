"""Word lists and topics the admin edits on the rules page (spec 2026-11-17-rules-editor).

The lists start as config/rules.yaml + config/topics.yaml. The first start fills table `rule_list` from them
(migration 0023, or the first open of the page); after that the DB wins and the YAML files are only the starting
copy. Everything else in rules.yaml (silence, max_dropped_share, the competitor_rule word and label) stays in the file.

Words are stored normalised the same way the matcher reads the transcript (`search_normalize`: no tashkeel, alef /
hamza forms, ة/ه and ى/ي folded), so what the page shows is what is matched. History rows are never deleted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import unicodedata
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import ROOT
from ..transcripts import search_normalize
from .settings import FLAG_KINDS, OTHER, TOPIC_KEY, Rules, load_rules, load_topics

log = logging.getLogger(__name__)

OWN, IGNORE, TOPICS = "competitor_own", "competitor_ignore", "topics"
WORD_LISTS = (*FLAG_KINDS, OWN, IGNORE)           # bad_words, escalation, competitor + the competitor-rule words
LIST_NAMES = (*WORD_LISTS, TOPICS)
SINGLE_WORD = frozenset({OWN, IGNORE})            # the competitor rule compares one next word
MAX_ITEMS, MAX_LETTERS = 500, 40                  # requirement 3
MAX_TOPICS, MAX_LABEL = 30, 40
CACHE_SECONDS = 60.0                              # requirement 6: the nlp worker sees a change within 1 minute


class RuleError(ValueError):
    """A refused edit. The message may quote the admin's own word (never call text)."""


def paths() -> tuple[Path, Path]:
    return (Path(os.getenv("RULES_FILE", str(ROOT / "config" / "rules.yaml"))),
            Path(os.getenv("TOPICS_FILE", str(ROOT / "config" / "topics.yaml"))))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- normalising + checking one entry ----------

def _letters_only(raw: str) -> bool:
    return all(ch.isalpha() or ch == " " or ch == "ـ" or unicodedata.category(ch).startswith("M") for ch in raw)


def norm_word(raw: str, list_name: str) -> str:
    """Typed word/phrase -> the stored (normalised) form. Raises RuleError."""
    raw = " ".join(str(raw or "").split())
    if not raw:
        raise RuleError("empty word")
    if not _letters_only(raw):
        raise RuleError(f"«{raw[:MAX_LETTERS]}»: letters and spaces only")
    n = search_normalize(raw)
    if not n:
        raise RuleError(f"«{raw[:MAX_LETTERS]}»: nothing left after normalising")
    if len(n) > MAX_LETTERS:
        raise RuleError(f"«{n[:MAX_LETTERS]}…»: at most {MAX_LETTERS} letters")
    if list_name in SINGLE_WORD and " " in n:
        raise RuleError(f"«{n}»: one word only in this list")
    return n


def check_topic(key: str, label: str) -> tuple[str, str]:
    key, label = str(key or "").strip(), " ".join(str(label or "").split())
    if not TOPIC_KEY.match(key):
        raise RuleError("topic key: a-z and _ only, 1-30 characters")
    if not label or len(label) > MAX_LABEL or any(unicodedata.category(c).startswith("C") for c in label):
        raise RuleError(f"topic label: 1-{MAX_LABEL} characters")
    return key, label


# ---------- the starting copy (YAML) ----------

def yaml_lists(rules_path: Path, topics_path: Path) -> dict[str, list]:
    """The YAML lists in their stored form: normalised, duplicates dropped, order kept. A YAML word that breaks the
    page's limits is left out (logged by count only)."""
    rules, topics = load_rules(rules_path), load_topics(topics_path)
    cr = rules.competitor_rule
    raw = {**{k: rules.flags.get(k, ()) for k in FLAG_KINDS},
           OWN: cr.own if cr else (), IGNORE: cr.ignore_next if cr else ()}
    out: dict[str, list] = {}
    for name, words in raw.items():
        kept, skipped = [], 0
        for w in words:
            try:
                n = norm_word(w, name)
            except RuleError:
                skipped += 1
                continue
            if n not in kept:
                kept.append(n)
        if skipped:
            log.warning("rules seed: %d word(s) of %s left out (limits)", skipped, name)
        out[name] = kept[:MAX_ITEMS]
    out[TOPICS] = [[k, v] for k, v in topics.items()][:MAX_TOPICS]
    return out


def ensure_seeded(session: Session, rules_path: Path | None = None, topics_path: Path | None = None) -> list[str]:
    """Insert every list that has no DB row yet (from YAML) + one history row each. Idempotent, safe when two
    processes race (ON CONFLICT DO NOTHING). Caller commits. Returns the lists filled now."""
    from ..db.models import RuleChange, RuleList
    have = set(session.scalars(select(RuleList.name)))
    if have >= set(LIST_NAMES):
        return []
    rp, tp = (rules_path, topics_path) if rules_path and topics_path else paths()
    filled = []
    for name, items in yaml_lists(rp, tp).items():
        if name in have:
            continue
        res = session.execute(insert(RuleList).values(name=name, items=items, updated_by=None, updated_at=_now())
                              .on_conflict_do_nothing(index_elements=["name"]).returning(RuleList.name))
        if res.scalar() is not None:
            session.add(RuleChange(list_name=name, user_id=None, added=_shown(name, items), removed=[],
                                   reason="first fill from the YAML file", at=_now()))
            filled.append(name)
    session.flush()
    return filled


def _shown(name: str, items: list) -> list[str]:
    return [f"{k}: {v}" for k, v in items] if name == TOPICS else list(items)


# ---------- what the matcher and the LLM use ----------

def db_lists(session: Session) -> dict[str, list]:
    from ..db.models import RuleList
    return {r.name: list(r.items or []) for r in session.scalars(select(RuleList)) if r.name in LIST_NAMES}


def apply(rules: Rules, topics: dict[str, str], rows: dict[str, list], rules_path: Path) -> tuple[Rules, dict]:
    """The YAML settings with the DB lists on top. No DB rows = exactly the YAML (same version as before)."""
    if not rows:
        return rules, topics
    flags = {k: tuple(rows[k]) if k in rows else rules.flags.get(k, ()) for k in FLAG_KINDS}
    cr = rules.competitor_rule
    if cr is not None:
        cr = replace(cr, own=tuple(rows.get(OWN, cr.own)), ignore_next=tuple(rows.get(IGNORE, cr.ignore_next)))
    db_topics = {str(k): str(v) for k, v in rows.get(TOPICS) or []}
    if db_topics and OTHER in db_topics:
        topics = db_topics
    h = hashlib.sha256(rules_path.read_bytes() + json.dumps(rows, ensure_ascii=False, sort_keys=True).encode())
    return replace(rules, flags=flags, competitor_rule=cr, version="r-" + h.hexdigest()[:8]), topics


def current(session: Session, rules_path: Path | None = None,
            topics_path: Path | None = None) -> tuple[Rules, dict[str, str]]:
    rp, tp = (rules_path, topics_path) if rules_path and topics_path else paths()
    return apply(load_rules(rp), load_topics(tp), db_lists(session), rp)


def current_topics(session: Session, topics_path: Path | None = None) -> dict[str, str]:
    """Topics only (API, CLI, forward): the DB list when filled, else the YAML file."""
    topics = load_topics(topics_path or paths()[1])
    from ..db.models import RuleList
    row = session.get(RuleList, TOPICS)
    db_topics = {str(k): str(v) for k, v in (row.items if row else None) or []}
    return db_topics if db_topics and OTHER in db_topics else topics


_cache: dict[tuple, tuple[float, tuple[Rules, dict]]] = {}


def cached(session_factory, rules_path: Path, topics_path: Path,
           max_age: float = CACHE_SECONDS) -> tuple[Rules, dict[str, str]]:
    """For the nlp worker (one read per minute, not per call). A DB error keeps the last good lists."""
    key = (id(session_factory), str(rules_path), str(topics_path))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < max_age:
        return hit[1]
    try:
        with session_factory() as s:
            val = current(s, rules_path, topics_path)
    except SQLAlchemyError as exc:
        if hit is None:
            raise
        log.warning("rule lists not re-read (%s): the last lists stay in use", type(exc).__name__)
        return hit[1]
    _cache[key] = (time.monotonic(), val)
    return val


def clear_cache() -> None:
    _cache.clear()


# ---------- edits from the page ----------

def _locked_row(session: Session, name: str):
    from ..db.models import RuleList
    ensure_seeded(session)
    row = session.get(RuleList, name, with_for_update=True)
    if row is None:                                # cannot happen after ensure_seeded; never edit nothing
        raise RuleError("list not found")
    return row


def _reason(reason: str) -> str:
    reason = " ".join(str(reason or "").split())
    if not 3 <= len(reason) <= 300:
        raise RuleError("write a short reason (3-300 characters)")
    return reason


def change_words(session: Session, name: str, user_id: int, add: list[str], remove: list[str],
                 reason: str) -> list[str]:
    """Add / remove words of one list. Duplicates and unknown words are refused. Caller commits."""
    from ..db.models import RuleChange
    if name not in WORD_LISTS:
        raise RuleError("unknown list")
    reason = _reason(reason)
    row = _locked_row(session, name)
    items = list(row.items or [])
    new_add: list[str] = []
    for w in add:
        n = norm_word(w, name)
        if n in items or n in new_add:
            raise RuleError(f"«{n}» is already in the list")
        new_add.append(n)
    gone: list[str] = []
    for w in remove:
        n = search_normalize(" ".join(str(w or "").split()))
        if n not in items or n in gone:
            raise RuleError(f"«{n[:MAX_LETTERS]}» is not in the list")
        gone.append(n)
    if not new_add and not gone:
        raise RuleError("nothing to change")
    items = [w for w in items if w not in gone] + new_add
    if len(items) > MAX_ITEMS:
        raise RuleError(f"at most {MAX_ITEMS} words in one list")
    row.items, row.updated_by, row.updated_at = items, user_id, _now()
    session.add(RuleChange(list_name=name, user_id=user_id, added=new_add, removed=gone, reason=reason, at=_now()))
    return items


def change_topics(session: Session, user_id: int, set_: dict[str, str], remove: list[str],
                  reason: str) -> list[list[str]]:
    """Add a topic / change a label (`set_`), remove topics. 'other' always stays; a key never changes meaning,
    old analyses keep theirs (the page shows the key when its label is gone). Caller commits."""
    from ..db.models import RuleChange
    reason = _reason(reason)
    row = _locked_row(session, TOPICS)
    topics = {str(k): str(v) for k, v in row.items or []}
    added, gone = [], []
    for k in remove:
        k = str(k or "").strip()
        if k == OTHER:
            raise RuleError(f"'{OTHER}' must stay")
        if k not in topics:
            raise RuleError(f"topic {k[:30]} not found")
        if k in set_:
            raise RuleError(f"topic {k[:30]}: change or remove, not both")
        gone.append(f"{k}: {topics.pop(k)}")
    for k, v in set_.items():
        k, v = check_topic(k, v)
        if topics.get(k) == v:
            continue
        if any(lbl == v for kk, lbl in topics.items() if kk != k):
            raise RuleError(f"label «{v}» is already used")
        if k in topics:
            gone.append(f"{k}: {topics[k]}")
        topics[k] = v
        added.append(f"{k}: {v}")
    if not added and not gone:
        raise RuleError("nothing to change")
    if len(topics) > MAX_TOPICS:
        raise RuleError(f"at most {MAX_TOPICS} topics")
    items = [[k, v] for k, v in topics.items()]
    row.items, row.updated_by, row.updated_at = items, user_id, _now()
    session.add(RuleChange(list_name=TOPICS, user_id=user_id, added=added, removed=gone, reason=reason, at=_now()))
    return items


def match_sentence(text: str, rules: Rules) -> list[dict]:
    """"Test a sentence": which lists match (same matcher as the calls). Nothing is stored or logged."""
    from .flags import find

    class _Seg:
        flag, start_seconds, end_seconds = None, 0.0, 0.0

        def __init__(self, t: str) -> None:
            self.text = t
    seen, out = set(), []
    for h in find([_Seg(text)], rules):
        if (h.kind, h.phrase) not in seen:
            seen.add((h.kind, h.phrase))
            out.append({"kind": h.kind, "phrase": h.phrase})
    return out
