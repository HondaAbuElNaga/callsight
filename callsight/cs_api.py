"""Client of the customer-service system's API (the company's own system at api4.sstli.com; spec
2026-11-16-priority-no-followup, part 4, "API version 2"). Only the worker and the CLI use it: the key lives in the
server .env and never reaches the browser. Settings: CS_API_URL, CS_API_KEY (X-Api-Key), CS_USER_GUID (the "PBX
account" every request is recorded under). An empty key = part 4 is off.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_URL = "https://api4.sstli.com/api/customer-service"
TIMEOUT = 20.0
log = logging.getLogger(__name__)


def _snippet(r: httpx.Response) -> str:
    """The start of an error answer, one line, for the log (their error text; never our key: it is only sent in a
    header)."""
    try:
        return " ".join(r.text.split())[:400]
    except Exception:  # noqa: BLE001
        return "(body not readable)"


class CsError(Exception):
    """The other system answered with an error. status = HTTP code, message = its `message` text (shown to users)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status, self.message = status, message


class CsAuthError(CsError):
    """401 / 403: bad key, path not allowed for the key, or the PBX account lost its rights. Retrying cannot help
    until someone fixes the key, so the send job stops for this run."""


class CsUnavailable(Exception):
    """Network error, timeout, 5xx, 408 or 429: try again later. status = the HTTP code when there was one; 408 / 429
    mean the request was NOT processed (safe to send again), the others may have been."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class CsConfig:
    url: str
    key: str
    user_guid: str

    @property
    def enabled(self) -> bool:
        return bool(self.key and self.user_guid)

    @classmethod
    def from_env(cls) -> "CsConfig":
        return cls(url=(os.getenv("CS_API_URL") or DEFAULT_URL).rstrip("/"), key=os.getenv("CS_API_KEY", "").strip(),
                   user_guid=os.getenv("CS_USER_GUID", "").strip())


def enabled_from_env() -> bool:
    return CsConfig.from_env().enabled


class CsClient:
    def __init__(self, cfg: CsConfig, transport: httpx.BaseTransport | None = None):
        if not cfg.url.startswith("https://"):
            raise ValueError("CS_API_URL must be an https:// address")
        self.cfg = cfg
        self._http = httpx.Client(base_url=cfg.url, timeout=TIMEOUT, transport=transport,
                                  headers={"X-Api-Key": cfg.key, "Accept": "application/json"})

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "CsClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _call(self, method: str, path: str, **kw) -> Any:
        t0 = time.monotonic()
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            log.warning("cs api %s %s failed after %.1fs: %s", method, path, time.monotonic() - t0, type(exc).__name__)
            raise CsUnavailable(type(exc).__name__) from None      # never the URL: no key in logs
        took = time.monotonic() - t0
        if r.status_code >= 400:   # the exact answer, so a failed forward can be explained (2026-11-23)
            log.warning("cs api %s %s -> HTTP %s in %.1fs: %s", method, path, r.status_code, took, _snippet(r))
        if r.status_code >= 500 or r.status_code in (408, 429):      # busy / too many requests: try later
            raise CsUnavailable(f"HTTP {r.status_code}", r.status_code)
        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) and r.status_code < 400:
            log.warning("cs api %s %s -> HTTP %s in %.1fs, answer is not a JSON object: %s", method, path,
                        r.status_code, took, _snippet(r))
        if r.status_code >= 400:
            msg = data.get("message") if isinstance(data, dict) else None
            msg = (str(msg) if msg else f"HTTP {r.status_code}")[:250]
            raise (CsAuthError if r.status_code in (401, 403) else CsError)(r.status_code, msg)
        if not isinstance(data, dict):
            raise CsUnavailable("answer is not JSON")
        return data

    # ----- the paths of the key (API version 2) -----

    def forward_targets(self) -> dict:
        return self._call("GET", "/forward-targets", params={"userGuid": self.cfg.user_guid})

    def customer_by_phone(self, phone: str) -> str:
        """accountGuid of the customer with this number; created there (name = the number) when missing."""
        d = self._call("POST", "/customers/by-phone",
                       json={"userGuid": self.cfg.user_guid, "phone": phone, "createIfMissing": True})
        guid = d.get("accountGuid")
        if not guid:
            raise CsError(400, "no accountGuid in the answer")
        return str(guid)

    def create_ticket(self, account_guid: str, notes: str, priority: int, forward_to: str) -> tuple[str, int | None]:
        """A general inquiry (callType 0, their advice for the PBX), open (close=false), forwarded. -> (guid, number)"""
        d = self._call("POST", "/tickets", json={
            "userGuid": self.cfg.user_guid, "accountGuid": account_guid, "callType": 0, "notes": notes,
            "priority": priority, "close": False, "forwardToGuid": forward_to})
        guid = str(d.get("guid") or "")
        if not guid or len(guid) > 64:          # our column is 64; never retry: the ticket may exist (review D1)
            raise CsError(400, "bad ticket guid in the answer")
        num = d.get("number")
        num = int(num) if isinstance(num, int) or (isinstance(num, str) and num.isdigit()) else None
        return guid, (num if num is not None and 0 < num < 2**31 else None)


    def get_ticket(self, guid: str) -> dict:
        """GET /tickets/{guid}: {"ticket": {..., closedAt}, "timeline": [...]} (part 4 D2). The guid came from the
        ticket answer (checked <= 64 chars) and is URL-quoted all the same."""
        return self._call("GET", f"/tickets/{quote(guid, safe='')}", params={"userGuid": self.cfg.user_guid})

    # ----- the forward chain: move / reopen / close a ticket -----

    def forward_ticket(self, guid: str, to_user_guid: str, note: str) -> dict:
        """POST /tickets/{guid}/forward: the same ticket goes to another employee."""
        return self._call("POST", f"/tickets/{quote(guid, safe='')}/forward",
                          json={"userGuid": self.cfg.user_guid, "toUserGuid": to_user_guid, "note": note[:590]})

    def reopen(self, guid: str, note: str) -> dict:
        return self._call("POST", f"/tickets/{quote(guid, safe='')}/reopen",
                          json={"userGuid": self.cfg.user_guid, "note": note[:590]})

    def followup(self, guid: str, note: str, close: bool = False) -> dict:
        """POST /tickets/{guid}/followup: a message on the ticket; close=True closes it there."""
        return self._call("POST", f"/tickets/{quote(guid, safe='')}/followup",
                          json={"userGuid": self.cfg.user_guid, "note": note[:590], "close": close})


def employees(targets: dict) -> list[dict]:
    """Every employee in the forward-targets tree: guid, name, branch, group, is_supervisor (the forward chain lets
    the agent choose any of them)."""
    out, seen = [], set()
    for g in targets.get("groups") or []:
        for b in (g or {}).get("branches") or []:
            for e in (b or {}).get("employees") or []:
                guid = str((e or {}).get("guid") or "")
                if not guid or len(guid) > 64 or guid in seen:
                    continue
                seen.add(guid)
                out.append({"guid": guid, "name": str(e.get("name") or "")[:200],
                            "branch": str(b.get("name") or "")[:100], "group": str(g.get("key") or "")[:20],
                            "is_supervisor": bool(e.get("isBranchSupervisor"))})
    return out


def supervisors(targets: dict) -> list[dict]:
    """Every employee with isBranchSupervisor = true in the forward-targets tree: guid, name, branch, group."""
    out = []
    for g in targets.get("groups") or []:
        for b in (g or {}).get("branches") or []:
            for e in (b or {}).get("employees") or []:
                if e and e.get("isBranchSupervisor") and e.get("guid"):
                    out.append({"guid": str(e["guid"]), "name": str(e.get("name") or ""),
                                "branch": str(b.get("name") or ""), "group": str(g.get("key") or "")})
    return out
