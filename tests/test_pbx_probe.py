import json
import sys
import wave
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pbx_probe as p  # noqa: E402


def make_client(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": p.USER_AGENT})
    return p.YeastarClient("https://pbx.local:8088/", "id", "secret", http=http)


def token_handler(request: httpx.Request):
    path = request.url.path
    if path.endswith("/get_token"):
        assert json.loads(request.content) == {"username": "id", "password": "secret"}
        assert request.headers["User-Agent"] == "OpenAPI"
        return httpx.Response(200, json={"errcode": 0, "errmsg": "SUCCESS", "access_token": "A1",
                                         "refresh_token": "R1", "access_token_expire_time": 1800})
    if path.endswith("/refresh_token"):
        assert json.loads(request.content) == {"refresh_token": "R1"}
        return httpx.Response(200, json={"errcode": 0, "errmsg": "SUCCESS", "access_token": "A2",
                                         "refresh_token": "R2"})
    return httpx.Response(404)


def test_token_and_refresh():
    c = make_client(token_handler)
    c.get_token()
    assert c.access_token == "A1"
    c.refresh()
    assert (c.access_token, c.refresh_token) == ("A2", "R2")


def test_error_code_raises():
    c = make_client(lambda r: httpx.Response(200, json={"errcode": 10004, "errmsg": "FAILURE"}))
    with pytest.raises(p.ProbeError, match="10004"):
        c.get_token()


def test_get_adds_token_and_url():
    seen = {}

    def handler(request):
        if request.url.path.endswith("/get_token"):
            return token_handler(request)
        seen["url"] = request.url
        return httpx.Response(200, json={"errcode": 0, "data": []})

    c = make_client(handler)
    c.get("cdr/list", page=1)
    assert seen["url"].path == "/openapi/v1.0/cdr/list"
    assert seen["url"].params["access_token"] == "A1"


def test_mask_phone_keeps_extensions():
    assert p.mask_phone("0562836503") == "056*****03"
    assert p.mask_phone("116<116>") == "116<116>"
    assert p.mask_phone({"call_to": "0501606795", "ext": ["1001"]}) == {"call_to": "050*****95", "ext": ["1001"]}


@pytest.mark.parametrize("value", ["11/12/2026 01:14:12 PM", "11/12/2026 13:14:12", "2026/11/12 13:14:12",
                                   "2026-11-12 13:14:12"])
def test_parse_pbx_time(value):
    assert p.parse_pbx_time(value) == datetime(2026, 11, 12, 13, 14, 12)


def test_parse_pbx_time_unknown():
    assert p.parse_pbx_time("yesterday") is None


def test_fetch_recent_stops_at_cutoff():
    now = datetime.now()
    fmt = "%Y-%m-%d %H:%M:%S"
    new, old = (now - timedelta(days=1)).strftime(fmt), (now - timedelta(days=30)).strftime(fmt)
    pages = {"1": [{"id": 2, "time": new}, {"id": 1, "time": old}], "2": [{"id": 0, "time": old}]}
    calls = []

    def handler(request):
        if request.url.path.endswith("/get_token"):
            return token_handler(request)
        page = request.url.params["page"]
        calls.append(page)
        return httpx.Response(200, json={"errcode": 0, "data": pages.get(page, [])})

    rows = p.fetch_recent(make_client(handler), "cdr", days=7)
    assert [r["id"] for r in rows] == [2]
    assert calls == ["1", "2"]


def test_describe_wav(tmp_path):
    f = tmp_path / "a.wav"
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)
    assert "MONO" in p.describe_wav(f) and "8000 Hz" in p.describe_wav(f)


def test_missing_env(monkeypatch, tmp_path):
    for k in ("YEASTAR_BASE_URL", "YEASTAR_CLIENT_ID", "YEASTAR_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(p, "ROOT", tmp_path)
    with pytest.raises(p.ProbeError, match="Missing in .env"):
        p.YeastarClient.from_env()
