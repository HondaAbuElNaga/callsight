"""Yeastar P-Series OpenAPI adapter (read-only).

Facts (docs/pbx-discovery.md): token lives 30 min; every request needs a User-Agent; repeated
failed logins get the IP auto-blocked, so we stop after `max_login_failures` failures in a row.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
import ssl
from typing import Any, Callable

import httpx

from .authblock import AuthGuard
from .base import AuthBlocked, AuthError, PbxAdapter, PbxError, PbxUnavailable, TokenLimit

log = logging.getLogger(__name__)

USER_AGENT = "OpenAPI"
API_PATH = "/openapi/v1.0"
TOKEN_MAX_AGE = 25 * 60          # refresh before the 30 min expiry
TOKEN_LIMIT_ERRCODE = 60002      # "MAX LIMITATION EXCEEDED": more than 8 valid tokens for this API app
PAGE_SIZE = 100
CHANNEL_RE = re.compile(r"[A-Za-z0-9/_.:@\-]{1,100}")   # e.g. PJSIP/2000-00000114 (from event 30011)


def safe_recording_name(file: str) -> str:
    """Only a plain file name is accepted (no folders, no '..'), so it can't escape the audio folder."""
    name = (file or "").strip()
    if (not name or name in (".", "..") or PurePosixPath(name).name != name
            or PureWindowsPath(name).name != name or "\x00" in name):
        raise PbxError("unsafe recording file name")   # the name holds the caller's number: never logged
    return name


