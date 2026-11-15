# Tasks: Follow-up stages per call + agent page fixes

Branch `spec/2026-11-15-followup-stages` from `main` (04414b5). Review diff = `git diff main...HEAD`.

- [x] 1. Spec approved by the user (2026-11-15, recommended answers)
- [x] 2. Migration 0011 + model `CallFollowupNote`; add to `retention.CONTENT_TABLES`; up/down/up on a scratch DB
- [x] 3. Tests first: steps + moves + round-2 anchor + note deadline (`tests/test_followups.py`); note API, list
         look-back, counters, auto-seen share (`tests/test_agent_supervisor_api.py`); KPI numbers; retention
         → 11 new in test_followups.py, 5 new in test_agent_supervisor_api.py (+ review-fix tests, task 9), note route added to the agent route walk
- [x] 4. `followups.py`: `step()`, rounds ≥ 2 callbacks, notes in `rows()` / `as_dict()`, new KPI numbers
- [x] 5. API `work.py`: POST note, "needs action" view with 62-day look-back, counters, H message flag, share seen on open
- [x] 6. KPI page + PDF: new follow-up columns
- [x] 7. Web: call panel (step + history + note box, form first for agents), follow-ups page (C, D, E, F, H, J, K, L)
- [x] 8. Verify: full pytest 660 passed, 9 skipped, 1 failed = the known `test_llm_config_from_env` (PC `.env`);
         web build in Docker (`tsc -b && vite build`) OK; migration up → down 0010 → up on a scratch DB OK.
         NOT done: the by-hand browser check (no browser tool in this session; the PC's own services run the live
         migration-0010 DB, which this check would have to change). The user checks it after the deploy.
- [x] 9. Review: 3 subagents (bugs, security, accuracy) → security: no findings; bugs + accuracy: 9 findings
         (0 critical, 0 high, 2 medium, 7 low), merged to 7 distinct, all confirmed and fixed: form frozen once a note
         exists (PUT → 409, both reviewers); a call made before a "not solved" form is not its callback (search
         from the form's save time); finished rounds keep "answered"; KPI "open" leaves out no_number (spec table
         now lists no_number); the list reuses the 62-day query when من/إلى is inside it; round ≥ 2 anchor written in
         the spec as built (after the note and after the previous callback); test count 12 → 11. New tests: 3 in
         test_followups.py, 1 in test_agent_supervisor_api.py (wrong round via API 409, frozen form, per-agent KPI
         split). After the fixes: full pytest 664 passed, 9 skipped, 1 failed (the known `.env` test); web build in
         Docker OK. No second round (nothing critical/high).
- [~] 10. Spec → done, roadmap updated, branch pushed + PR. Merge to `main` (= live) only on the user's yes
