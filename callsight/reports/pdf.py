"""Arabic RTL PDF report (WeasyPrint): cover, team summary, per-day chart, agent table, one section per agent.

HTML is built here (escaped) and rendered with the bundled IBM Plex Sans Arabic font, so the PDF looks the same
on every machine. Numbers are wrapped in <bdi dir="ltr"> so digits never get mixed into the Arabic text order.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from string import Template

from sqlalchemy.orm import Session

from .. import kpi
from ..config import RIYADH
from ..ingest.classify import TeamConfig
from . import charts

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
TEMPLATE = HERE / "templates" / "report.html"

TEAM_LABEL = {"customer_service": "خدمة العملاء", "after_sales": "ما بعد البيع"}
KIND_LABEL = {"week": "أسبوعي", "month": "شهري", "range": "فترة مختارة"}
STATUS_LABEL = {"answered": "تم الرد", "abandoned_queue": "أغلق أثناء الانتظار", "abandoned_ivr": "أغلق في القائمة",
                "no_answer": "لم يتم الرد", "busy": "مشغول", "voicemail": "بريد صوتي"}
NOT_TRUSTED = "الأرقام غير موثقة بعد: لم تكتمل 7 مطابقات ليلية ناجحة"
PARTIAL = "جزئي"
NA = "غير متاح"
FOOTNOTES = [
    "متوسط مدة المعالجة = مدة الكلام ÷ المكالمات الواردة التي تم الرد عليها؛ سجل السنترال لا يحتوي على مدة "
    "التعليق (Hold)، والمكالمات الصادرة لها عمود منفصل ولا تُخلط.",
    "المكالمات المحوّلة: تُحسب أول جهة ردّت فقط.",
    "مكالمات الموظف = المكالمات الواردة التي ردّ عليها، لذلك مجموع الموظفين لا يساوي إجمالي الفريق "
    "(مثل مكالمات ردّ عليها المشرف 114). تظهر تحويلات الفريق في teams.yaml فقط.",
    "لم يتم الرد يشمل: لم يرد أحد، أغلق أثناء الانتظار، أغلق في القائمة، مشغول، بريد صوتي.",
    "رنات فائتة (حسب سجل السنترال): مكالمات واردة رنّت عند الموظف ولم يرد عليها، مرة واحدة لكل مكالمة.",
    "مدة الرنين قبل الرد: وقت رنين هاتف الموظف، وليست مدة الانتظار في الطابور.",
    "المكالمات المتكررة (72 ساعة): مكالمة واردة تم الرد عليها وسبقها اتصال من/إلى نفس الرقم مع نفس الفريق خلال "
    "72 ساعة؛ تُنسب لموظف آخر مكالمة سابقة تم الرد عليها، وإلا فهي غير منسوبة.",
    "قفل مبكر: مكالمة صادرة لم يُرد عليها وأغلقها الموظف قبل 30 ثانية من الرنين؛ لا تُحسب محاولة اتصال. "
    "محاولات صحيحة % = الصادرة التي تم الرد عليها أو رنّت 30 ثانية أو أنهاها طرف العميل (رفض/مشغول) ÷ كل الصادرة. "
    "الخط ينقطع وحده بعد نحو 62 ثانية رنين.",
    "متوسط الرضا غير متاح حتى تتوفر تقييمات المكالمات (المرحلة 3).",
    "أرقام العملاء مخفية جزئياً لحماية الخصوصية.",
]


# ---------- formatting ----------

def num(v) -> str:
    return f'<bdi dir="ltr">{escape(str(v))}</bdi>'


def dur(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = int(round(seconds))
    return num(f"{s // 60}:{s % 60:02d}")


def pct(v: float | None) -> str:
    return "–" if v is None else num(f"{v * 100:.1f}%")


def day_text(d: date) -> str:
    return num(d.strftime("%Y-%m-%d"))


def change(cur, prev, kind: str = "count", good_up: bool = True) -> str:
    """Difference vs previous period, coloured green when it moved the good way."""
    if cur is None or prev is None:
        return '<span class="muted">–</span>'
    diff = cur - prev
    if abs(diff) < 1e-9:
        return '<span class="muted">=</span>'
    sign = "+" if diff > 0 else "−"
    if kind == "rate":
        text = f"{sign}{abs(diff) * 100:.1f}"
    elif kind == "seconds":
        s = int(round(abs(diff)))
        text = f"{sign}{s // 60}:{s % 60:02d}"
    else:
        text = f"{sign}{abs(diff):g}"
    css = "up" if (diff > 0) == good_up else "down"
    return f'<span class="{css}">{num(text)}</span>'


# ---------- sections ----------

TEAM_ROWS = [  # (key, label, format, change kind, higher is better)
    ("calls", "المكالمات الواردة", "count", "count", True),
    ("answered", "تم الرد", "count", "count", True),
    ("unanswered", "لم يتم الرد", "count", "count", False),
    ("answer_rate", "نسبة الرد", "rate", "rate", True),
    ("aht", "متوسط مدة المعالجة (AHT)", "seconds", "seconds", False),
    ("avg_ring", "مدة الرنين قبل الرد", "seconds", "seconds", False),
    ("outbound", "المكالمات الصادرة", "count", "count", True),
    ("outbound_aht", "متوسط مدة الصادرة", "seconds", "seconds", False),
    ("early_hangups", "قفل مبكر (أقل من 30 ث رنين)", "count", "count", False),
    ("proper_rate", "محاولات صحيحة %", "rate", "rate", True),
    ("outbound_avg_ring", "متوسط ثواني الرنين (الصادرة بلا رد)", "seconds", "seconds", True),
    ("repeats", "مكالمات متكررة خلال 72 ساعة", "count", "count", False),
    ("satisfaction", "متوسط الرضا", "na", "count", True),
]


OUT_ROWS = [
    ("outbound", "المكالمات الصادرة", "count", "count", True),
    ("outbound_answered", "الصادرة التي تم الرد عليها", "count", "count", True),
    ("outbound_aht", "متوسط مدة الصادرة", "seconds", "seconds", False),
    ("early_hangups", "قفل مبكر (أقل من 30 ث رنين)", "count", "count", False),
    ("proper_rate", "محاولات صحيحة %", "rate", "rate", True),
    ("outbound_avg_ring", "متوسط ثواني الرنين (الصادرة بلا رد)", "seconds", "seconds", True),
]


def fmt(v, kind: str) -> str:
    if kind == "na":
        return NA
    if kind == "rate":
        return pct(v)
    if kind == "seconds":
        return dur(v)
    return "–" if v is None else num(v)


def summary_table(cur: dict, prev: dict, rows=TEAM_ROWS) -> str:
    out = ['<table><tr><th>المؤشر</th><th class="n">الفترة</th><th class="n">السابقة</th><th class="n">التغير</th></tr>']
    for key, label, kind, ckind, up in rows:
        if key not in cur:
            continue
        ch = "" if kind == "na" else change(cur[key], prev.get(key), ckind, up)
        out.append(f'<tr><td>{escape(label)}</td><td class="n">{fmt(cur[key], kind)}</td>'
                   f'<td class="n">{fmt(prev.get(key), kind)}</td><td class="n">{ch}</td></tr>')
    out.append("</table>")
    if cur.get("repeats_unattributed"):
        out.append(f'<p class="muted">مكالمات متكررة غير منسوبة لموظف: {num(cur["repeats_unattributed"])}</p>')
    return "".join(out)


def teams_table(per_team: dict[str, dict]) -> str:
    out = ['<table><tr><th>الفريق</th><th class="n">الواردة</th><th class="n">تم الرد</th><th class="n">لم يتم الرد</th>'
           '<th class="n">نسبة الرد</th><th class="n">AHT</th><th class="n">الرنين</th><th class="n">الصادرة</th>'
           '<th class="n">متكررة</th></tr>']
    for name, t in per_team.items():
        out.append(f'<tr><td>{escape(TEAM_LABEL.get(name, name))}</td><td class="n">{num(t["calls"])}</td>'
                   f'<td class="n">{num(t["answered"])}</td><td class="n">{num(t["unanswered"])}</td>'
                   f'<td class="n">{pct(t["answer_rate"])}</td><td class="n">{dur(t["aht"])}</td>'
                   f'<td class="n">{dur(t["avg_ring"])}</td><td class="n">{num(t["outbound"])}</td>'
                   f'<td class="n">{num(t["repeats"])}</td></tr>')
    out.append("</table>")
    return "".join(out)


def agents_table(agents: list[dict], outbound_only: bool = False) -> str:
    if outbound_only:
        out = ['<table><tr><th>الموظف</th><th>الفريق</th><th class="n">الصادرة</th><th class="n">تم الرد</th>'
               '<th class="n">متوسط مدة الصادرة</th><th class="n">قفل مبكر (&lt;30 ث)</th>'
               '<th class="n">محاولات صحيحة</th><th class="n">متوسط الرنين</th></tr>']
        for a in agents:
            out.append(f'<tr><td>{num(a["ext"])} {escape(a["name"])}</td>'
                       f'<td>{escape(TEAM_LABEL.get(a["team"], a["team"]))}</td><td class="n">{num(a["outbound"])}</td>'
                       f'<td class="n">{num(a["outbound_answered"])}</td><td class="n">{dur(a["outbound_aht"])}</td>'
                       f'<td class="n">{num(a["early_hangups"])}</td><td class="n">{pct(a["proper_rate"])}</td>'
                       f'<td class="n">{dur(a["outbound_avg_ring"])}</td></tr>')
        out.append("</table>")
        return "".join(out)
    out = ['<table><tr><th>الموظف</th><th>الفريق</th><th class="n">مكالمات ردّ عليها</th><th class="n">AHT</th>'
           '<th class="n">رنات فائتة</th><th class="n">الصادرة (تم الرد)</th><th class="n">AHT الصادرة</th>'
           '<th class="n">قفل مبكر</th><th class="n">محاولات صحيحة</th><th class="n">متوسط الرنين</th>'
           '<th class="n">متكررة</th></tr>']
    for a in agents:
        out.append(f'<tr><td>{num(a["ext"])} {escape(a["name"])}</td><td>{escape(TEAM_LABEL.get(a["team"], a["team"]))}</td>'
                   f'<td class="n">{num(a["calls"])}</td><td class="n">{dur(a["aht"])}</td>'
                   f'<td class="n">{num(a["missed_rings"])}</td>'
                   f'<td class="n">{num(str(a["outbound"]) + " (" + str(a["outbound_answered"]) + ")")}</td>'
                   f'<td class="n">{dur(a["outbound_aht"])}</td><td class="n">{num(a["early_hangups"])}</td>'
                   f'<td class="n">{pct(a["proper_rate"])}</td><td class="n">{dur(a["outbound_avg_ring"])}</td>'
                   f'<td class="n">{num(a["repeats"])}</td></tr>')
    out.append("</table>")
    return "".join(out)


def followup_table(fu: dict, agents: list[dict]) -> str:
    """Forms (agent-supervisor spec). Only called when the admin switched these KPIs on."""
    names = {a["ext"]: a["name"] for a in agents}
    # no callback columns: the form has no callback time any more (user, 2026-11-23)
    out = [f'<p>تُحتسب من {escape(fu["since"] or "")}.</p>',
           '<table><tr><th>الموظف</th><th class="n">نماذج مطلوبة</th><th class="n">في وقتها</th>'
           '<th class="n">نسبة النماذج</th></tr>']
    rows = [(f'{num(e)} {escape(names.get(e, ""))}', v) for e, v in fu["agents"].items()]
    rows.append(("الإجمالي", fu["summary"]))
    for label, v in rows:
        out.append(f'<tr><td>{label}</td><td class="n">{num(v["forms_due"])}</td>'
                   f'<td class="n">{num(v["forms_on_time"])}</td><td class="n">{pct(v["forms_rate"])}</td></tr>')
    out.append("</table>")
    # follow-up stages spec: how problems ended
    out.append('<table><tr><th>الموظف</th><th class="n">نماذج بها مشكلة</th><th class="n">حُلّت من أول مكالمة</th>'
               '<th class="n">نسبة الحل من أول مكالمة</th><th class="n">حُلّت بعد المتابعة</th>'
               '<th class="n">متوسط الجولات</th><th class="n">مفتوحة</th><th class="n">ملاحظات مستحقة</th>'
               '<th class="n">في وقتها</th><th class="n">نسبة الملاحظات</th>'
               '<th class="n">لا تحتاج متابعة</th><th class="n">نسبتها</th></tr>')
    for label, v in rows:
        avg = "–" if v.get("avg_rounds") is None else f'{v["avg_rounds"]:.1f}'
        out.append(f'<tr><td>{label}</td><td class="n">{num(v["answered_forms"])}</td>'
                   f'<td class="n">{num(v["solved_first"])}</td><td class="n">{pct(v["first_rate"])}</td>'
                   f'<td class="n">{num(v["solved_after"])}</td><td class="n">{num(avg)}</td>'
                   f'<td class="n">{num(v["open"])}</td><td class="n">{num(v["notes_due"])}</td>'
                   f'<td class="n">{num(v["notes_on_time"])}</td><td class="n">{pct(v["notes_rate"])}</td>'
                   f'<td class="n">{num(v["no_followup"])}</td><td class="n">{pct(v["no_followup_rate"])}</td></tr>')
    out.append("</table>")
    return "".join(out)


def status_table(by_status: dict) -> str:
    total = sum(by_status.values())
    out = ['<table><tr><th>الحالة</th><th class="n">العدد</th><th></th></tr>']
    for st, n in by_status.items():
        color = charts.OK if st == "answered" else charts.BAD
        out.append(f'<tr><td>{escape(STATUS_LABEL[st])}</td><td class="n">{num(n)}</td>'
                   f'<td class="n">{charts.hbar(n, total, color)}</td></tr>')
    out.append("</table>")
    return "".join(out)


def repeaters_table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="muted">لا توجد مكالمات متكررة.</p>'
    body = "".join(f'<tr><td>{num(r["number"])}</td><td class="n">{num(r["repeats"])}</td></tr>' for r in rows)
    return f'<table><tr><th>رقم العميل</th><th class="n">مكالمات متكررة</th></tr>{body}</table>'


def day_chart(days: list[dict], unanswered_label: str = "لم يتم الرد") -> str:
    legend = (f'<p class="legend muted"><span style="background:{charts.OK}"></span>تم الرد '
              f'<span style="background:{charts.BAD}"></span>{escape(unanswered_label)}</p>')
    return f'<div class="chart">{charts.day_bars(days)}</div>{legend}'


AGENT_TILES = [("calls", "مكالمات ردّ عليها", "count", "count", True),
               ("aht", "متوسط مدة المعالجة", "seconds", "seconds", False),
               ("unanswered", "رنات فائتة (حسب سجل السنترال)", "count", "count", False),
               ("outbound", "المكالمات الصادرة", "count", "count", True),
               ("outbound_aht", "متوسط مدة الصادرة", "seconds", "seconds", False),
               ("early_hangups", "قفل مبكر (أقل من 30 ث رنين)", "count", "count", False),
               ("proper_rate", "محاولات صحيحة %", "rate", "rate", True),
               ("outbound_avg_ring", "متوسط ثواني الرنين (الصادرة بلا رد)", "seconds", "seconds", True),
               ("repeats", "مكالمات متكررة منسوبة", "count", "count", False),
               ("satisfaction", "متوسط الرضا", "na", "count", True)]


def agent_section(row: dict, cur: dict, prev: dict) -> str:
    s, p = cur["summary"], prev
    outbound_only = row["team"] in kpi.OUTBOUND_ONLY_TEAMS
    tiles = "".join(
        f'<div class="tile"><div class="l">{escape(label)}</div><div class="v">{fmt(s[k], kind)}</div>'
        f'<div class="l">السابقة: {fmt(p.get(k), kind)} {"" if kind == "na" else change(s[k], p.get(k), ck, up)}</div></div>'
        for k, label, kind, ck, up in (OUT_ROWS if outbound_only else AGENT_TILES))
    team = TEAM_LABEL.get(row["team"], row["team"])
    daily = "" if outbound_only else f'<h3>المكالمات يومياً</h3>{day_chart(cur["days"], "رنات فائتة")}'
    return (f'<section class="agent"><h2>{num(row["ext"])} {escape(row["name"])} '
            f'<span class="muted">· {escape(team)}</span></h2><div class="tiles">{tiles}</div>{daily}'
            f'<h3>أكثر الأرقام تكراراً (منسوبة للموظف)</h3>{repeaters_table(cur["top_repeaters"])}</section>')


def cover(data: dict, scope_label: str, generated: datetime) -> str:
    p = data["period"]
    partial = f' <span class="tag">{PARTIAL}</span>' if p["partial"] else ""
    banner = "" if data["trusted"] else f'<div class="banner">{NOT_TRUSTED}</div>'
    pp = data["previous_period"]
    return (f'<div class="cover"><h1>تقرير مؤشرات الأداء · {KIND_LABEL[p["kind"]]}</h1>'
            f'<p>الفترة: من {num(p["start"])} إلى {num(p["end"])}{partial}</p>'
            f'<p>النطاق: {escape(scope_label)}</p>'
            f'<p class="muted">المقارنة مع: {num(pp["start"])} – {num(pp["end"])} · '
            f'تاريخ الإنشاء: {num(generated.astimezone(RIYADH).strftime("%Y-%m-%d %H:%M"))} (توقيت الرياض)</p>'
            f'</div>{banner}')


def scope_label(team: str | None, agent: str | None, agent_name: str = "") -> str:
    if agent:
        return f"الموظف {agent} {agent_name}".strip()
    return TEAM_LABEL.get(team, team) if team else "كل الفرق"


def build_html(session: Session, period: kpi.Period, teams: TeamConfig, team: str | None = None,
               agent: str | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    data = kpi.report(session, period, teams, team=team, agent=agent, now=now)
    prev_p = period.previous()
    name = data["agents"][0]["name"] if agent and data["agents"] else ""
    view_team = teams.ext_team.get(agent) if agent else team
    outbound_only = view_team in kpi.OUTBOUND_ONLY_TEAMS
    parts = [cover(data, scope_label(team, agent, name), now), "<h2>ملخص الفترة</h2>",
             summary_table(data["summary"], data["previous"], OUT_ROWS if outbound_only else TEAM_ROWS)]
    if outbound_only:
        parts.append('<p class="muted">ما بعد البيع: مكالمات صادرة فقط (لا تستقبل مكالمات واردة).</p>')
    if not agent and not team and len(kpi.team_names(teams)) > 1:
        per_team = {t: kpi.compute(session, period.start, period.end, teams=teams, team=t)["summary"]
                    for t in kpi.team_names(teams)}
        parts += ["<h2>حسب الفريق</h2>", teams_table(per_team)]
    if not outbound_only:
        parts += ["<h2>المكالمات الواردة يومياً</h2>", day_chart(data["days"], "رنات فائتة" if agent else "لم يتم الرد")]
    if data["by_status"] is not None and not outbound_only:
        parts += ["<h2>حالات المكالمات الواردة</h2>", status_table(data["by_status"])]
    parts += ["<h2>الموظفون</h2>", agents_table(data["agents"], outbound_only),
              "<h2>أكثر الأرقام تكراراً</h2>", repeaters_table(data["top_repeaters"])]
    fu = data.get("followup") or {}
    if fu.get("enabled") and fu.get("active"):        # show-only until the admin sets followup_kpi_since
        parts += ["<h2>المتابعة ومعاودة الاتصال</h2>", followup_table(fu, data["agents"])]
    parts.append('<ol class="notes">' + "".join(f"<li>{escape(n)}</li>" for n in FOOTNOTES) + "</ol>")
    if not agent:
        for row in data["agents"]:
            cur = kpi.compute(session, period.start, period.end, teams=teams, agent=row["ext"])
            prev = kpi.compute(session, prev_p.start, prev_p.end, teams=teams, agent=row["ext"])["summary"]
            parts.append(agent_section(row, cur, prev))
    title = f"CallSight {period.kind} {period.start.isoformat()}"
    return Template(TEMPLATE.read_text(encoding="utf-8")).substitute(title=escape(title), body="\n".join(parts))


def _allowed(url: str) -> bool:
    """Only our bundled font files may load: no other local file, no network (SSRF / file-leak guard)."""
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    u = urlparse(url)
    if u.scheme != "file":
        return False
    path = Path(url2pathname(u.path)).resolve()
    return path.is_relative_to(FONTS.resolve()) and path.is_file()


def _fetcher():
    from weasyprint.urls import URLFetcher

    class FontsOnly(URLFetcher):
        def fetch(self, url, headers=None):
            if not _allowed(url):
                raise ValueError("blocked resource in report HTML")
            return super().fetch(url, headers)

    return FontsOnly(allowed_protocols=("file",), fail_on_errors=True)


def render_pdf(html: str) -> bytes:
    """HTML -> PDF bytes. WeasyPrint needs Pango (in the Docker image; not on the Windows PC)."""
    from weasyprint import HTML
    return HTML(string=html, base_url=FONTS.as_uri() + "/", url_fetcher=_fetcher()).write_pdf()


def make_pdf(session: Session, period: kpi.Period, teams: TeamConfig, team: str | None = None,
             agent: str | None = None, now: datetime | None = None) -> bytes:
    return render_pdf(build_html(session, period, teams, team=team, agent=agent, now=now))


def filename(period: kpi.Period, team: str | None = None, agent: str | None = None) -> str:
    scope = f"agent-{agent}" if agent else (team or "all")
    if period.kind == "range":
        last = (period.end - timedelta(days=1)).isoformat()
        return f"callsight-{period.start.isoformat()}-to-{last}-{scope}.pdf"
    return f"callsight-{period.kind}-{period.start.isoformat()}-{scope}.pdf"
