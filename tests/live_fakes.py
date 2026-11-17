"""Fakes for the live-board tests: Redis, PBX WebSocket, PBX HTTP. No network, no PBX contact."""
from __future__ import annotations

import asyncio
import json
import queue
from pathlib import Path

import httpx

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "live_events_synthetic.json").read_text(encoding="utf-8"))


class FakeRedis:
    """The few sync Redis commands the code uses. ttl/expiry are recorded, not enforced (tests move time by hand)."""

    def __init__(self, down: bool = False):
        self.data: dict[str, object] = {}
        self.ttls: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []
        self.down = down

    def _check(self):
        if self.down:
            raise ConnectionError("redis down")

    def get(self, k):
        self._check()
        return self.data.get(k)

    def set(self, k, v, nx=False, ex=None):
        self._check()
        if nx and k in self.data:
            return None
        self.data[k] = v
        if ex is not None:
            self.ttls[k] = ex
        return True

    def delete(self, *ks):
        self._check()
        n = 0
        for k in ks:
            n += self.data.pop(k, None) is not None
            self.ttls.pop(k, None)
        return n

    def exists(self, k):
        self._check()
        return int(k in self.data)

    def ttl(self, k):
        self._check()
        return self.ttls.get(k, -2 if k not in self.data else -1)

    def expire(self, k, seconds):
        self._check()
        if k not in self.data:
            return False
        self.ttls[k] = seconds
        return True

    def publish(self, ch, msg):
        self._check()
        self.published.append((ch, msg))
        return 1

    def pipeline(self):
        return FakePipe(self)

    def messages(self) -> list[dict]:
        return [json.loads(m) for _, m in self.published]


class FakePipe:
    def __init__(self, r: FakeRedis):
        self.r, self.ops = r, []

    def __getattr__(self, name):
        def op(*a, **kw):
            self.ops.append((name, a, kw))
            return self
        return op

    def execute(self):
        self.r._check()
        return [getattr(self.r, n)(*a, **kw) for n, a, kw in self.ops]


class FakeSource:
    """Live source for the API WebSocket; tests push messages from the test thread (thread-safe queue)."""

    def __init__(self, snap: dict | None = None, last_seen: float | None = None):
        self.snap, self.seen = snap, last_seen
        self.q: queue.Queue = queue.Queue()

    async def snapshot(self):
        return self.snap

    async def last_seen(self):
        return self.seen

    async def messages(self):
        while True:
            try:
                yield self.q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.005)


class WsClosed(Exception):
    """Stands in for websockets.ConnectionClosed (the service treats any exception as a lost connection)."""


class FakeWs:
    """Scripted PBX WebSocket. Script items: str (a message), Exception (raised by recv), float (stall that long)."""

    def __init__(self, script: list, subscribe_reply: str = '{"errcode":0,"errmsg":"SUCCESS"}'):
        self.script = [subscribe_reply, *script]
        self.sent: list[str] = []
        self._quiet_until: float | None = None

    async def send(self, text):
        self.sent.append(text)

    async def recv(self):
        """A float in the script = silence for that long (survives recv() being cancelled by a timeout)."""
        loop = asyncio.get_running_loop()
        while True:
            if self._quiet_until is not None:
                rest = self._quiet_until - loop.time()
                if rest > 0:
                    await asyncio.sleep(rest)
                self._quiet_until = None
            if not self.script:
                raise WsClosed("closed")
            item = self.script.pop(0)
            if isinstance(item, float):
                self._quiet_until = loop.time() + item
                continue
            if isinstance(item, Exception):
                raise item
            return item


class FakeConnect:
    """websockets.connect stand-in: one FakeWs per connection, records every URL (tests only check them)."""

    def __init__(self, sockets: list, on_empty=None):
        self.sockets, self.urls, self.kwargs, self.on_empty = list(sockets), [], [], on_empty

    def __call__(self, url, **kw):
        self.urls.append(url)
        self.kwargs.append(kw)
        conn = self

        class Ctx:
            async def __aenter__(self_inner):
                if not conn.sockets:
                    if conn.on_empty:
                        conn.on_empty()
                    raise OSError("connection refused")
                item = conn.sockets.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

            async def __aexit__(self_inner, *a):
                return False
        return Ctx()


class FakePbxHttp:
    """httpx transport for the Yeastar adapter: counts logins, answers with the given errcodes in turn."""

    def __init__(self, login_codes: list[int] | None = None, refresh_ok: bool = True):
        self.login_codes = list(login_codes or [0])
        self.refresh_ok = refresh_ok
        self.calls: list[str] = []
        self.n_tok = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(path)
        if path == "get_token":
            code = self.login_codes.pop(0) if self.login_codes else 0
            if code:
                return httpx.Response(200, json={"errcode": code, "errmsg": "FAILED"})
            self.n_tok += 1
            return httpx.Response(200, json={"errcode": 0, "access_token": f"tok{self.n_tok}",
                                             "refresh_token": f"ref{self.n_tok}"})
        if path == "refresh_token":
            if not self.refresh_ok:
                return httpx.Response(200, json={"errcode": 10004, "errmsg": "INVALID"})
            self.n_tok += 1
            return httpx.Response(200, json={"errcode": 0, "access_token": f"tok{self.n_tok}",
                                             "refresh_token": f"ref{self.n_tok}"})
        if path == "del_token":
            return httpx.Response(200, json={"errcode": 0})
        return httpx.Response(404, json={"errcode": 1})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), base_url="https://pbx.invalid")

    @property
    def logins(self) -> int:
        return self.calls.count("get_token")
