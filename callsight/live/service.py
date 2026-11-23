"""The `live` service: ONE WebSocket to the PBX -> LiveState -> Redis (spec 2026-11-14-live-board).

Off by default, double gate (decision 7): the compose service has profile `live` (not started by `up -d`) AND this
program exits 0 at once unless LIVE_ENABLED=true.

PBX safety (decision 1):
- own single login (never shares the ingest token); reuses a saved token younger than 25 min after a restart
  (saved in a 0600 file on a volume only this service mounts, NOT in Redis: every container can read Redis);
  at most 1 login per 10 min (Redis key live:login_lock); every ~25 min it refreshes with the refresh_token
  (if the PBX refuses the refresh: one gated login, still max 1 per 10 min) and reconnects the WebSocket with the
  new token; del_token on a clean stop (SIGTERM interrupts a waiting recv() at once).
- pbx:auth_blocked is checked before every login (Redis down = treated as blocked) and set on a refused login or
  errcode 60002; a failed login is NEVER retried: the service waits until a person runs `pbx-unblock`.
- PBX unreachable: exponential back-off 5 s -> 5 min with jitter.
Never logged: raw payloads, the subscribe URL (it carries ?access_token=), tokens, caller numbers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..pbx.authblock import AuthGuard
from ..pbx.base import AuthError, PbxError, PbxUnavailable, TokenLimit
from .bus import Publisher
from .events import TOPICS, CallState, parse
from .state import LiveState

log = logging.getLogger(__name__)

TOKEN_FILE_DEFAULT = "/live-state/pbx_token.json"   # LIVE_TOKEN_FILE; volume mounted only into the live service
LOGIN_LOCK_KEY = "live:login_lock"   # at most one login per LOGIN_INTERVAL
LOGIN_INTERVAL = 10 * 60
TOKEN_REUSE = 25 * 60


class LoginRateLimited(PbxUnavailable):
    def __init__(self, wait: float):
        super().__init__(f"live service: next PBX login allowed in {int(wait)} s")
        self.wait = wait


class TokenRejected(PbxError):
    """The PBX refused our token on subscribe (expired / revoked)."""


class SubscribeRefused(PbxError):
    """The PBX answered the subscribe with an errcode that is not about the token."""


class HeartbeatTimeout(PbxError):
    """Nothing received for too long: the connection is dead."""


def env_true(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Timing:
    heartbeat: float = 30.0          # Yeastar closes the socket after 60 s without a heartbeat
    idle_timeout: float = 75.0       # no message (not even "heartbeat response") for this long = dead connection
    rotate_after: float = 25 * 60    # token age at which we refresh + reconnect
    sync_grace: float = 120.0        # "syncing" ends with the first event, or after this long
    prune_every: float = 60.0
    blocked_poll: float = 60.0       # how often to look whether a person ran pbx-unblock
    subscribe_timeout: float = 15.0
    republish_every: float = 30.0    # while waiting (back-off, login gate): keep the 90 s snapshot key alive


class Backoff:
    """5 s, 10 s, 20 s ... capped at 5 min, each with +-20 % jitter so restarts don't hit the PBX together."""

    def __init__(self, base: float = 5.0, cap: float = 300.0, jitter: float = 0.2, rand=random.random):
        self.base, self.cap, self.jitter, self._rand = base, cap, jitter, rand
        self.attempt = 0

    def next(self) -> float:
        delay = min(self.cap, self.base * 2 ** self.attempt)
        self.attempt += 1
        return delay * (1 - self.jitter + 2 * self.jitter * self._rand())

    def reset(self) -> None:
        self.attempt = 0


class LoginGate:
    """Redis SET NX EX: one login per LOGIN_INTERVAL across restarts. Redis down = no login."""

    def __init__(self, redis, interval: float = LOGIN_INTERVAL):
        self.redis, self.interval = redis, interval

    def __call__(self) -> None:
        try:
            if self.redis.set(LOGIN_LOCK_KEY, "1", nx=True, ex=int(self.interval)):
                return
            ttl = self.redis.ttl(LOGIN_LOCK_KEY)
            if ttl == -1:      # a lock without expiry (set by hand / restored) would block every login forever
                self.redis.expire(LOGIN_LOCK_KEY, int(self.interval))
                ttl = int(self.interval)
        except Exception as exc:  # noqa: BLE001
            raise LoginRateLimited(60) from exc
        # ttl 0 / -2 = the lock is just expiring or already gone (the wait ended a fraction of a second early):
        # try again in 1 s instead of a new full 10 min. +1 s so the next try lands after the expiry.
        raise LoginRateLimited(ttl + 1 if isinstance(ttl, int) and ttl > 0 else 1)


