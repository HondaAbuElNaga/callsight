"""KPI + report endpoints against the test Postgres: auth, validation, numbers = kpi.compute, audit, speed."""
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from callsight.api import auth
from callsight.api.main import ApiConfig, create_app
from callsight.db.models import ReportArchive, ReportDownload
from test_kpi import TEAM, engine, week  # noqa: F401  (engine is a fixture)

SECRET = "x" * 40
PASSWORD = "correct-horse-battery"
WEEK = {"period": "week", "start": "2026-11-03"}


def seed_month(eng, n: int = 6000) -> None:
    """n synthetic calls spread over November 2026 (inbound/outbound, both teams, repeats, missed rings)."""
    with eng.begin() as c:
        c.execute(text("""
INSERT INTO call (uid, direction, entry, customer_number, team, agent_ext, missed_agent_ext, start_time, end_time,
                  ivr_seconds, wait_seconds, talk_seconds, total_seconds, status, recording_status, recording_attempts)
SELECT 'seed-' || g,
       CASE WHEN g % 5 = 0 THEN 'outbound' ELSE 'inbound' END, 'queue',
       '05' || lpad(((g * 7919) % 900)::text, 8, '0'),
       CASE WHEN g % 4 = 0 THEN 'after_sales' ELSE 'customer_service' END,
       CASE WHEN g % 7 = 0 THEN NULL WHEN g % 4 = 0 THEN '116' WHEN g % 2 = 0 THEN '104' ELSE '119' END,
       CASE WHEN g % 7 = 0 THEN '104' END,
       timestamptz '2026-10-31 21:00+00' + make_interval(secs => g * 2592000.0 / :n),
       timestamptz '2026-10-31 21:00+00' + make_interval(secs => g * 2592000.0 / :n + 200),
       0, g % 30, CASE WHEN g % 7 = 0 THEN 0 ELSE 30 + g % 400 END, 200,
       CASE WHEN g % 7 = 0 THEN 'abandoned_queue' ELSE 'answered' END, 'none', 0
FROM generate_series(1, :n) g"""), {"n": n})
        c.execute(text("""
INSERT INTO call_leg (id, call_id, uid, kind, start_time, to_number, disposition, raw)
SELECT 1000000 + c.id, c.id, c.uid, 'extension', c.start_time, '104', 'NO ANSWER', '{}'::jsonb
FROM call c WHERE c.uid LIKE 'seed-%' AND c.status = 'abandoned_queue'"""))


@pytest.fixture
def env(engine, tmp_path):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE report_download, report_archive, app_user, call_leg, call, agent, reconcile_run "
                       "RESTART IDENTITY CASCADE"))
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as s:
        week(s)
        auth.create_user(s, "boss", PASSWORD, "supervisor", "114")
    app = create_app(Session, ApiConfig(secret=SECRET, audio_dir=tmp_path, teams=lambda: TEAM, reports_dir=tmp_path))
    return TestClient(app), Session, tmp_path


def login(client):
    assert client.post("/api/login", json={"username": "boss", "password": PASSWORD}).status_code == 200


def test_kpi_endpoints_need_login(env):
    client, _, _ = env
    for url in ("/api/kpi", "/api/reports", "/api/reports/pdf", "/api/reports/1/pdf"):
        assert client.get(url).status_code == 401, url


def test_kpi_week_matches_hand_calculation(env):
    client, _, _ = env
    login(client)
    r = client.get("/api/kpi", params=WEEK)
    assert r.status_code == 200
    d = r.json()
    assert d["period"] == {"kind": "week", "start": "2026-11-01", "end": "2026-11-07", "partial": False}
    assert (d["summary"]["calls"], d["summary"]["answered"], d["summary"]["repeats"]) == (15, 10, 5)
    assert d["summary"]["aht"] == pytest.approx(142.0) and d["summary"]["satisfaction"] is None
    assert d["previous"]["calls"] == 2 and d["trusted"] is False
    assert [a["ext"] for a in d["agents"]] == ["104", "116", "119"]
    assert d["days"][0] == {"day": "2026-11-01", "calls": 2, "answered": 2, "unanswered": 0}
    assert all("XX…" in x["number"] for x in d["top_repeaters"])


