"""The training system (training.sstli.com, the company's own system; spec 2026-11-16-priority-no-followup, "Part 4:
the training lookup"): who is the student calling us, which branch / diploma / section, and the responsible
trainer(s). Read only. The forward form uses it to suggest the person who should solve the problem: the trainer when
he is in the forward list, else the supervisor of the student's branch (the agent can always change it).

Settings (server .env, never in the browser): TRAINING_API_URL, TRAINING_API_KEY (X-Api-Key). Empty key = off.
The answer is never stored: it is shown on the form and kept in memory for a few minutes only.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass

import httpx

logging.getLogger("httpx").setLevel(logging.WARNING)     # its INFO line holds the URL, so the phone (T4)

DEFAULT_URL = "https://training.sstli.com/api/v1"
TIMEOUT = 8.0                      # whole request, however slowly the answer comes (review)
CONNECT_TIMEOUT = 3.0
MAX_BYTES = 256_000                # a bigger answer is refused (the real one is a few KB)
FAIL_SECONDS = 60                  # after a failure: answer "unavailable" at once for a minute (no 8 s waits)
MAX_RUNNING = 4                    # look-ups at the same time; more = "unavailable" (API threads stay free)
PER_MINUTE = 60                    # all look-ups together (the other side allows 120 a minute)
CACHE_SECONDS = 600
CACHE_MAX = 500
MAX_STUDENTS = 5                   # one number -> a few students at most (siblings on one phone)
MAX_PLACEMENTS = 5
MAX_TRAINERS = 10
# a trainer item: its fields are not known yet (all answers so far had `trainers: []`), so any of these is the name
TRAINER_NAME_KEYS = ("name", "full_name", "fullName", "trainer", "trainer_name", "trainerName")


class TrainingUnavailable(Exception):
    """No usable answer (network, timeout, HTTP error, bad key, not JSON). The message never holds the number."""


@dataclass(frozen=True)
class TrainingConfig:
    url: str
    key: str

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    @classmethod
    def from_env(cls) -> "TrainingConfig":
        return cls(url=(os.getenv("TRAINING_API_URL") or DEFAULT_URL).strip().rstrip("/"),
                   key=os.getenv("TRAINING_API_KEY", "").strip())


def local_mobile(number: str | None) -> str | None:
    """The caller number as the training system writes phones (05xxxxxxxx), or None when it is not a Saudi mobile
    (a landline or an extension: no look-up)."""
    d = "".join(ch for ch in (number or "") if ch.isdigit())
    if len(d) == 11 and d.startswith("905"):          # a trunk digit before the number
        d = d[1:]
    for prefix in ("00966", "0966", "966"):
        if d.startswith(prefix):
            d = "0" + d[len(prefix):]
            break
    if len(d) == 9 and d.startswith("5"):
        d = "0" + d
    return d if len(d) == 10 and d.startswith("05") else None


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def search_key(typed: str | None) -> str | None:
    """What the agent typed when the caller's number finds no student: a national ID / iqama (10 digits starting
    1 or 2) or the student's registered mobile. Arabic digits, spaces and dashes are fine. Anything else = None."""
    raw = (typed or "").translate(_AR_DIGITS).strip()
    if len(raw) > 20 or any(not (ch.isascii() and ch.isdigit()) and ch not in " -+" for ch in raw):
        return None
    d = "".join(ch for ch in raw if ch.isdigit())
    if len(d) == 10 and d[0] in "12":
        return d
    return local_mobile(d)


def _text(v, n: int = 200) -> str:
    return str(v).strip()[:n] if isinstance(v, (str, int)) and str(v).strip() else ""


def _trainer(t) -> str:
    if isinstance(t, str):
        return _text(t)
    if isinstance(t, dict):
        for k in TRAINER_NAME_KEYS:
            if _text(t.get(k)):
                return _text(t.get(k))
    return ""


def parse(data) -> list[dict]:
    """{data: [{student, placements: [{branch, diploma, level, section, status, trainers}]}]} -> plain lists of text
    (bad or missing parts dropped, sizes capped)."""
    rows = data.get("data") if isinstance(data, dict) else None
    out = []
    for st in (rows if isinstance(rows, list) else [])[:MAX_STUDENTS]:
        if not isinstance(st, dict):
            continue
        places = []
        for p in (st.get("placements") if isinstance(st.get("placements"), list) else [])[:MAX_PLACEMENTS]:
            if not isinstance(p, dict):
                continue
            trainers = p.get("trainers") if isinstance(p.get("trainers"), list) else []
            names = [n for n in (_trainer(t) for t in trainers[:MAX_TRAINERS]) if n]
            places.append({"branch": _text(p.get("branch")), "diploma": _text(p.get("diploma")),
                           "level": _text(p.get("level"), 50), "section": _text(p.get("section"), 50),
                           "status": _text(p.get("status"), 50), "trainers": list(dict.fromkeys(names))})
        out.append({"student": _text(st.get("student")), "placements": places})
    return out


class TrainingClient:
    def __init__(self, cfg: TrainingConfig, transport: httpx.BaseTransport | None = None):
        if not cfg.url.startswith("https://"):
            raise ValueError("TRAINING_API_URL must be an https:// address")
        self.cfg = cfg
        self._transport = transport
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()
        self._running = threading.BoundedSemaphore(MAX_RUNNING)
        self._failed_at: float | None = None
        self._sent: list[float] = []              # times of the requests of the last minute

    def _may_send(self, now: float) -> bool:
        with self._lock:
            if self._failed_at is not None and now - self._failed_at < FAIL_SECONDS:
                return False
            self._sent = [x for x in self._sent if now - x < 60]
            if len(self._sent) >= PER_MINUTE:
                return False
            self._sent.append(now)
            return True

    def _fetch(self, phone: str) -> list[dict]:
        deadline = time.monotonic() + TIMEOUT
        with httpx.Client(base_url=self.cfg.url, transport=self._transport,
                          timeout=httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT),
                          headers={"X-Api-Key": self.cfg.key, "Accept": "application/json"}) as http:
            with http.stream("GET", "/students/responsible-trainer", params={"q": phone}) as r:
                if r.status_code != 200:
                    raise TrainingUnavailable(f"HTTP {r.status_code}")
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise TrainingUnavailable("answer too big")
                    if time.monotonic() > deadline:
                        raise TrainingUnavailable("too slow")
        try:
            return parse(json.loads(bytes(body)))
        except ValueError:
            raise TrainingUnavailable("answer is not JSON") from None

    def students(self, phone: str) -> list[dict]:
        """The students with this phone (05xxxxxxxx). Answers are kept CACHE_SECONDS in memory (the form is opened
        again and again); a failure, too many look-ups at once or in the minute = TrainingUnavailable at once."""
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(phone)
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        if not self._may_send(now):
            raise TrainingUnavailable("paused")
        if not self._running.acquire(blocking=False):
            raise TrainingUnavailable("busy")
        try:
            got = self._fetch(phone)
        except (httpx.HTTPError, TrainingUnavailable) as exc:
            with self._lock:
                self._failed_at = time.monotonic()
            if isinstance(exc, TrainingUnavailable):
                raise
            raise TrainingUnavailable(type(exc).__name__) from None      # never the URL: it holds the number
        finally:
            self._running.release()
        with self._lock:
            self._failed_at = None
            if len(self._cache) >= CACHE_MAX:
                self._cache = {k: v for k, v in self._cache.items() if now - v[0] < CACHE_SECONDS}
                if len(self._cache) >= CACHE_MAX:
                    self._cache.clear()
            self._cache[phone] = (now, got)
        return got
