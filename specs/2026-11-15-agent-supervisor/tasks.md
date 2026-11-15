# Tasks: Agent + supervisor work

Branch `spec/2026-11-15-agent-supervisor`, started from `spec/2026-11-15-aws-deploy` (holds the latest status docs;
review diff = `git diff spec/2026-11-15-aws-deploy...HEAD`).

- [x] 1. Spec approved (3 judge agents on the user's behalf, 2026-11-15; decisions 1–12 in spec.md)
- [x] 2. Migration 0010 + models (call.content_deleted_at, call_followup, call_share, app_setting, admin_log);
         down_revision 0008 (the real head: 0007 → 0009 → 0008). up → down 0008 → up on a scratch DB: OK
- [x] 3. Tests first: `tests/test_followups.py` (needed calls, deadline, callback statuses at the edges, KPIs),
         `tests/test_work_api.py` (form, shares, agent summary, scope), `tests/test_admin_api.py` (users, settings,
         delete content); route walk in `tests/test_agent_role.py` extended
- [x] 4. `callsight/followups.py` (rules + queries + KPI numbers)
- [x] 5. Shared per-call erase (retention + delete), backup-copy worker task
- [x] 6. API: `admin.py` (users, settings, delete), `work.py` (follow-ups, shares, summary), shared-call visibility
- [x] 7. KPI + PDF columns (show-only until `followup_kpi_since`)
- [x] 8. Web: users page, follow-ups page, form / share / delete / summary in the call panel, KPI columns
- [x] 9. Verify: full pytest 626 passed, 9 skipped, 1 failed = `test_call_analysis.py::test_llm_config_from_env`
         (fails the same without this work: it reads the PC's `.env`, where NLP_ENABLED=true since 2026-11-15);
         new tests: test_followups.py 19, test_agent_supervisor_api.py 15, route walk extended. Migration up/down/up
         OK. Web build in Docker (`tsc -b && vite build`): OK
- [ ] 10. Review: 3 subagents (bugs, security, accuracy); fix confirmed critical/high
- [ ] 11. Roadmap + ideas updated, commit; push the branch (not `main`: `main` deploys live — ask the user)
