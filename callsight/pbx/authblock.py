"""Shared "stop logging in to the PBX" switch (spec 2026-11-14-live-board, decision 1).

Yeastar may auto-block our IP after failed logins, and it refuses a 9th open token (errcode 60002). Every process
that logs in (Celery worker = ingest, CLI, live service) checks the Redis key `pbx:auth_blocked` before a login and
sets it on a refused login or errcode 60002. Only a person clears it: `python -m callsight pbx-unblock`.

Token budget for the one API Client ID (max 8 valid tokens, 30 min each): worker 1 + CLI 1 + live 1 = 3 of 8.
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger(__name__)

AUTH_BLOCKED_KEY = "pbx:auth_blocked"


class AuthGuard:
    """Redis-backed switch. `fail_closed`: when Redis can't be asked, treat it as blocked (live service, which
    needs Redis anyway); ingest/CLI use fail_closed=False and still have the adapter's own failed-login counter."""

    def __init__(self, redis, who: str, fail_closed: bool = True, clock=time.time):
        self.redis, self.who, self.fail_closed, self._clock = redis, who, fail_closed, clock

    def blocked(self) -> bool:
        try:
            return bool(self.redis.exists(AUTH_BLOCKED_KEY))
        except Exception as exc:  # noqa: BLE001 - any Redis client error
            log.warning("cannot read %s (%s): %s", AUTH_BLOCKED_KEY, type(exc).__name__,
                        "not logging in" if self.fail_closed else "continuing (in-process failed-login limit only)")
            return self.fail_closed

    def block(self, reason: str) -> None:
        """Set by the first process that sees a refused login; never overwritten (keeps the first reason)."""
        value = json.dumps({"by": self.who, "reason": reason[:200], "at": int(self._clock())})
        try:
            self.redis.set(AUTH_BLOCKED_KEY, value, nx=True)
        except Exception as exc:  # noqa: BLE001
            log.error("cannot set %s (%s); this process stops logging in anyway", AUTH_BLOCKED_KEY,
                      type(exc).__name__)
        log.error("PBX logins blocked (%s). After fixing the cause run: python -m callsight pbx-unblock", reason)


def describe(redis) -> dict | None:
    raw = redis.get(AUTH_BLOCKED_KEY)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"reason": "unreadable value"}


def unblock(redis) -> dict | None:
    """Clears the switch; returns what was stored (None when it was not set)."""
    info = describe(redis)
    redis.delete(AUTH_BLOCKED_KEY)
    return info