def test_kpi_team_and_agent_filters(env):
    client, _, _ = env
    login(client)
    t = client.get("/api/kpi", params={**WEEK, "team": "after_sales"}).json()
    assert t["summary"]["calls"] == 4 and [a["ext"] for a in t["agents"]] == ["116"]
    a = client.get("/api/kpi", params={**WEEK, "agent": "104"}).json()
    assert (a["summary"]["calls"], a["summary"]["unanswered"]) == (3, 3) and a["by_status"] is None
    m = client.get("/api/kpi", params={"period": "month", "start": "2026-11-20"}).json()
    assert m["summary"]["calls"] == 17 and m["previous_period"]["start"] == "2026-10-01"


@pytest.mark.parametrize("params", [
    {"period": "year"}, {"start": "2026-13-01"}, {"start": "1900-01-01"}, {"team": "sales"},
    {"team": "Robert'); drop"}, {"agent": "114"}, {"agent": "302"}, {"agent": "1; drop table call"},
    {"team": "after_sales", "agent": "104"},
])
def test_kpi_bad_input_rejected(env, params):
    client, _, _ = env
    login(client)
    assert client.get("/api/kpi", params=params).status_code == 422
    assert client.get("/api/reports/pdf", params=params).status_code == 422


def test_archive_download_is_audited_and_confined(env):
    client, Session, tmp = env
    login(client)
    (tmp / "2026").mkdir()
    (tmp / "2026" / "r.pdf").write_bytes(b"%PDF-1.7 test")
    (tmp.parent / "secret.pdf").write_bytes(b"%PDF secret")
    with Session() as s:
        s.add_all([ReportArchive(period_type="week", period_start=date(2026, 11, 1), period_end=date(2026, 11, 8),
                                 scope="all", file="2026/r.pdf", size_bytes=13),
                   ReportArchive(period_type="month", period_start=date(2026, 10, 1), period_end=date(2026, 11, 1),
                                 scope="all", file="../secret.pdf", size_bytes=11)])
        s.commit()
    lst = client.get("/api/reports").json()
    assert [(r["period_type"], r["period_end"]) for r in lst] == [("week", "2026-11-07"), ("month", "2026-10-31")]
    r = client.get(f"/api/reports/{lst[0]['id']}/pdf")
    assert r.status_code == 200 and r.content == b"%PDF-1.7 test" and r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/reports/{lst[1]['id']}/pdf").status_code == 404     # outside REPORTS_DIR
    assert client.get("/api/reports/999/pdf").status_code == 404
    with Session() as s:
        rows = s.scalars(select(ReportDownload)).all()
        assert [(x.archive_id, x.scope, x.period_start) for x in rows] == [(lst[0]["id"], "all", date(2026, 11, 1))]


def test_live_pdf_download_audited(env):
    client, Session, _ = env
    login(client)
    r = client.get("/api/reports/pdf", params={**WEEK, "agent": "119"})
    with Session() as s:
        n = s.scalar(select(func.count()).select_from(ReportDownload))
    if r.status_code == 503:                       # no Pango on this machine (Windows PC): nothing logged
        assert n == 0
        pytest.skip("WeasyPrint/Pango not available here; covered in the Docker run")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert 'filename="callsight-week-2026-11-01-agent-119.pdf"' in r.headers["content-disposition"]
    with Session() as s:
        assert s.scalars(select(ReportDownload)).one().scope == "agent:119"


def test_kpi_month_of_6000_calls_under_2_seconds(env, engine):  # noqa: F811
    client, _, _ = env
    seed_month(engine)
    login(client)
    t0 = time.perf_counter()
    r = client.get("/api/kpi", params={"period": "month", "start": "2026-11-01"})
    took = time.perf_counter() - t0
    assert r.status_code == 200 and r.json()["summary"]["calls"] > 4800
    print(f"KPI API, month with 6,000 seeded calls: {took:.2f} s")
    assert took < 2.0
