"""One Redis lock for heavy maintenance work: backup and retention hold it; reconcile (and the asr worker, after
the transcription merge) wait while it is held. Expires on its own if a holder dies."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Iterator

MAINT_LOCK = "callsight:lock:maint"
MAINT_TTL = 6 * 3600

_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


@contextmanager
def maint_lock(redis, ttl: int = MAINT_TTL) -> Iterator[bool]:
    """Yields True when we got the lock (and releases only our own token), False when someone else holds it."""
    token = uuid.uuid4().hex
    got = bool(redis.set(MAINT_LOCK, token, nx=True, ex=ttl))
    try:
        yield got
    finally:
        if got:
            redis.eval(_RELEASE, 1, MAINT_LOCK, token)


def maint_busy(redis) -> bool:
    return bool(redis.exists(MAINT_LOCK))


# ---------- GPU lock (spec 2026-11-14-call-analysis, decision 6) ----------
# One 8 GB GPU for the asr worker (holds it while its model is loaded) and the nlp task (holds it for one Ollama
# answer, keep_alive=0). They never hold the GPU together. The value is the owner ("asr" / "nlp"); the same owner
# may take it again (refreshes the expiry). Expires on its own if a holder dies.
GPU_LOCK = "callsight:lock:gpu"


def _owner_of(value) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


def gpu_acquire(redis, owner: str, ttl: int) -> bool:
    if redis.set(GPU_LOCK, owner, nx=True, ex=ttl):
        return True
    if _owner_of(redis.get(GPU_LOCK)) == owner:     # ours already: refresh
        redis.expire(GPU_LOCK, ttl)
        return True
    return False


def gpu_release(redis, owner: str) -> bool:
    """Deletes the lock only when `owner` holds it."""
    return bool(redis.eval(_RELEASE, 1, GPU_LOCK, owner))


def gpu_holder(redis) -> str | None:
    return _owner_of(redis.get(GPU_LOCK))
