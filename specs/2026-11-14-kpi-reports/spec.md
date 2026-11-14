# Spec: KPI engine, dashboard and PDF reports

**Roadmap items:** Phase 4, all four items (KPI engine, KPI dashboard, "Export report" PDF, scheduled reports)
**Status:** draft

## Goal
One click gives a correct weekly or monthly report per agent and for the team (mission: "Reports are manual" →
"Button → weekly / monthly PDF report per agent and team"). Numbers follow the KPI contract in `product/mission.md`.
Phase 4 **Done when:** KPIs for a test week match a hand calculation exactly; PDF (Arabic RTL) generates in < 30 s;
weekly/monthly reports also auto-generate on schedule.

## Facts (checked in the code 2026-11-14)
- `call` table already has per call: `direction`, `team`, `agent_ext` (who answered), `missed_agent_ext`
  (the agent who was rung and didn't answer, where known), `queue_number`, `customer_number`, `start_time`,
  `wait_seconds`, `talk_seconds`, `hold_seconds` (**always NULL**: the CDR has no hold time), `status`
  (answered / abandoned_queue / abandoned_ivr / no_answer / busy / voicemail).
- Team membership from `config/teams.yaml`: customer_service = 104, 119 (queue 6401); after_sales = 116. Supervisor 114.
- Times are stored in UTC; days and weeks are Riyadh time (`day_bounds`). Saudi work week starts **Sunday**.
- No satisfaction score exists yet (Phase 3).
- Phase 1 "Done when" (7 nightly reconciles OK) is not met yet, so data completeness is not proven.

## Scope
- In:
  - `callsight/kpi.py`: KPI functions for a period (date range) × scope (team / one agent / whole team set),
    plus the previous period of the same length for the trend.
  - API: `GET /api/kpi?period=week|month&start=YYYY-MM-DD&team=&agent=` → KPIs + previous period + per-day series
    + per-agent table (admin + supervisor).
  - **KPI page** in the web app ("المؤشرات"): week/month picker, team + agent filter, KPI tiles with change vs
    previous period, calls-per-day bar chart, status breakdown, per-agent table. Charts with **Apache ECharts**
    (already in the tech stack).
  - **PDF export**: `GET /api/reports/pdf?...` → Arabic RTL PDF (team summary + one section per agent), made with
    **WeasyPrint** from an HTML template; charts are simple SVG bars drawn in Python; bundled IBM Plex Sans Arabic TTF.
  - **Scheduled reports**: Celery beat, **Sunday 07:00 Riyadh** = previous week (Sun–Sat), **1st of month 07:00** =
    previous month; PDFs saved to `REPORTS_DIR/YYYY/` and listed in a `report_archive` table; archive list +
    download on the KPI page.
  - CLI: `python -m callsight report --period week|month --start YYYY-MM-DD` (writes the PDF, prints the KPIs) —
    used for the hand-calculation check.
- Out:
  - Satisfaction KPI values (Phase 3): shown as "غير متاح" (n/a), never 0, until scores exist.
  - Hold time: the CDR has none → AHT = talk time only, footnote in the report.
  - E-mailing reports (no mail server; later idea).
  - Agent login seeing own KPIs (agent role is Phase 6).

## KPI definitions (from mission.md, made exact for our data)
| KPI | Team / all | Per agent |
|---|---|---|
| Calls | inbound calls of the team (any status) in the period, counted by `start_time` (Riyadh) | inbound calls answered by the agent (`agent_ext`) |
| Answered | inbound `status = answered` | same as Calls for the agent |
| Unanswered | inbound with status in no_answer, abandoned_queue, abandoned_ivr, busy, voicemail | inbound calls where `missed_agent_ext = agent` |
| Answer rate | answered ÷ calls | — |
| AHT | mean `talk_seconds` (+ `hold_seconds` when not NULL) over answered inbound+outbound calls | same, agent's answered calls |
| Avg wait | mean `wait_seconds` of answered inbound calls | — |
| Outbound | outbound calls (count, answered) — extra, not in the mission table | per agent |
| Repeat calls 72h | answered inbound call whose `customer_number` had **another team call (any direction/status) in the previous 72 h**; counted against the agent who handled that **earlier** call (if it was answered) | count attributed to the agent of the earlier call |
| Avg satisfaction | n/a until Phase 3 | n/a |

Internal calls are excluded everywhere. Calls with no customer number never count as repeats.

## Requirements
1. `kpi.compute(session, start, end, team=None, agent=None)` returns all KPIs above; pure SQL aggregates, one
   query per KPI group; works for 0 calls (rates = null, not division errors).
2. Period helpers: week = Sunday..Saturday containing `start`; month = calendar month; previous period computed.
3. API endpoints admin + supervisor only (same auth as call list); input validated (dates, period, ext format).
4. KPI page: Arabic RTL, filters apply immediately (no "show" button — user preference), loads < 2 s for a month.
5. PDF: A4, RTL, Arabic shaped correctly (verified by extracting the text back), cover with period + generated-at,
   team table, per-agent sections, footnotes (AHT without hold; satisfaction n/a; data not yet reconciled 7 days
   if true). Generation < 30 s for a month.
6. Scheduled jobs write PDFs + archive rows; a re-run for the same period replaces the file (idempotent).
7. Every PDF download is audited (who, which report) like recording plays.
8. A fixed fixture week (from `tests/fixtures/cdr_shapes.json` style rows) has hand-calculated expected KPIs in the
   test; the test must match exactly.

## Acceptance criteria
- [ ] `pytest` passes, incl. KPI tests on a hand-calculated fixture week (every KPI, team + per agent, repeat-call
      attribution, empty period), period helpers (Sunday weeks, month edges, Riyadh midnight), API auth (no login 401,
      supervisor ok), PDF test (Arabic text extracted back equals the input; < 30 s)
- [ ] `python -m callsight report --period week --start <a real week>` prints KPIs and writes a PDF
- [ ] KPI page shows tiles + charts + agent table for a real week in the browser (screenshot)
- [ ] Beat schedule contains the weekly + monthly jobs; running the task by hand creates archive rows + files
- [ ] Hand calculation for one real week matches the report ⚠ NEEDS USER (or done by Claude from raw SQL and listed)

## Technical approach
- `callsight/kpi.py` (queries), `callsight/reports/` (`pdf.py`, `templates/report.html`, `charts.py` for SVG,
  `fonts/IBMPlexSansArabic-*.ttf`).
- Migration **0004** `report_archive` (+ `report_download` audit) — additive only; `pg_dump` before applying on the
  live DB.
- API image: apt `libpango-1.0-0 libpangoft2-1.0-0` (+ fonts) for WeasyPrint; `REPORTS_DIR=/data/reports` volume.
- Web: `echarts` npm package (pinned exact version), new `KpiPage.tsx`, nav link.
- Risks: WeasyPrint system libs on slim image; Arabic + numbers mixing (bidi) in tables; repeat-call query cost
  (index on `customer_number, start_time`).

## Open questions (to be decided by the 2 judges on the user's behalf)
1. **Week start:** Sunday (Saudi work week) — recommended — or Monday (ISO)?
2. **AHT includes outbound calls?** Recommended: yes for per-agent AHT (it is the agent's handling time), team AHT
   on inbound only? → Recommended single rule: inbound + outbound answered calls, both shown separately in the table.
3. **Repeat definition "another call within previous 72 h"**: count prior calls of any status (recommended: a
   customer who could not get through and calls again is also a repeat) or only prior answered calls?
4. **Report time:** Sunday 07:00 / 1st 07:00 Riyadh (recommended) — the PC may be off then; the job also runs at
   API start-up for any missed period ("catch-up").
5. **New tools:** WeasyPrint (already listed 🔜), ECharts (listed 🔜), `pypdf` for the test only (new, test-only).
