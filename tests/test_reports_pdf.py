"""PDF report: HTML content (everywhere), real PDF + Arabic text read back + speed (needs Pango: Docker image),
scheduled archive (idempotent, catch-up)."""
import io
import re
import time
import unicodedata
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from callsight import kpi
from callsight.db.models import Agent, ReportArchive
from callsight.reports import archive, charts, pdf
from test_kpi import TEAM, at, engine, week  # noqa: F401  (engine is a fixture)
from test_kpi_api import seed_month

WEEK = kpi.period_for("week", date(2026, 11, 1))
NOW = datetime(2026, 11, 14, 6, 0, tzinfo=timezone.utc)
# pypdf returns the 2-letter ligature glyphs of IBM Plex (e.g. final "بي", "ني", "لى") out of place, so the
# read-back check uses a name without them; the visual check of these is done on a rendered PNG.
AGENT_NAME = "خالد الشمري"


def weasyprint_or_skip():
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint/Pango not available here (Windows PC); run in the Docker image")


@pytest.fixture
def session(engine):  # noqa: F811
    with engine.begin() as c:
        c.execute(text("TRUNCATE report_download, report_archive, call_leg, call, agent, reconcile_run "
                       "RESTART IDENTITY CASCADE"))
    with sessionmaker(engine, expire_on_commit=False)() as s:
        week(s)
        s.merge(Agent(ext="104", name=AGENT_NAME, team="customer_service", in_team=True))
        s.commit()
        yield s