class TokenStore:
    """This service's own PBX token (so a restart does not log in again), in a file readable only by this service.
    path None = not kept (tests)."""

    def __init__(self, path: Path | None, clock=time.time):
        self.path, self._clock = path, clock

    def load(self) -> dict | None:
        if self.path is None:
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - missing / unreadable: just log in later (rate limited)
            return None
        if not isinstance(data, dict) or not data.get("access_token"):
            return None
        age = self._clock() - float(data.get("saved_at", 0)) + float(data.get("age", 0))
        if not 0 <= age < TOKEN_REUSE:
            return None
        return {**data, "age": age}

    def save(self, state: dict | None) -> None:
        if not state:
            return
        if self.path is None:
            return
        data = {**state, "saved_at": self._clock()}
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.unlink(missing_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save the live PBX token (%s)", type(exc).__name__)

    def clear(self) -> None:
        if self.path is None:
            return
        try:
            self.path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def ws_url(base_url: str, token: str) -> str:
    """https://pbx:8088 -> wss://pbx:8088/openapi/v1.0/subscribe?access_token=... (NEVER log the result)."""
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/openapi/v1.0/subscribe?access_token={token}"


def ssl_context(verify: ssl.SSLContext | bool) -> ssl.SSLContext:
    if isinstance(verify, ssl.SSLContext):
        return verify
    ctx = ssl.create_default_context()
    if not verify:                     # same posture as the HTTP adapter (YEASTAR_VERIFY_SSL=false: warned at start)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


Connect = Callable[..., Any]   # websockets.connect-like: async context manager with send()/recv()


class LiveService:
    def __init__(self, adapter, redis, connect: Connect, *, guard: AuthGuard, base_url: str,
                 ssl_ctx: ssl.SSLContext | None = None, timing: Timing = Timing(), backoff: Backoff | None = None,
                 token_file: Path | None = None, team,   # TeamConfig; None = show every call (tests only)
                 wall=time.time, mono=time.monotonic, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.adapter, self.redis, self.connect, self.guard = adapter, redis, connect, guard
        self.base_url, self.ssl_ctx, self.t = base_url, ssl_ctx, timing
        self.backoff = backoff or Backoff()
        self._wall, self._mono, self._sleep = wall, mono, sleep
        self.state = LiveState(team=team)       # team only: other company calls are never published
        self.pub = Publisher(redis, clock=wall)
        self.tokens = TokenStore(token_file, clock=wall)
        self.status = "disconnected"
        self.stop = asyncio.Event()

    # ---------- publishing ----------

    def _publish(self, deltas: list[dict], status: str | None = None) -> None:
        if status:
            self.status = status
        try:
            self.pub.publish(self.state.snapshot(), deltas, self.status, self.state.channels())
        except Exception as exc:  # noqa: BLE001 - Redis hiccup: the next publish carries the full snapshot again
            log.warning("could not publish live state (%s)", type(exc).__name__)

    # ---------- token ----------

    async def _token(self) -> str:
        if self.adapter.access_token is None:
            saved = self.tokens.load()
            if saved:
                self.adapter.restore_tokens(saved["access_token"], saved.get("refresh_token"), saved["age"])
        token = await asyncio.to_thread(self.adapter.token)    # login (gated) / refresh only when needed
        self.tokens.save(self.adapter.token_state())
        return token

    # ---------- main loop ----------

    async def _wait(self, seconds: float) -> None:
        """Sleep until `seconds` passed or stop; re-publishes the status every `republish_every` so the snapshot key
        (TTL 90 s) does not expire during a long back-off / login wait (the page would say "service stopped")."""
        end = self._mono() + max(0.0, seconds)
        while not self.stop.is_set():
            rest = end - self._mono()
            if rest <= 0:
                return
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=min(rest, self.t.republish_every))
            except asyncio.TimeoutError:
                if end - self._mono() > 0:
                    self._publish([])

    async def run(self) -> None:
        self._publish([], "disconnected")
        was_blocked = False
        while not self.stop.is_set():
            if self.guard.blocked():
                was_blocked = True
                self._publish(self.state.reset(), "auth_blocked")
                await self._wait(self.t.blocked_poll)
                continue
            if was_blocked:                    # a person ran pbx-unblock: allow one login attempt again
                self.adapter.login_failures, was_blocked = 0, False
            try:
                token = await self._token()
            except LoginRateLimited as exc:
                log.info("live: waiting %d s before the next PBX login (max 1 per 10 min)", int(exc.wait))
                await self._wait(exc.wait)
                continue
            except (AuthError, TokenLimit) as exc:
                # The adapter already set pbx:auth_blocked (and counts the failure in-process): never retried.
                log.error("live: PBX login refused (%s); waiting for pbx-unblock", type(exc).__name__)
                if not self.guard.blocked():   # the key could not be written (Redis hiccup): set it now, so the
                    self.guard.block(f"live: login refused ({type(exc).__name__})")   # pbx-unblock path works
                self.adapter.access_token = None
                self.tokens.clear()
                self._publish(self.state.reset(), "auth_blocked")
                await self._wait(self.t.blocked_poll)
                continue
            except PbxError as exc:
                delay = self.backoff.next()
                log.warning("live: PBX unreachable (%s); retry in %d s", type(exc).__name__, int(delay))
                self._publish(self.state.reset(), "disconnected")
                await self._wait(delay)
                continue
            try:
                await self._session(token)
                self.backoff.reset()
            except TokenRejected:
                log.warning("live: PBX refused the token on subscribe; dropping it")
                self.adapter.access_token = self.adapter.refresh_token = None
                self.tokens.clear()
                self._publish(self.state.reset(), "disconnected")
                await self._wait(self.backoff.next())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - connection errors of any kind; str(exc) may hold the URL
                delay = self.backoff.next()
                log.warning("live: PBX WebSocket lost (%s); reconnect in %d s", type(exc).__name__, int(delay))
                self._publish(self.state.reset(), "disconnected")
                await self._wait(delay)

    async def _session(self, token: str) -> None:
        """One WebSocket connection. Returns normally when the token is due for rotation (or on stop)."""
        state = self.adapter.token_state() or {"age": 0.0}
        rotate_at = self._mono() + max(1.0, self.t.rotate_after - float(state.get("age", 0.0)))
        async with self.connect(ws_url(self.base_url, token), ssl=self.ssl_ctx if self.base_url.startswith("https")
                                else None, user_agent_header="OpenAPI", open_timeout=15) as ws:
            await ws.send(json.dumps({"topic_list": list(TOPICS)}))
            reply = await asyncio.wait_for(ws.recv(), timeout=self.t.subscribe_timeout)
            self._check_subscribe(reply)
            log.info("live: subscribed to PBX call events")
            self.backoff.reset()
            now = self._mono()
            sync_until, last_rx = now + self.t.sync_grace, now
            next_beat, next_prune = now + self.t.heartbeat, now + self.t.prune_every
            self._publish(self.state.reset(), "syncing")
            while not self.stop.is_set():
                now = self._mono()
                if now >= rotate_at:
                    self.adapter.force_refresh()      # next _token(): refresh_token (no new login), then reconnect
                    return
                wake = min(next_beat, next_prune, rotate_at, last_rx + self.t.idle_timeout + 0.01,
                           sync_until if self.status == "syncing" else next_beat)
                msg = await self._recv_or_stop(ws, max(0.01, wake - now))
                if self.stop.is_set():
                    return
                now = self._mono()
                if msg is not None:
                    last_rx = now
                    self._handle(msg)
                if self.status == "syncing" and now >= sync_until:
                    self._publish([], "connected")
                if now >= next_beat:
                    await ws.send("heartbeat")
                    next_beat = now + self.t.heartbeat
                    self._publish([])                  # keeps the snapshot key alive
                if now >= next_prune:
                    next_prune = now + self.t.prune_every
                    gone = self.state.prune(self._wall())
                    if gone:
                        self._publish(gone)
                if now - last_rx > self.t.idle_timeout:
                    raise HeartbeatTimeout("no message from the PBX")

    async def _recv_or_stop(self, ws, timeout: float) -> Any:
        """ws.recv() that a stop (SIGTERM) interrupts at once: Docker kills after the grace period, and shutdown()
        must still run del_token. None on timeout / stop; connection errors are raised."""
        recv = asyncio.ensure_future(ws.recv())
        stopper = asyncio.ensure_future(self.stop.wait())
        try:
            done, _ = await asyncio.wait({recv, stopper}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopper.cancel()
        if recv in done:
            return recv.result()
        recv.cancel()
        try:
            await recv
        except BaseException:  # noqa: BLE001 - cancelled / closed: nothing to read
            pass
        return None

    def _check_subscribe(self, reply: Any) -> None:
        try:
            data = json.loads(reply)
        except (TypeError, ValueError):
            raise SubscribeRefused("subscribe: answer is not JSON") from None
        code = data.get("errcode") if isinstance(data, dict) else None
        if code == 0:
            return
        if "TOKEN" in str(data.get("errmsg", "") if isinstance(data, dict) else "").upper():
            raise TokenRejected(f"subscribe refused: errcode={code}")
        raise SubscribeRefused(f"subscribe refused: errcode={code}")

    def _handle(self, msg: Any) -> None:
        if msg == "heartbeat response":
            return
        event = parse(msg)              # masks caller numbers; the raw message is never logged or stored
        if event is None:
            try:                        # only the event type, never the content
                d = json.loads(msg) if isinstance(msg, (str, bytes)) else msg
                log.info("live: event not used (type %s)", d.get("type") if isinstance(d, dict) else type(d).__name__)
            except Exception:  # noqa: BLE001
                log.info("live: event not used (not JSON)")
            return
        if isinstance(event, CallState):   # call id, member kinds, extension numbers, states: no caller numbers
            log.info("live: call %s members %s", event.call_id,
                     [f"{m.kind}:{m.ext or '-'}:{m.status or '-'}" for m in event.members])
        deltas = self.state.apply(event, self._wall())
        if isinstance(event, CallState) and not deltas:
            log.info("live: call %s not shown (not the team's, or nothing changed)", event.call_id)
        self._publish(deltas, "connected" if self.status == "syncing" else None)

    async def shutdown(self) -> None:
        """Clean stop: del_token (frees one of the PBX's 8 tokens) and "no live data" on the page."""
        try:
            await asyncio.to_thread(self.adapter.close)
        except Exception as exc:  # noqa: BLE001
            log.warning("live: del_token failed (%s)", type(exc).__name__)
        self.tokens.clear()
        try:
            self.pub.clear()
        except Exception:  # noqa: BLE001
            pass


def build_service() -> LiveService:
    """Real wiring (only reached with LIVE_ENABLED=true)."""
    import websockets

    from .. import app as wiring
    from ..config import load_teams
    from ..pbx.tls import verify_setting
    from ..pbx.yeastar import YeastarAdapter
    s = wiring.settings()
    s.require_pbx()
    redis = wiring.redis_client(timeout=5)
    guard = AuthGuard(redis, "live", fail_closed=True)
    verify = verify_setting(s.yeastar_ca_file, s.yeastar_verify_ssl, s.yeastar_check_hostname,
                            cert_sha256=s.yeastar_cert_sha256)
    adapter = YeastarAdapter(s.yeastar_base_url, s.yeastar_client_id, s.yeastar_client_secret, verify_ssl=verify,
                             cert_sha256=s.yeastar_cert_sha256, guard=guard, login_gate=LoginGate(redis),
                             max_login_failures=1)
    return LiveService(adapter, redis, websockets.connect, guard=guard, base_url=s.yeastar_base_url,
                       ssl_ctx=ssl_context(verify),
                       token_file=Path(os.getenv("LIVE_TOKEN_FILE", TOKEN_FILE_DEFAULT)),
                       team=load_teams(s.teams_file))


def main(enabled: Callable[[], bool] = lambda: env_true("LIVE_ENABLED"),
         build: Callable[[], LiveService] = build_service) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "httpcore", "websockets"):       # their debug lines can carry URLs with the token
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if not enabled():
        print("live board is off (LIVE_ENABLED is not true): nothing to do, no PBX connection.")
        return 0
    svc = build()

    async def runner() -> None:
        import signal
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, svc.stop.set)
            except (NotImplementedError, RuntimeError):   # Windows: Ctrl+C raises KeyboardInterrupt instead
                pass
        try:
            await svc.run()
        finally:
            await svc.shutdown()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
    return 0
