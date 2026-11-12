"""Read-only probe for the Yeastar P-Series OpenAPI (Phase 0: PBX discovery).

Usage:
    python tools/pbx_probe.py token
    python tools/pbx_probe.py cdr --days 7
    python tools/pbx_probe.py recordings --limit 10
    python tools/pbx_probe.py recording --latest
    python tools/pbx_probe.py events --seconds 120

Settings come from .env (see .env.example). The probe never changes PBX settings.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"
USER_AGENT = "OpenAPI"  # Yeastar requires a User-Agent header on every request
API_PATH = "/openapi/v1.0"

# Phone numbers of 7+ digits are masked in saved samples (extensions stay readable).
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d{3})\d{2,}(\d{2})(?!\d)")
_TIME_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


class ProbeError(RuntimeError):
    pass


# ---------- helpers (pure, unit-tested) ----------

def mask_phone(value: Any) -> Any:
    """Mask long digit runs (customer numbers) in strings, recursively in dicts/lists."""
    if isinstance(value, str):
        return _PHONE_RE.sub(lambda m: m.group(1) + "*" * (len(m.group(0)) - len(m.group(1)) - 2) + m.group(2), value)
    if isinstance(value, dict):
        return {k: mask_phone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_phone(v) for v in value]
    return value


def parse_pbx_time(value: Any) -> datetime | None:
    """Parse the PBX time field; returns None if unknown format."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    if not isinstance(value, str):
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def check(resp_json: dict, what: str) -> dict:
    if resp_json.get("errcode") != 0:
        raise ProbeError(f"{what} failed: errcode={resp_json.get('errcode')} errmsg={resp_json.get('errmsg')}")
    return resp_json


def describe_wav(path: Path) -> str:
    try:
        with wave.open(str(path), "rb") as w:
            ch, rate, width, frames = w.getnchannels(), w.getframerate(), w.getsampwidth(), w.getnframes()
        kind = "STEREO (agent/customer may be split)" if ch == 2 else "MONO (voices mixed -> diarization needed)"
        return f"WAV {ch} channel(s), {rate} Hz, {width * 8}-bit, {frames / rate:.1f} s -> {kind}"
    except (wave.Error, EOFError) as exc:
        return f"not a plain PCM WAV ({exc}); check with ffprobe"


# ---------- client ----------

class YeastarClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, verify_ssl: bool = True,
                 http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.verify_ssl = verify_ssl
        self.http = http or httpx.Client(verify=verify_ssl, timeout=30,
                                         headers={"User-Agent": USER_AGENT})
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    @classmethod
    def from_env(cls) -> "YeastarClient":
        load_dotenv(ROOT / ".env")
        missing = [k for k in ("YEASTAR_BASE_URL", "YEASTAR_CLIENT_ID", "YEASTAR_CLIENT_SECRET") if not os.getenv(k)]
        if missing:
            raise ProbeError(f"Missing in .env: {', '.join(missing)} (copy .env.example to .env and fill it)")
        verify = os.getenv("YEASTAR_VERIFY_SSL", "true").lower() not in ("0", "false", "no")
        return cls(os.environ["YEASTAR_BASE_URL"], os.environ["YEASTAR_CLIENT_ID"],
                   os.environ["YEASTAR_CLIENT_SECRET"], verify_ssl=verify)

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}{API_PATH}/{endpoint}"

    def get_token(self) -> dict:
        data = check(self.http.post(self._url("get_token"),
                                    json={"username": self.client_id, "password": self.client_secret}).json(),
                     "get_token")
        self.access_token, self.refresh_token = data["access_token"], data["refresh_token"]
        return data

    def refresh(self) -> dict:
        if not self.refresh_token:
            raise ProbeError("no refresh_token; call get_token first")
        data = check(self.http.post(self._url("refresh_token"),
                                    json={"refresh_token": self.refresh_token}).json(), "refresh_token")
        self.access_token, self.refresh_token = data["access_token"], data["refresh_token"]
        return data

    def get(self, endpoint: str, **params: Any) -> dict:
        if not self.access_token:
            self.get_token()
        params["access_token"] = self.access_token
        return check(self.http.get(self._url(endpoint), params=params).json(), endpoint)

    def revoke(self) -> None:
        if self.access_token:
            self.http.get(self._url("del_token"), params={"access_token": self.access_token})
            self.access_token = None


# ---------- commands ----------

