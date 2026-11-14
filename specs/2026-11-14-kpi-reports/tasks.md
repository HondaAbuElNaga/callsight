# Tasks: KPI engine, dashboard and PDF reports

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–15 in spec.md)
- [ ] 2. Tech stack: WeasyPrint, ECharts → ✅ when adopted; add `pypdf` (test only); exact versions
- [ ] 3. Tests first: hand-calculated fixture week (every KPI, team + per agent, repeats incl. boundary/outbound/
         unattributed, missed rings from call_leg, empty period, month edge, Riyadh midnight, Sunday weeks)
- [ ] 4. `callsight/kpi.py` (periods + compute) + migration 0004 (indexes, report_archive, report_download)
- [ ] 5. CLI `python -m callsight report --period week|month --start YYYY-MM-DD [--pdf out.pdf]`
- [ ] 6. API `GET /api/kpi` (admin + supervisor, validated input) + tests
- [ ] 7. KPI page (ECharts, RTL, tiles + trend, calls-per-day, status breakdown, agent table, banner, partial flag)
- [ ] 8. PDF (WeasyPrint, IBM Plex Sans Arabic TTF, SVG bars, masked numbers, bdi numbers, footnotes, banner)
         + `GET /api/reports/pdf` + download audit + Arabic extraction test + < 30 s on 6,000 seeded calls
- [ ] 9. Scheduled weekly/monthly jobs + catch-up at beat start-up + archive list/download (may be [~])
- [ ] 10. Verify acceptance criteria (screenshot of KPI page on real data; CLI on a real week)
- [ ] 11. ⚠ NEEDS USER: compare one real week with your own count (PBX report or hand count) — Claude lists the raw
          numbers to compare in the final report
- [ ] 12. Review with 3 subagents (bugs, security, accuracy); fix confirmed findings
- [ ] 13. Spec status → done; roadmap Phase 4 items ticked ([~] while "Done when" unverified); merge to main (--no-ff)
