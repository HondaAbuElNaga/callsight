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
