# Tasks: Priority + "لا تحتاج متابعة" visible

- [x] 1. User approved spec.md (2026-11-16; all 14 questions answered in the spec).
- [x] 2. Branch `spec/2026-11-16-priority-no-followup` from up-to-date `main`; commit spec + tasks. → from origin/main ddb5474.
- [x] 3. Tests first: priority validation (required, bad code, no_followup → NULL, note rejects priority, supervisor edit),
        API output `priority` / `priority_ar`.
- [x] 4. Model column + migration 0015 (check constraint, downgrade); `PRIORITIES` table; validate / form_dict / history.
- [x] 5. `FollowupIn.priority`; tests of task 3 pass. → part A commit 7daa45a (+ priority filter, 1-day rule).
- [x] 6. Tests first: `counts.no_followup`, `state=no_followup`; then the code in `api/work.py`.
- [x] 7. Tests first: KPI `no_followup` + `no_followup_rate`; then `followups.kpi`; PDF two columns + test.
- [x] 7b. Tests first: `unsolved` (dates ignored, leaves when solved, agent/supervisor scope, order); then the SQL
        pre-filter + `fu.rows(call_ids=...)` in `api/work.py`.
- [x] 8. Web: "تحتاج حل" side section (wide: side column, phone: above the list), plus: types, labels, priority select in the first form, tag in the list, counter + filter, KPI columns + text.
- [ ] 8b. Part 4 (forward to a person): API received 2026-11-16 (spec "Part 4: the other system's API"); only send
        is covered, M1 (accountGuid by phone) blocks sending, M2–M4 block solved / questions / re-send. Waiting for the
        developer's answers + user approval. Then: write
        requirement 12 in full (endpoints, auth, solved callback), user approves, tests first, build. Parts 1–3 can go
        live before part 4.
- [x] 9. Verify: migration up/down/up, full pytest, web build; tick acceptance criteria with real output.
- [x] 10. Review: 3 subagents (bugs, security, accuracy) per part. → A: 2 low (2-day limit re-checked on an
        unchanged old time; untested path) fixed. B: 1 low (priority filter emptied the no-follow-up view) fixed.
        C: 1 low security (unbounded unsolved query) fixed, 1 medium + 2 low accuracy (oldest-first tie-break not
        tested; no state tag in the side section; no time for no_number) fixed. 7 findings in all (all confirmed + fixed), 0 rejected.
        Verify: 705 passed, 1 known `.env` failure, 9 skipped; web build OK; migration up/down/up OK.
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
