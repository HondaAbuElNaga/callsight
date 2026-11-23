# Tasks – customer-service employee KPI %

- [x] 1. User approves spec + open questions → approved 2026-11-23, all recommendations
- [x] 2. Tests first: scoring helpers (bands, AHT, average) → tests/test_agent_score.py
- [x] 3. Tests: 21:30 deadline (+ cut-over), update old deadline tests → test_deadline_2130_work_day_from_the_cut_over; old tests kept (before the cut-over)
- [x] 4. Tests: DB fixture for parts 1–3 and the work-day window → fixture day 104/119/116
- [x] 5. `followups.after_deadline` 21:30 rule + `DEADLINE_2130_FROM`
- [x] 6. `callsight/agent_score.py` + `kpi.report()` → `agent_score`
- [x] 7. API test: `/api/kpi` has `agent_score`, agent 403 → test_kpi_api asserts agent_score
- [x] 8. Web: types in `api.ts`, panel in `KpiPage.tsx`, 21:30 texts → tsc -b passes (Node in Docker; vite build runs in the Docker image)
- [x] 9. Docs: `docs/followup-form-and-forward.md` deadline, roadmap line (roadmap added after review)
- [x] 10. Verify: full pytest, tsc + build, every acceptance criterion → 998 passed, 9 skipped, 1 failed: test_llm_config_from_env fails on main too (.env-dependent)
- [x] 11. Review: 3 subagents (bugs, security, accuracy), fix confirmed → 4 low findings, all confirmed; 3 fixed, 1 = push checklist (cut-over date)
- [x] 12. Status done, roadmap, push only on the user's "ok push" — before pushing: `DEADLINE_2130_FROM` = go-live day → pushed 2026-11-23, cut-over 24/11 kept (push before it)