def squash(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


# ---------- HTML (no Pango needed) ----------

def test_html_has_sections_flags_and_no_full_numbers(session):
    html = pdf.build_html(session, WEEK, TEAM, now=NOW)
    assert pdf.NOT_TRUSTED in html and "تقرير مؤشرات الأداء" in html and f'class="tag">{pdf.PARTIAL}<' not in html
    assert html.count('class="agent"') == 3 and AGENT_NAME in html
    assert "خدمة العملاء" in html and "ما بعد البيع" in html and pdf.NA in html
    assert '<bdi dir="ltr">15</bdi>' in html                         # numbers isolated from the Arabic order
    for full in ("0507777777", "501111111", "0505555555", "0506666666"):
        assert full not in html
    assert "05XX…7777" in html
    for note in pdf.FOOTNOTES:
        assert note in html


def test_html_partial_period_and_agent_scope(session):
    p = kpi.period_for("week", date(2026, 11, 14))
    html = pdf.build_html(session, p, TEAM, agent="104", now=at(11, 14, 9))
    assert f'class="tag">{pdf.PARTIAL}<' in html and html.count('class="agent"') == 0 and "الموظف 104" in html


def test_after_sales_pdf_shows_outgoing_only(session):
    for kw in ({"team": "after_sales"}, {"agent": "116"}):
        html = pdf.build_html(session, WEEK, TEAM, now=NOW, **kw)
        assert "المكالمات الصادرة" in html and "الصادرة التي تم الرد عليها" in html, kw
        assert "المكالمات الواردة يومياً" not in html and "حالات المكالمات الواردة" not in html, kw
        body = html.split('<ol class="notes">')[0]                # the footnotes explain every term: not counted
        assert "<td>رنات فائتة" not in body and "<td>نسبة الرد" not in body and "<th class=\"n\">رنات فائتة" not in body, kw


def test_range_pdf_filename_and_title(session):
    p = kpi.date_range(date(2026, 11, 1), date(2026, 11, 7))
    assert pdf.filename(p) == "callsight-2026-11-01-to-2026-11-07-all.pdf"
    assert "فترة مختارة" in pdf.build_html(session, p, TEAM, now=NOW)


def test_day_chart_runs_right_to_left():
    days = [{"day": date(2026, 11, d), "calls": d, "answered": d, "unanswered": 0} for d in range(1, 8)]
    xs = [float(x) for x in re.findall(r'<rect x="([\d.]+)"', charts.day_bars(days))]
    assert xs == sorted(xs, reverse=True)                            # Sunday on the right


def test_filename():
    assert pdf.filename(WEEK) == "callsight-week-2026-11-01-all.pdf"
    assert pdf.filename(WEEK, agent="104") == "callsight-week-2026-11-01-agent-104.pdf"


# ---------- real PDF (Docker image) ----------

def test_pdf_arabic_text_reads_back(session):
    weasyprint_or_skip()
    from pypdf import PdfReader
    data = pdf.make_pdf(session, WEEK, TEAM, now=NOW)
    reader = PdfReader(io.BytesIO(data))
    assert reader.pages[0].mediabox.width == pytest.approx(595.28, abs=1)      # A4
    lines = [squash(line) for p in reader.pages for line in p.extract_text().splitlines()]
    for phrase in ("تقرير مؤشرات الأداء", AGENT_NAME, pdf.NOT_TRUSTED, "رنات فائتة (حسب سجل السنترال)", pdf.NA,
                   "متوسط مدة المعالجة (AHT)"):
        # pypdf moves digit/Latin runs inside an Arabic line, so each Arabic run of the phrase is checked
        # (logical or visual order) on one line, and the line must also hold the phrase's digits.
        parts = [squash(x) for x in re.findall(r"[؀-ۿ\s]+", phrase) if x.strip()]
        digits = re.sub(r"\D", "", phrase)
        assert any(all(w in line or w[::-1] in line for w in parts) and digits in re.sub(r"\D", "", line)
                   for line in lines), phrase
    assert "0507777777" not in "".join(lines) and len(reader.pages) >= 4       # summary + 3 agent sections


def test_pdf_month_of_6000_calls_under_30_seconds(session, engine):  # noqa: F811
    weasyprint_or_skip()
    seed_month(engine)
    t0 = time.perf_counter()
    data = pdf.make_pdf(session, kpi.period_for("month", date(2026, 11, 1)), TEAM, now=NOW)
    took = time.perf_counter() - t0
    print(f"PDF, month with 6,000 seeded calls: {took:.2f} s, {len(data)} bytes")
    assert data.startswith(b"%PDF") and took < 30


# ---------- archive (PDF faked on the PC, real in Docker) ----------

@pytest.fixture
def fake_pdf(monkeypatch):
    calls = []

    def make(session, period, teams, now=None, **kw):
        calls.append(period)
        return b"%PDF-fake " + period.start.isoformat().encode()
    monkeypatch.setattr(archive, "make_pdf", make)
    return calls


def test_generate_is_idempotent(session, tmp_path, fake_pdf):
    a = archive.generate(session, "week", TEAM, tmp_path, now=NOW)
    b = archive.generate(session, "week", TEAM, tmp_path, now=NOW)
    assert a.id == b.id and a.period_start == date(2026, 11, 1) and a.file == "2026/callsight-week-2026-11-01-all.pdf"
    assert (tmp_path / a.file).read_bytes() == b"%PDF-fake 2026-11-01"
    assert session.scalar(select(func.count()).select_from(ReportArchive)) == 1
    assert [p.name for p in (tmp_path / "2026").iterdir()] == ["callsight-week-2026-11-01-all.pdf"]   # no temp left


def test_catch_up_builds_missing_latest_week_and_month_once(session, tmp_path, fake_pdf):
    made = archive.catch_up(session, TEAM, tmp_path, now=NOW)
    assert sorted((r.period_type, r.period_start) for r in made) == [("month", date(2026, 10, 1)),
                                                                     ("week", date(2026, 11, 1))]
    assert archive.catch_up(session, TEAM, tmp_path, now=NOW) == [] and len(fake_pdf) == 2


def test_beat_schedule_has_weekly_and_monthly_reports():
    jobs = pytest.importorskip("callsight.jobs", reason="needs .env settings")
    sched = jobs.celery.conf.beat_schedule
    week_job, month_job = sched["report-weekly"], sched["report-monthly"]
    assert week_job["task"] == "callsight.jobs.report_week" and week_job["schedule"].day_of_week == {0}
    assert week_job["schedule"].hour == {7} and week_job["schedule"].minute == {0}
    assert month_job["schedule"].day_of_month == {1} and month_job["schedule"].hour == {7}
    assert jobs.celery.conf.timezone == "Asia/Riyadh"


def test_real_archive_run(session, tmp_path):
    weasyprint_or_skip()
    row = archive.generate(session, "week", TEAM, tmp_path, now=NOW)
    assert (tmp_path / row.file).read_bytes().startswith(b"%PDF") and row.size_bytes > 10_000


def test_pdf_refuses_local_files_and_network():
    """Only bundled fonts may load: an injected <img src="file:///..."> or http URL is blocked."""
    weasyprint_or_skip()
    assert not pdf._allowed("file:///etc/passwd") and not pdf._allowed("http://192.168.50.151/x.png")
    assert pdf._allowed(next(pdf.FONTS.glob("*.ttf")).resolve().as_uri())     # bundled font still loads
    from weasyprint.urls import FatalURLFetchingError
    with pytest.raises(FatalURLFetchingError):                              # fail_on_errors: rendering stops
        pdf.render_pdf('<html><body><img src="file:///etc/passwd"></body></html>')
    assert pdf.render_pdf("<html><body><p>مرحبا</p></body></html>").startswith(b"%PDF")


def test_chart_axis_fits_stacked_bars():
    svg = charts.day_bars([{"day": date(2026, 11, 1), "calls": 4, "answered": 4, "unanswered": 5}])
    assert ">12<" in svg                                     # axis top (12) covers answered + missed (9), not 4
