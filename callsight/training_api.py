"""The training system (training.sstli.com, the company's own system; spec 2026-11-16-priority-no-followup, "Part 4:
the training lookup"): who is the student calling us, which branch / diploma / section, and the responsible
trainer(s). Read only. The forward form uses it to suggest the person who should solve the problem: the trainer when
he is in the forward list, else the supervisor of the student's branch (the agent can always change it).

Settings (server .env, never in the browser): TRAINING_API_URL, TRAINING_API_KEY (X-Api-Key). Empty key = off.
The answer is never stored: it is shown on the form and kept in memory for a few minutes only.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import httpx

DEFAULT_URL = "https://training.sstli.com/api/v1"
TIMEOUT = 8.0                      # the form waits for this box only; it never blocks saving
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
    for prefix in ("00966", "966"):
        if d.startswith(prefix):
            d = "0" + d[len(prefix):]
            break
    if len(d) == 9 and d.startswith("5"):
        d = "0" + d
    return d if len(d) == 10 and d.startswith("05") else None


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

    def students(self, phone: str) -> list[dict]:
        """The students with this phone (05xxxxxxxx). Answers are kept CACHE_SECONDS in memory (the form is opened
        again and again; the other side allows 120 requests a minute)."""
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(phone)
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        try:
            with httpx.Client(base_url=self.cfg.url, timeout=TIMEOUT, transport=self._transport,
                              headers={"X-Api-Key": self.cfg.key, "Accept": "application/json"}) as http:
                r = http.get("/students/responsible-trainer", params={"q": phone})
        except httpx.HTTPError as exc:
            raise TrainingUnavailable(type(exc).__name__) from None      # never the URL: it holds the number
        if r.status_code != 200:
            raise TrainingUnavailable(f"HTTP {r.status_code}")
        try:
            got = parse(r.json())
        except ValueError:
            raise TrainingUnavailable("answer is not JSON") from None
        with self._lock:
            if len(self._cache) >= CACHE_MAX:
                self._cache = {k: v for k, v in self._cache.items() if now - v[0] < CACHE_SECONDS}
                if len(self._cache) >= CACHE_MAX:
                    self._cache.clear()
            self._cache[phone] = (now, got)
        return got
