"""YeastarAdapter against a fake PBX (httpx.MockTransport) - no network."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from callsight.pbx.base import AuthBlocked, AuthError, PbxError
from callsight.pbx.yeastar import YeastarAdapter, safe_recording_name


class FakePbx:
    def __init__(self, n_rows=250, login_ok=True):
        # ids 1..n, one row every 60 s starting at T0
        self.t0 = 1_794_000_000
        self.rows = [{"id": i, "uid": f"u{i}", "timestamp": self.t0 + 60 * i} for i in range(1, n_rows + 1)]
        self.login_ok = login_ok
        self.logins = self.refreshes = 0
        self.pages_served = 0
        self.expire_next = False
        self.fail_transport = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_transport:
            self.fail_transport -= 1
            raise httpx.ConnectError("boom")
        path = urlparse(str(request.url)).path
        q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        ok = lambda **kw: httpx.Response(200, json={"errcode": 0, "errmsg": "SUCCESS", **kw})
        if path.endswith("/get_token"):
            self.logins += 1
            if not self.login_ok:
                return httpx.Response(200, json={"errcode": 20004, "errmsg": "PASSWORD ERROR"})
            return ok(access_token=f"tok{self.logins}", refresh_token="ref", access_token_expire_time=1800)
        if path.endswith("/refresh_token"):
            self.refreshes += 1
            return ok(access_token=f"rtok{self.refreshes}", refresh_token="ref2")
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(200, json={"errcode": 10004, "errmsg": "ACCESS TOKEN EXPIRED"})
        if path.endswith("/cdr/list"):
            self.pages_served += 1
            page, size = int(q["page"]), int(q["page_size"])
            desc = sorted(self.rows, key=lambda r: -r["id"])
            return ok(total_number=len(desc), data=desc[(page - 1) * size: page * size])
        if path.endswith("/recording/download"):
            return ok(file=q["file"], download_resource_url="/api/download/x.wav")
        if path.endswith("/api/download/x.wav"):
            return httpx.Response(200, content=b"RIFFdata")
        if path.endswith("/queue/list"):
            return ok(queue_list=[{"number": "6401", "name": "CS", "static_agent_list": [
                {"text": "Agent A", "text2": "104"}, {"text": "Agent B", "text2": "119"}],
                "dynamic_agent_list": None}])
        if path.endswith("/extension/list"):
            return ok(total_number=2, data=[{"number": "104", "caller_id_name": "A"},
                                            {"number": "119", "caller_id_name": "B"}])
        return httpx.Response(404)


def make(fake: FakePbx, clock=None):
    t = {"now": 0.0}
    adapter = YeastarAdapter("https://pbx:8088", "id", "secret",
                             http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                             sleep=lambda s: None, clock=clock or (lambda: t["now"]))
    return adapter, t


def test_fetch_since_returns_new_rows_plus_overlap_and_stops_paging():
    fake = FakePbx(n_rows=1000)
    a, _ = make(fake)
    rows = a.fetch_cdr_since(last_id=950, overlap=100)
    assert sorted(r["id"] for r in rows) == list(range(851, 1001))
    assert fake.pages_served == 2          # stops once it reaches the floor


def test_fetch_since_zero_reads_everything():
    fake = FakePbx(n_rows=250)
    a, _ = make(fake)
    assert len(a.fetch_cdr_since(0)) == 250


def test_fetch_between_filters_by_timestamp():
    fake = FakePbx(n_rows=500)
    a, _ = make(fake)
    start = datetime.fromtimestamp(fake.t0 + 60 * 100, tz=timezone.utc)
    end = datetime.fromtimestamp(fake.t0 + 60 * 200, tz=timezone.utc)
    rows = a.fetch_cdr_between(start, end)
    assert sorted(r["id"] for r in rows) == list(range(100, 200))


def test_token_reused_then_refreshed_after_25_min():
    fake = FakePbx()
    a, t = make(fake)
    a.list_queues(); a.list_queues()
    assert fake.logins == 1 and fake.refreshes == 0
    t["now"] = 26 * 60
    a.list_queues()
    assert fake.refreshes == 1 and a.access_token == "rtok1"


def test_expired_token_relogs_once():
    fake = FakePbx()
    a, _ = make(fake)
    a.list_queues()
    fake.expire_next = True
    assert a.list_queues()[0]["number"] == "6401"
    assert fake.logins == 2


def test_stops_after_three_failed_logins():
    fake = FakePbx(login_ok=False)
    a, _ = make(fake)
    for _ in range(3):
        with pytest.raises(AuthError):
            a.list_queues()
    with pytest.raises(AuthBlocked):
        a.list_queues()
    assert fake.logins == 3                # 4th attempt never reaches the PBX


def test_network_errors_are_retried():
    fake = FakePbx()
    fake.fail_transport = 2
    a, _ = make(fake)
    assert a.list_extensions() == [{"ext": "104", "name": "A"}, {"ext": "119", "name": "B"}]


def test_network_down_raises_pbx_error():
    fake = FakePbx()
    fake.fail_transport = 99
    a, _ = make(fake)
    with pytest.raises(PbxError):
        a.list_queues()


def test_download_recording(tmp_path):
    a, _ = make(FakePbx())
    out = a.download_recording("20261112-abc-Inbound.wav", tmp_path / "x" / "rec.wav")
    assert out.read_bytes() == b"RIFFdata"
    assert not (tmp_path / "x" / "rec.wav.part").exists()


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b.wav", r"a\b.wav", "..", "", "C:\\x.wav"])
def test_unsafe_recording_names_rejected(bad):
    with pytest.raises(PbxError):
        safe_recording_name(bad)


def test_queues_parsed():
    a, _ = make(FakePbx())
    q = a.list_queues()[0]
    assert q["number"] == "6401" and [x["ext"] for x in q["agents"]] == ["104", "119"]
