"""Live board push: WebSocket /api/live + the Listen stub (spec 2026-11-14-live-board, decisions 2 and 4).

WebSocket rules: the exact Origin allowlist is checked BEFORE accept (SameSite does not cover other localhost
ports). Login cookie, admin/supervisor only and max 3 sockets per user / 20 in total are checked right AFTER accept,
then the socket is closed with 4401 / 4403 / 1013: a close before accept reaches a real browser only as 1006 (uvicorn
answers the handshake with HTTP 403), so the page could not tell "login expired" apart. Then: snapshot, then deltas,
each with a sequence number (the browser asks for "snapshot" again on a gap); the login is re-checked every
RECHECK seconds (logout / disabled user / 8 h expiry close the socket); a client whose bounded send queue fills
up is dropped. One Redis subscriber per API process fans out to all sockets.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Protocol

import anyio
from fastapi import WebSocket
from fastapi.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect, WebSocketState

from ..db.models import AppUser
from . import auth

log = logging.getLogger(__name__)

CLOSE_POLICY = 1008        # bad Origin
CLOSE_TRY_AGAIN = 1013     # too many sockets / too slow
CLOSE_LOGIN = 4401         # no / expired login
CLOSE_FORBIDDEN = 4403     # not admin or supervisor


class LiveSource(Protocol):
    async def snapshot(self) -> dict | None: ...
    async def last_seen(self) -> float | None: ...
    def messages(self) -> AsyncIterator[dict]: ...


class NoLiveSource:
    """API without Redis wiring (tests of other pages): always "no live data"."""

    async def snapshot(self) -> dict | None:
        return None

    async def last_seen(self) -> float | None:
        return None

    async def messages(self) -> AsyncIterator[dict]:
        while True:
            await asyncio.sleep(3600)
            if False:  # pragma: no cover
                yield {}


class RedisLiveSource:
    """redis.asyncio reader of the keys/channel the live service writes (callsight.live.bus)."""

    def __init__(self, url: str):
        import redis.asyncio as aioredis
        self.redis = aioredis.Redis.from_url(url, socket_connect_timeout=2)

    async def snapshot(self) -> dict | None:
        from ..live.bus import SNAPSHOT_KEY
        try:
            raw = await self.redis.get(SNAPSHOT_KEY)
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            log.warning("live: cannot read snapshot (%s)", type(exc).__name__)
            return None

    async def last_seen(self) -> float | None:
        from ..live.bus import LAST_SEEN_KEY
        try:
            raw = await self.redis.get(LAST_SEEN_KEY)
            return float(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None

    async def messages(self) -> AsyncIterator[dict]:
        from ..live.bus import CHANNEL
        delay = 1.0
        while True:
            pubsub = None
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(CHANNEL)
                delay = 1.0
                async for m in pubsub.listen():
                    if m.get("type") != "message":
                        continue
                    try:
                        yield json.loads(m["data"])
                    except (TypeError, ValueError):
                        continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - Redis restart: resubscribe with back-off
                log.warning("live: Redis subscription lost (%s)", type(exc).__name__)
                await _close_pubsub(pubsub)          # don't leak one pool connection per Redis restart
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2)


@dataclass(eq=False)
class Client:
    user_id: int
    queue: asyncio.Queue
    dropped: asyncio.Event = field(default_factory=asyncio.Event)


class LiveHub:
    def __init__(self, source: LiveSource, max_total: int = 20, max_per_user: int = 3, queue_size: int = 64):
        self.source, self.max_total, self.max_per_user, self.queue_size = source, max_total, max_per_user, queue_size
        self.clients: set[Client] = set()
        self._pump: asyncio.Task | None = None

    def join(self, user_id: int) -> Client | None:
        if len(self.clients) >= self.max_total or \
                sum(1 for c in self.clients if c.user_id == user_id) >= self.max_per_user:
            return None
        c = Client(user_id, asyncio.Queue(maxsize=self.queue_size))
        self.clients.add(c)
        loop = asyncio.get_running_loop()
        if self._pump is None or self._pump.done() or self._pump.get_loop() is not loop:
            self._pump = loop.create_task(self._run())
        return c

    def leave(self, c: Client) -> None:
        self.clients.discard(c)

    def offer(self, c: Client, item: dict) -> None:
        try:
            c.queue.put_nowait(item)
        except asyncio.QueueFull:          # slow client: drop it rather than buffer without limit
            c.dropped.set()

    async def _run(self) -> None:
        async for msg in self.source.messages():
            for c in list(self.clients):
                self.offer(c, {"type": "delta", **msg})


@dataclass
class LiveConfig:
    origins: frozenset[str]
    source: LiveSource
    recheck_seconds: float = 30.0
    max_total: int = 20
    max_per_user: int = 3
    queue_size: int = 64
    clock: Callable[[], float] = time.time


async def board_snapshot(cfg: LiveConfig) -> dict:
    """The whole board now. Also served once over HTTP (GET /api/live/snapshot) for systems that poll."""
    snap = await cfg.source.snapshot()
    base = {"type": "snapshot", "server_time": round(cfg.clock(), 3)}
    if snap is None:          # live service off / dead: "no live data" + when it was last seen
        return {**base, "available": False, "seq": None, "status": "off", "calls": [],
                "last_seen": await cfg.source.last_seen()}
    return {**base, "available": True, "seq": snap.get("seq"), "status": snap.get("status"),
            "calls": snap.get("calls") or [], "last_seen": snap.get("published_at")}


def register(app, cfg: LiveConfig, session_factory, secret: str) -> LiveHub:
    hub = LiveHub(cfg.source, cfg.max_total, cfg.max_per_user, cfg.queue_size)

    def check_login(token: str | None) -> AppUser | None:
        with session_factory() as s:
            return auth.user_from_token(s, token, secret)

    async def snapshot_message() -> dict:
        return await board_snapshot(cfg)

    @app.websocket("/api/live")
    async def live_ws(ws: WebSocket) -> None:
        if ws.headers.get("origin") not in cfg.origins:
            await ws.close(code=CLOSE_POLICY)
            return
        token = ws.cookies.get(auth.COOKIE_NAME)
        user = await run_in_threadpool(check_login, token)
        await ws.accept()        # after the Origin check; the codes below must reach the browser (see module doc)
        if user is None:
            await ws.close(code=CLOSE_LOGIN)
            return
        if user.role not in auth.STAFF_ROLES:
            await ws.close(code=CLOSE_FORBIDDEN)
            return
        client = hub.join(user.id)
        if client is None:
            await ws.close(code=CLOSE_TRY_AGAIN)
            return
        try:
            hub.offer(client, await snapshot_message())
            outcome: dict[str, int] = {}

            async def first(fn, *args) -> None:
                code = await fn(*args)
                outcome.setdefault("code", code)
                tg.cancel_scope.cancel()          # whichever ends first ends the socket

            async def dropped() -> int:
                await client.dropped.wait()
                return CLOSE_TRY_AGAIN

            async with anyio.create_task_group() as tg:
                tg.start_soon(first, _sender, ws, client)
                tg.start_soon(first, _receiver, ws, client, hub, snapshot_message)
                tg.start_soon(first, _recheck, token, check_login, cfg.recheck_seconds)
                tg.start_soon(first, dropped)
            if ws.client_state == WebSocketState.CONNECTED and ws.application_state == WebSocketState.CONNECTED:
                try:
                    await ws.close(code=outcome.get("code", CLOSE_TRY_AGAIN))
                except RuntimeError:
                    pass
        finally:
            hub.leave(client)

    return hub


async def _close_pubsub(pubsub) -> None:
    if pubsub is None:
        return
    try:
        closer = getattr(pubsub, "aclose", None) or pubsub.close
        await closer()
    except Exception:  # noqa: BLE001 - already broken: nothing more to free
        pass


async def _sender(ws: WebSocket, client: Client) -> int:
    try:
        while True:
            await ws.send_text(json.dumps(await client.queue.get(), ensure_ascii=False))
    except (WebSocketDisconnect, RuntimeError):
        return 1000


async def _receiver(ws: WebSocket, client: Client, hub: LiveHub, snapshot_message) -> int:
    """The only thing a browser may send: "snapshot" (after a sequence gap / on wake-up)."""
    try:
        while True:
            text = await ws.receive_text()
            if text[:20] == "snapshot":
                hub.offer(client, await snapshot_message())
    except (WebSocketDisconnect, RuntimeError, KeyError):
        return 1000


async def _recheck(token: str | None, check_login, every: float) -> int:
    """Logout, a disabled account, a changed password / extension or the 8 h expiry end the socket too."""
    while True:
        await asyncio.sleep(every)
        user = await run_in_threadpool(check_login, token)
        if user is None:
            return CLOSE_LOGIN
        if user.role not in auth.STAFF_ROLES:
            return CLOSE_FORBIDDEN
