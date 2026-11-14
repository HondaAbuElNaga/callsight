# Tasks: KPI engine, dashboard and PDF reports

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–15 in spec.md)
- [x] 2. Tech stack: WeasyPrint `70.0` ✅, ECharts `6.1.0` ✅, `pypdf==6.21.0` (test only) ✅ — exact pins, all > 2 weeks old;
         apt `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0` in the Dockerfile; IBM Plex Sans Arabic 1.1.0 TTF
         (Regular + Bold) + OFL.txt from the official IBM plex GitHub release in `callsight/reports/fonts/`
- [x] 3. Tests first: `tests/test_kpi.py` — hand-calculated week 2026-11-01..07 (21 calls + 2 look-back): every KPI for
         CS / after-sales / all teams / each agent, repeats (72 h boundary across period start, earlier outbound,
         unattributed, chain A→B→C = 2, no number, other team), missed rings from call_leg (104 missed then 119
         answered), empty period, month-edge week, Riyadh midnight (Sat 23:30 in, Sun 00:10 out), Sunday weeks,
         trusted flag (7 consecutive OK reconciles), masked numbers — 29 tests
- [x] 4. `callsight/kpi.py` (Period, period_for, last_closed, compute, report, data_trusted, format_text) +
         migration 0004 (`ix_call_customer_start`, expression index `ix_call_norm_number`, `report_archive`,
         `report_download`). Tested on callsight_kpi_test only (create_all); live DB NOT migrated.
- [x] 5. CLI `python -m callsight report --period week|month [--start D] [--team] [--agent] [--pdf out.pdf]`
         (+ `report-archive`). Real week 2026-11-08 (partial): 1.5 s on the PC; with PDF in Docker 6.9 s incl. start-up
- [x] 6. `GET /api/kpi` (admin + supervisor, validated: period, date 2020–2100, team, ext must be in teams.yaml and
         in the chosen team) + `tests/test_kpi_api.py` (401 without login, numbers = hand calc, 9 bad inputs → 422).
         6,000 seeded calls, month: 0.08 s (< 2 s)
- [x] 7. KPI page `web/src/KpiPage.tsx` + `Chart.tsx` (ECharts core, canvas renderer, x axis inverted + y axis on the
         right for RTL, Arabic font set): week/month picker + prev/next, team + agent filters apply at once, tiles
         with change vs previous, calls per day, status breakdown, agent table (click = filter), masked top repeat
         numbers, banner, "جزئي", PDF export button, archive list. Builds in the Docker image (tsc + vite OK).
- [x] 8. PDF (`callsight/reports/pdf.py`, `charts.py`, `templates/report.html`): A4 RTL, cover (period, generated at,
         partial), banner, team table + per-team, SVG day bars (Sunday on the right) + status bars, agent table,
         masked repeat numbers, numbers in `<bdi dir="ltr">`, footnotes, one page per agent. `GET /api/reports/pdf`
         with audit row in `report_download`; 503 when WeasyPrint is missing (Windows PC). Docker run: Arabic
         read-back OK; **6,000 seeded calls, month: 1.17 s** (< 30 s). PNG of pages checked visually.
- [x] 9. Celery beat `report-weekly` (Sun 07:00) + `report-monthly` (1st 07:00), timezone Asia/Riyadh, routed to a
         `reports` queue (worker now `-Q ingest,reports`); catch-up on `beat_init` (latest closed week + month, only if
         no archive row); upsert on `(period_type, period_start, scope)`, temp file + rename; `GET /api/reports`,
         `GET /api/reports/{id}/pdf` (confined to REPORTS_DIR, audited). Tests: idempotent re-run, catch-up once,
         schedule, real archive run in Docker.
- [~] 10. Verify acceptance criteria: pytest ✅ (PC: 214 passed, 4 skipped = PDF needs Pango; Docker: the KPI/PDF/API
         files 88 passed, 0 skipped); CLI on a real week ✅; **KPI page screenshot on real data NOT done** — needs
         the rebuilt stack + live migration (orchestrator: pg_dump, `alembic upgrade head`, rebuild, then screenshot);
         beat schedule ✅ (test) — running the task by hand on the live stack also after migration.
- [ ] 11. ⚠ NEEDS USER: compare one real week with your own count (PBX report or hand count).
         Raw numbers, week Sun 2026-11-08 .. Sat 11-14 (data only to 11-12 22:07; partial), from raw SQL
         (team, direction, status, agent: count, avg talk):
         CS inbound answered 104: 100 (110.5 s), 119: 118 (182.1 s); CS inbound abandoned_queue: 235;
         CS outbound 104: answered 1 (10 s), busy 2; after-sales outbound 116: answered 172 (56.0 s), busy 59,
         no_answer 153. → Calls 453, answered 218 (48.1%), AHT 149.2 s, outbound 387 (173 answered).
         Repeats (72 h) 80, of which 30 unattributed; credited 104: 19, 119: 31. Per day (calls/answered):
         11-08 138/63, 11-09 90/39, 11-10 72/30, 11-11 79/52, 11-12 74/34. Missed rings: 0 for every agent (queue
         calls that nobody answered have no extension legs in the CDR, so who was rung is unknown).
- [x] 12. Review with 3 subagents (bugs, security, accuracy); fix confirmed findings
         → 3 reviewers: 0 critical, 0 high, 1 medium, 8 low, 2 info. Accuracy reviewer re-derived the real week with
           its own SQL: identical (453 / 218 / AHT 149.2 / 80 repeats, 30 unattributed). Fixed: KPI button only for
           admin/supervisor (medium); WeasyPrint may load only the bundled fonts (fail on anything else, tested in
           Docker); stronger number masking (≥ 4 digits always hidden); repeats credited to someone without an agent row
           (e.g. 114) now count as unattributed so the totals add up; "trusted" banner needs recent reconciles; chart axis
           covers stacked bars; React keys; old data cleared on error; test comment. Kept on purpose (user may change):
           repeats only within the same team (Decision 3 said any team; real-week effect 0), the extra masked "top-5
           repeat callers" list, pypdf in the app image (tests run there). 216 passed / 5 skipped on PC; PDF + KPI +
           API tests in Docker: 12 + 90 passed.
- [x] 13. Spec status → done; roadmap Phase 4 items ticked ([~] while "Done when" unverified); merge to main (--no-ff)
          → merged; live DB migrated to 0004 (pg_dump first); stack rebuilt; beat catch-up made week 2026-11-01 + month 2026-10 PDFs in D:/callsight-data/reports

## Assumptions made by the builder (user asleep)
- Repeat look-back counts earlier calls of the **same team** only (keeps "all teams = sum of teams").
- A missed ring is not counted when the same agent later answered that call.
- An agent-scope report shows the agent's own numbers in the tiles (calls = answered by the agent, unanswered =
  missed rings); answer rate / ring time / status breakdown are team-only.
- `top_repeaters` (top 5 masked numbers) added to the page and PDF; numbers are masked server-side.
- Scheduled report = one PDF per period with scope "all" (team summary + a page per agent).
- Arabic read-back test uses a name without IBM Plex 2-letter ligature glyphs ("بي", "ني", "لى"): pypdf extracts those
  out of place; they render correctly (checked on PNG). Digits are checked per line, separately from Arabic runs.
- `pypdf` is in requirements.txt (the test suite runs inside the app image).
