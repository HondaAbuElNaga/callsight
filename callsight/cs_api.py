"""Client of the customer-service system's API (the company's own system at api4.sstli.com; spec
2026-11-16-priority-no-followup, part 4, "API version 2"). Only the worker and the CLI use it: the key lives in the
server .env and never reaches the browser. Settings: CS_API_URL, CS_API_KEY (X-Api-Key), CS_USER_GUID (the "PBX
account" every request is recorded under). An empty key = part 4 is off.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_URL = "https://api4.sstli.com/api/customer-service"
TIMEOUT = 20.0


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
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise CsUnavailable(type(exc).__name__) from None      # never the URL: no key in logs
        if r.status_code >= 500 or r.status_code in (408, 429):      # busy / too many requests: try later
            raise CsUnavailable(f"HTTP {r.status_code}", r.status_code)
        try:
            data = r.json()
        except ValueError:
            data = None
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