def save_sample(name: str, payload: Any) -> Path:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    path = SAMPLES / f"{name}.json"
    path.write_text(json.dumps(mask_phone(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def cmd_token(c: YeastarClient, _: argparse.Namespace) -> None:
    d = c.get_token()
    print(f"token OK (expires in {d.get('access_token_expire_time')} s)")
    c.refresh()
    print("refresh OK")


def fetch_recent(c: YeastarClient, endpoint: str, days: int, max_pages: int = 50) -> list[dict]:
    """Page through <endpoint>/list newest-first until records are older than `days`."""
    cutoff = datetime.now() - timedelta(days=days)
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        data = c.get(f"{endpoint}/list", page=page, page_size=100, sort_by="id", order_by="desc")
        batch = data.get("data") or []
        rows.extend(batch)
        times = [parse_pbx_time(r.get("time")) for r in batch]
        if not batch or (times and all(t is not None and t < cutoff for t in times)):
            break
    return [r for r in rows if (t := parse_pbx_time(r.get("time"))) is None or t >= cutoff]


def cmd_cdr(c: YeastarClient, a: argparse.Namespace) -> None:
    rows = fetch_recent(c, "cdr", a.days)
    print(f"{len(rows)} CDR rows in the last {a.days} day(s)")
    if rows:
        print("fields:", ", ".join(rows[0].keys()))
        for r in rows[:20]:
            print(json.dumps(mask_phone(r), ensure_ascii=False))
    print("sample saved:", save_sample("cdr_list", rows[:50]))


def cmd_recordings(c: YeastarClient, a: argparse.Namespace) -> None:
    data = c.get("recording/list", page=1, page_size=a.limit, sort_by="id", order_by="desc")
    rows = data.get("data") or []
    print(f"{len(rows)} recording(s) (total reported: {data.get('total_number')})")
    for r in rows:
        print(json.dumps(mask_phone(r), ensure_ascii=False))
    print("sample saved:", save_sample("recording_list", rows))


def cmd_recording(c: YeastarClient, a: argparse.Namespace) -> None:
    rec_id = a.id
    if a.latest or not rec_id:
        rows = c.get("recording/list", page=1, page_size=1, sort_by="id", order_by="desc").get("data") or []
        if not rows:
            raise ProbeError("no recordings found")
        rec_id = rows[0]["id"]
    info = c.get("recording/download", id=rec_id)
    url = f"{c.base_url}{info['download_resource_url']}"
    out_dir = ROOT / "data" / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (info.get("file") or f"recording_{rec_id}.wav")
    with c.http.stream("GET", url, params={"access_token": c.access_token}) as r:
        r.raise_for_status()
        with out.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"saved {out} ({out.stat().st_size / 1024:.1f} KB)")
    print("format:", describe_wav(out))


async def _events(c: YeastarClient, seconds: int, topics: list[int]) -> None:
    import ssl
    import websockets

    if not c.access_token:
        c.get_token()
    ws_url = c.base_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}{API_PATH}/subscribe?access_token={c.access_token}"
    ssl_ctx = None
    if ws_url.startswith("wss://"):
        ssl_ctx = ssl.create_default_context()
        if not c.verify_ssl:  # self-signed PBX certificate
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
    events: list[Any] = []
    async with websockets.connect(ws_url, ssl=ssl_ctx, user_agent_header=USER_AGENT) as ws:
        await ws.send(json.dumps({"topic_list": topics}))
        print("subscribe:", await ws.recv())
        print(f"listening {seconds}s for events {topics} - make a test call now...")
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        next_beat = loop.time() + 30
        while (now := loop.time()) < end:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=max(0.1, min(next_beat, end) - now))
            except asyncio.TimeoutError:
                if loop.time() >= next_beat:
                    await ws.send("heartbeat")
                    next_beat = loop.time() + 30
                continue
            if msg == "heartbeat response":
                continue
            try:
                parsed = json.loads(msg)
            except json.JSONDecodeError:
                parsed = msg
            events.append(parsed)
            print(json.dumps(mask_phone(parsed), ensure_ascii=False))
    print(f"{len(events)} event(s); sample saved:", save_sample("events", events))


def cmd_events(c: YeastarClient, a: argparse.Namespace) -> None:
    asyncio.run(_events(c, a.seconds, a.topics))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("token")
    s = sub.add_parser("cdr"); s.add_argument("--days", type=int, default=7)
    s = sub.add_parser("recordings"); s.add_argument("--limit", type=int, default=10)
    s = sub.add_parser("recording"); s.add_argument("--latest", action="store_true"); s.add_argument("--id")
    s = sub.add_parser("events"); s.add_argument("--seconds", type=int, default=120)
    s.add_argument("--topics", type=int, nargs="+", default=[30011, 30012, 30008])
    a = p.parse_args(argv)
    cmds = {"token": cmd_token, "cdr": cmd_cdr, "recordings": cmd_recordings,
            "recording": cmd_recording, "events": cmd_events}
    try:
        c = YeastarClient.from_env()
        try:
            cmds[a.cmd](c, a)
        finally:
            c.revoke()
    except ProbeError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print("CONNECTION ERROR:", exc, file=sys.stderr)
        print("Check YEASTAR_BASE_URL, that this PC can reach the PBX, and YEASTAR_VERIFY_SSL.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
