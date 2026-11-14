"""The frozen blind test set (decision 8) and who may see an LLM estimate.

- Pool: answered customer calls of a team agent that have a done transcript.
- Sampler: per agent, calls ordered by sha256(salt:call_id); each agent gets a share of `n` proportional to its
  pool size (largest remainder). Same pool + salt -> same set (repeatable). Written once into `blind_set`, never
  changed afterwards (rows only disappear when a call is deleted).
- Until the set is frozen NO estimate is shown anywhere (any call could still become blind), so every label given
  before freezing is a blind label too. Blind calls: the estimate is computed (for the baseline) but hidden on the
  rating page always, and elsewhere until the viewing user has rated the call.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ..asr.queueing import CUSTOMER_DIRECTIONS
from ..db.models import BlindSet, Call, SatisfactionLabel, Transcript

SAMPLER_VERSION = "sha-prop-v1"
DEFAULT_SALT = "callsight-blind-v1"
DEFAULT_N = 100
DEFAULT_MIN_POOL = 200          # automatic freeze waits for a pool twice the set size (a real sample, not "all")
LABEL_TARGET = 500


def _key(salt: str, call_id: int) -> str:
    return hashlib.sha256(f"{salt}:{call_id}".encode()).hexdigest()


def pool(session: Session) -> list[tuple[int, str]]:
    done = exists().where(Transcript.call_id == Call.id, Transcript.status == "done")
    rows = session.execute(select(Call.id, Call.agent_ext).where(
        Call.status == "answered", Call.direction.in_(CUSTOMER_DIRECTIONS), Call.agent_ext.is_not(None), done)
        .order_by(Call.id)).all()
    return [(cid, ext) for cid, ext in rows]


def sample(calls: list[tuple[int, str]], n: int, salt: str = DEFAULT_SALT) -> list[tuple[int, str]]:
    """Proportional over agents, sha256 order inside each agent; result ordered by hash (= rank)."""
    by_agent: dict[str, list[int]] = defaultdict(list)
    for cid, ext in calls:
        by_agent[ext].append(cid)
    total = len(calls)
    n = min(n, total)
    if n < 1:
        return []
    exact = {ext: n * len(ids) / total for ext, ids in by_agent.items()}
    quota = {ext: math.floor(x) for ext, x in exact.items()}
    for ext in sorted(exact, key=lambda e: (-(exact[e] - quota[e]), e))[:n - sum(quota.values())]:
        quota[ext] += 1
    picked = []
    for ext, ids in by_agent.items():
        picked += [(cid, ext) for cid in sorted(ids, key=lambda c: _key(salt, c))[:quota[ext]]]
    return sorted(picked, key=lambda p: _key(salt, p[0]))


def is_frozen(session: Session) -> bool:
    return session.scalar(select(exists().where(BlindSet.call_id.is_not(None)))) or False


def size(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(BlindSet)) or 0


def freeze(session: Session, n: int = DEFAULT_N, salt: str = DEFAULT_SALT, min_pool: int = DEFAULT_N) -> int:
    """Create the blind set once. Raises ValueError when it exists already or the pool is too small."""
    if n < 1:
        raise ValueError("the blind set needs at least 1 call (n >= 1)")   # a negative n would freeze nearly all
    if is_frozen(session):
        raise ValueError("the blind set is already frozen (it never changes)")
    calls = pool(session)
    if len(calls) < max(n, min_pool):
        raise ValueError(f"only {len(calls)} transcribed calls; need at least {max(n, min_pool)}")
    picked = sample(calls, n, salt)
    session.add_all(BlindSet(call_id=cid, rank=i, agent_ext=ext, sampler_version=SAMPLER_VERSION, salt=salt)
                    for i, (cid, ext) in enumerate(picked, 1))
    session.commit()
    return len(picked)


def ensure(session: Session, n: int = DEFAULT_N, min_pool: int = DEFAULT_MIN_POOL) -> int:
    """Beat job: freeze automatically once the pool is big enough. Returns rows created (0 = nothing done)."""
    if is_frozen(session):
        return 0
    try:
        return freeze(session, n=n, min_pool=min_pool)
    except ValueError:
        session.rollback()
        return 0


def is_blind(session: Session, call_id: int) -> bool:
    return session.get(BlindSet, call_id) is not None


def rated_by(session: Session, call_id: int, user_id: int) -> bool:
    return session.scalar(select(SatisfactionLabel.id).where(SatisfactionLabel.call_id == call_id,
                                                             SatisfactionLabel.user_id == user_id)) is not None


def estimate_hidden(session: Session, call_id: int, user_id: int, rating_page: bool = False) -> str | None:
    """Why the LLM estimate must not be shown to this user for this call (None = may be shown)."""
    if not is_frozen(session):
        return "not_frozen"
    if is_blind(session, call_id) and (rating_page or not rated_by(session, call_id, user_id)):
        return "blind"
    return None


def progress(session: Session) -> dict:
    labelled = session.scalar(select(func.count(func.distinct(SatisfactionLabel.call_id)))) or 0
    blind_total = size(session)
    blind_done = session.scalar(select(func.count(func.distinct(BlindSet.call_id))).join(
        SatisfactionLabel, (SatisfactionLabel.call_id == BlindSet.call_id) & SatisfactionLabel.prefill.is_(None))) or 0
    return {"labels": labelled, "labels_target": LABEL_TARGET, "blind_frozen": blind_total > 0,
            "blind_rated": blind_done, "blind_target": blind_total or DEFAULT_N}
