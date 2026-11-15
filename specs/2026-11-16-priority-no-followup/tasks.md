# Tasks: Priority + "لا تحتاج متابعة" visible

- [x] 1. User approved spec.md (2026-11-16; all 14 questions answered in the spec).
- [ ] 2. Branch `spec/2026-11-16-priority-no-followup` from up-to-date `main`; commit spec + tasks.
- [ ] 3. Tests first: priority validation (required, bad code, no_followup → NULL, note rejects priority, supervisor edit),
        API output `priority` / `priority_ar`.
- [ ] 4. Model column + migration 0015 (check constraint, downgrade); `PRIORITIES` table; validate / form_dict / history.
- [ ] 5. `FollowupIn.priority`; tests of task 3 pass.
- [ ] 6. Tests first: `counts.no_followup`, `state=no_followup`; then the code in `api/work.py`.
- [ ] 7. Tests first: KPI `no_followup` + `no_followup_rate`; then `followups.kpi`; PDF two columns + test.
- [ ] 7b. Tests first: `unsolved` (dates ignored, leaves when solved, agent/supervisor scope, order); then the SQL
        pre-filter + `fu.rows(call_ids=...)` in `api/work.py`.
- [ ] 8. Web: "تحتاج حل" side section (wide: side column, phone: above the list), plus: types, labels, priority select in the first form, tag in the list, counter + filter, KPI columns + text.
- [ ] 8b. Part 4 (forward to a person): BLOCKED until the user sends the other system's API (2026-11-17). Then: write
        requirement 12 in full (endpoints, auth, solved callback), user approves, tests first, build. Parts 1–3 can go
        live before part 4.
- [ ] 9. Verify: migration up/down/up, full pytest, web build; tick acceptance criteria with real output.
- [ ] 10. Review: 3 subagents (bugs, security, accuracy) on `git diff main...HEAD`; fix confirmed findings.
- [ ] 11. Spec status done, roadmap "Current position", push to `main` only on the user's yes (push = live deploy).

## How the user wants this built (2026-11-16)
- Build part by part: (A) priority + filter + 1-day rule, (B) "لا تحتاج متابعة" counter + KPI column, (C) "تحتاج حل"
  section + supervisor one-line view, later (D) part 4 when the API arrives.
- **After each part:** review the code with 3 subagents (bugs, security, accuracy), fix confirmed findings, then go on
  to the next part without waiting.
- **Any recommendation / open choice that comes up:** let 3 judge subagents pick the best solution, record it in the
  spec, and continue (do not stop to ask the user).
- Branch from up-to-date `main` (the PC is on `fix/live-login-gate`, whose commit 376fb94 is not on `main` yet).
- Push to `main` (= live) only on the user's "ok push".