class YeastarAdapter(PbxAdapter):
    def __init__(self, base_url: str, client_id: str, client_secret: str, verify_ssl: bool | ssl.SSLContext = True,
                 http: httpx.Client | None = None, max_login_failures: int = 3, retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 max_pages: int = 300, cert_sha256: str | None = None,
                 check_pin: Callable[[str, str], None] | None = None, guard: AuthGuard | None = None,
                 listen_enabled: bool = False, login_gate: Callable[[], None] | None = None):
        self.base_url = base_url.rstrip("/")
        self._id, self._secret = client_id, client_secret
        self.http = http or httpx.Client(verify=verify_ssl, timeout=60, headers={"User-Agent": USER_AGENT})
        self.cert_sha256 = cert_sha256
        if check_pin is None:
            from .tls import check_pin
        self._check_pin = check_pin
        self.guard = guard                   # shared pbx:auth_blocked switch (Redis); None = in-process limit only
        self.listen_enabled = listen_enabled  # 2nd gate for call/listen: True only in the API when LISTEN_ENABLED=true
        self._listen_lock = threading.Lock()  # the API answers in threads; one Listen request to the PBX at a time
        self.login_gate = login_gate         # live service: raises when a login is not allowed yet (1 per 10 min)
        self.max_login_failures = max_login_failures
        self.login_failures = 0
        self.retries = retries
        self._sleep, self._clock = sleep, clock
        self.max_pages = max_pages
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._token_at = 0.0

    # ---------- HTTP plumbing ----------

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}{API_PATH}/{endpoint}"

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        """HTTP with retries + back-off on network errors and 5xx."""
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self.http.request(method, url, **kw)
                if resp.status_code < 500:
                    return resp
                last = PbxError(f"HTTP {resp.status_code} from PBX")
            except httpx.TransportError as exc:
                last = exc
            if attempt < self.retries - 1:
                self._sleep(2 ** attempt)
        raise PbxUnavailable(f"PBX unreachable after {self.retries} tries: {last}")

    def _json(self, resp: httpx.Response, what: str) -> dict:
        try:
            data = resp.json()
        except ValueError as exc:
            raise PbxError(f"{what}: not JSON (HTTP {resp.status_code})") from exc
        if not isinstance(data, dict):
            raise PbxError(f"{what}: unexpected response")
        return data

    def _login(self) -> None:
        if self.login_failures >= self.max_login_failures:
            raise AuthBlocked(f"stopped after {self.login_failures} failed logins; check Client ID/Secret "
                              "and the PBX API IP allow-list, then restart")
        if self.guard is not None and self.guard.blocked():
            raise AuthBlocked("PBX logins are blocked (pbx:auth_blocked) after a refused login or the token limit; "
                              "fix the cause, then run: python -m callsight pbx-unblock")
        if self.login_gate is not None:
            self.login_gate()
        if self.cert_sha256:                     # pinned: compare the certificate BEFORE sending the secret
            self._check_pin(self.base_url, self.cert_sha256)
        data = self._json(self._request("POST", self._url("get_token"),
                                        json={"username": self._id, "password": self._secret}), "get_token")
        if data.get("errcode") == TOKEN_LIMIT_ERRCODE:
            if self.guard is not None:
                self.guard.block("errcode 60002: token limit (max 8 open API logins)")
            # Not a wrong password: the PBX allows max 8 valid tokens per API app (30 min each).
            raise TokenLimit("PBX token limit reached (max 8 open API logins). Wait 30 minutes; "
                             "check no other program uses the same API Client ID.")
        if data.get("errcode") != 0:
            self.login_failures += 1
            if self.guard is not None:
                self.guard.block(f"get_token refused: errcode={data.get('errcode')}")
            raise AuthError(f"get_token refused: errcode={data.get('errcode')} errmsg={data.get('errmsg')}")
        self.login_failures = 0
        self._set_tokens(data)

    def _set_tokens(self, data: dict) -> None:
        self.access_token, self.refresh_token = data["access_token"], data.get("refresh_token")
        self._token_at = self._clock()

    def _ensure_token(self) -> None:
        if not self.access_token:
            self._login()
            return
        if self._clock() - self._token_at < TOKEN_MAX_AGE:
            return
        if self.refresh_token:
            data = self._json(self._request("POST", self._url("refresh_token"),
                                            json={"refresh_token": self.refresh_token}), "refresh_token")
            if data.get("errcode") == 0:
                self._set_tokens(data)
                return
        self.access_token = None
        self._login()

    def token(self) -> str:
        """A valid access token (login / refresh only when needed). Used by the live service's WebSocket."""
        self._ensure_token()
        if not self.access_token:  # pragma: no cover - _ensure_token raises instead
            raise AuthError("no access token")
        return self.access_token

    def token_state(self) -> dict | None:
        """Tokens + their age, so a restarted live service can reuse them instead of logging in again."""
        if not self.access_token:
            return None
        return {"access_token": self.access_token, "refresh_token": self.refresh_token,
                "age": max(0.0, self._clock() - self._token_at)}

    def restore_tokens(self, access_token: str, refresh_token: str | None, age: float) -> None:
        self.access_token, self.refresh_token = access_token, refresh_token
        self._token_at = self._clock() - age

    def force_refresh(self) -> None:
        """The next token() refreshes (refresh_token first; a new login only if that fails)."""
        self._token_at = self._clock() - TOKEN_MAX_AGE

    def listen(self, monitor_ext: str, channel_id: str) -> dict:
        """Silent listen (POST call/listen, type "listen": the monitor hears both sides, nobody hears the monitor).
        The PBX rings `monitor_ext`; `channel_id` is the monitored agent's channel from event 30011. Second gate:
        refuses unless this adapter was built with listen_enabled=True (the API does so only while
        LISTEN_ENABLED=true). Sent ONCE (no retry: a repeat would ring the supervisor's phone twice)."""
        if not self.listen_enabled:
            raise PbxError("listen is switched off in the PBX adapter")
        if not (isinstance(monitor_ext, str) and monitor_ext.isdigit() and 1 <= len(monitor_ext) <= 6):
            raise PbxError("listen: bad monitor extension")
        if not (isinstance(channel_id, str) and CHANNEL_RE.fullmatch(channel_id)):
            raise PbxError("listen: bad channel id")
        body = {"monitor": monitor_ext, "channel_id": channel_id, "type": "listen"}
        with self._listen_lock:
            for attempt in (1, 2):
                self._ensure_token()
                try:
                    resp = self.http.request("POST", self._url("call/listen"),
                                             params={"access_token": self.access_token}, json=body)
                except httpx.TransportError as exc:
                    raise PbxUnavailable(f"call/listen: PBX unreachable ({type(exc).__name__})") from None
                data = self._json(resp, "call/listen")
                if data.get("errcode") == 0:
                    return data
                if attempt == 1 and "TOKEN" in str(data.get("errmsg", "")).upper():
                    self.access_token = None      # expired token: log in again once (nothing was rung)
                    continue
                raise PbxError(f"call/listen refused: errcode={data.get('errcode')} errmsg={data.get('errmsg')}")
        raise PbxError("call/listen failed")  # pragma: no cover

    def get(self, endpoint: str, **params: Any) -> dict:
        for attempt in (1, 2):
            self._ensure_token()
            data = self._json(self._request("GET", self._url(endpoint),
                                            params={**params, "access_token": self.access_token}), endpoint)
            if data.get("errcode") == 0:
                return data
            if attempt == 1 and "TOKEN" in str(data.get("errmsg", "")).upper():
                self.access_token = None      # expired/invalid token: log in again once
                continue
            raise PbxError(f"{endpoint} failed: errcode={data.get('errcode')} errmsg={data.get('errmsg')}")
        raise PbxError(f"{endpoint} failed")  # pragma: no cover

    # ---------- CDR ----------

    def _pages(self, max_pages: int | None = None):
        """Newest-first pages of cdr/list. max_pages=None: until the caller stops or the PBX runs out."""
        limit = self.max_pages if max_pages is None else max_pages
        for page in range(1, limit + 1):
            batch = self.get("cdr/list", page=page, page_size=PAGE_SIZE, sort_by="id", order_by="desc").get("data") or []
            if not batch:
                return
            yield batch
            if len(batch) < PAGE_SIZE:
                return
        raise PbxError(f"cdr/list: more than {limit} pages; narrow the range")

    def fetch_cdr_since(self, last_id: int, overlap: int = 300) -> list[dict]:
        floor = max(0, last_id - overlap)
        rows: list[dict] = []
        for batch in self._pages():
            rows.extend(r for r in batch if int(r["id"]) > floor)
            if min(int(r["id"]) for r in batch) <= floor:
                break
        return rows

    def fetch_cdr_between(self, start: datetime, end: datetime) -> list[dict]:
        lo, hi = int(start.timestamp()), int(end.timestamp())
        rows: list[dict] = []
        # Stops on the timestamp check below, so the page cap only guards against a PBX that never
        # returns older rows: allow ~2,000 rows/day for the requested range.
        days = max(1, (int(datetime.now().timestamp()) - lo) // 86400 + 1)
        for batch in self._pages(max_pages=max(self.max_pages, 20 * days)):
            rows.extend(r for r in batch if lo <= int(r.get("timestamp") or 0) < hi)
            # ids grow with write time; once a whole page is older than `start` (with a 1 h margin for
            # long calls written late), stop.
            if max(int(r.get("timestamp") or 0) for r in batch) < lo - 3600:
                break
        return rows

    # ---------- recordings ----------

    def download_recording(self, file: str, dest: Path) -> Path:
        safe_recording_name(file)
        info = self.get("recording/download", file=file)
        url_path = info.get("download_resource_url") or ""
        if not url_path.startswith("/"):
            raise PbxError("recording/download: no download URL")
        tmp = dest.with_name(dest.name + ".part")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with self.http.stream("GET", f"{self.base_url}{url_path}",
                                  params={"access_token": self.access_token}) as r:
                if r.status_code != 200:
                    raise PbxError(f"recording download HTTP {r.status_code}")
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            tmp.replace(dest)
        except httpx.HTTPError as exc:
            raise PbxError(f"recording download failed: {exc}") from exc
        except OSError as exc:              # disk full, no permission on the audio folder, ...
            raise PbxError(f"cannot write recording to disk: {exc}") from exc
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:                 # Linux: parent is not a folder -> ENOTDIR; the PbxError above stands
                pass
        return dest

    # ---------- agents ----------

    def list_queues(self) -> list[dict]:
        out = []
        for q in self.get("queue/list").get("queue_list") or []:
            agents = [{"ext": str(a.get("text2")), "name": a.get("text") or ""}
                      for a in (q.get("static_agent_list") or []) + (q.get("dynamic_agent_list") or [])
                      if a.get("text2")]
            out.append({"number": str(q.get("number")), "name": q.get("name") or "", "agents": agents})
        return out

    def list_extensions(self) -> list[dict]:
        out: list[dict] = []
        for page in range(1, self.max_pages + 1):
            data = self.get("extension/list", page=page, page_size=PAGE_SIZE)
            batch = data.get("data") or []
            out.extend({"ext": str(e.get("number")), "name": e.get("caller_id_name") or ""} for e in batch)
            if len(batch) < PAGE_SIZE or len(out) >= int(data.get("total_number") or 0):
                break
        return out

    def close(self) -> None:
        if self.access_token:
            try:
                data = self.http.get(self._url("del_token"), params={"access_token": self.access_token}).json()
                if data.get("errcode") != 0:
                    log.warning("del_token failed: errcode=%s errmsg=%s", data.get("errcode"), data.get("errmsg"))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("del_token failed: %s", exc)
            self.access_token = self.refresh_token = None
