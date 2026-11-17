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
- [x] 8b. Part 4 (forward to a person): API received 2026-11-16 (spec "Part 4: the other system's API"); only send → built as D1–D4 + the chain.
        is covered, M1 (accountGuid by phone) blocks sending, M2–M4 block solved / questions / re-send. Waiting for the
        developer's answers + user approval. Then: write
        requirement 12 in full (endpoints, auth, solved callback), user approves, tests first, build. Parts 1–3 can go
        live before part 4.
- [x] D0. Approved 2026-11-16 ("approve"). Still waiting: user sends CS_API_KEY + CS_USER_GUID (privately, to the server). → keys received 2026-11-18, on the server (server task link-keys).
- [x] D1. Tests first, then: forward_person table + sync, migration 0016, form kind "مشرف فرع" + drop-down + note,
        send job with retry, steps forwarded / forward_overdue, no solve by agent. → 3-agent review.
        → commits 80a3e04, 66fd728 + review round 2. Review: 14 findings (2 high, 5 medium, 7 low; 2 reported twice),
        all confirmed: fixed (stale "sent" lock, failed forward unrecoverable → step forward_failed, duplicate ticket
        after an unclear answer, 408/429, ticket answer checks, health page, "not sent yet" on lines, spec notes);
        callback-first order kept as a recorded choice. Round 2 (bugs): 2 more (failed + notes: other answers via a
        note; 429 on the ticket retried), fixed. Verify: 733 passed, 1 known .env failure, 9 skipped; web build OK;
        migration 0016 up/down/up OK.
- [x] D2. Tests first, then: ticket polling + forward_event, ingest of supervisors' calls (team `forward`, hidden from
        team views), solved check (closedAt + last call rules), forward_solved / unconfirmed. → 3-agent review.
  Review 2026-11-16: 3 agents, 15 findings; fixed (see spec Build decisions D2). Not fixed: "تحتاج حل" pre-filter still loads confirmed forward solves (done with the chain's per-hop result); counter = closed today.
- [x] DC. Forward chain (spec "Part 4 change"): all employees in the drop-down, forward_hop table, 24 h → branch
  supervisor → 4 managers (copy tickets, first close wins), per-hop polling/proof/watch. Waits for the user's approval
  and the 4 managers' names.
  Review 2026-11-16: 3 agents, 29 findings (no high); fixed or recorded in spec Build decisions DC. 770 passed.
- [x] DP. Forwards page (spec "Part 4: forwards page"): archive list + filters, story of one problem, numbers per
  employee / branch supervisor / manager / branch. Built + 3-agent review fixed 2026-11-16.
- [x] D3. Tests first, then: tracker step list, questions/answers both ways, AI summary added once, one-line view.
        → 3-agent review. Built 2026-11-16 (commit 2c5b281 + review fixes). Review: accuracy 13 findings (no high),
        security 7 (no high): fixed (labels, counter ignores priority, tracker for pending/failed, agent name, no
        login name on the ticket, notes only from the call's agent + warning, answer needs a question, summary =
        shown analysis and not repeated, erase lock, prefixes without diacritics). The bugs reviewer stopped on a
        usage limit: re-run together with D4's review.
- [x] D4. Tests first, then: forward people KPI + late hops (re-send / escalation replaced by the chain, DC).
        → 3-agent review. Built 2026-11-16 (tests/test_forward_d4.py). Review (bugs over D3+D4, security, accuracy):
        34 findings, none high except 2 accuracy (fair time split missing; agents blamed for solved questions):
        all fixed or recorded in spec "Review of D3 + D4". Verify: 820 passed, 1 known .env failure, 9 skipped;
        web build OK; migration 0020 up/down(0016)/up OK.
- [x] DT. Training lookup (spec "Part 4: the training lookup", user 2026-11-16): `callsight/training_api.py`,
        `forward.suggest`, `GET /api/calls/{id}/student`, student box + pre-pick in the form, `.env` keys. Tests
        `tests/test_training.py`. Review: 3 agents, 0 high, all findings fixed (spec "Part 4: the training
        lookup", Review). Real look-up tested: 200 with the key, `trainers` still empty on their side.
- [~] D5. On the server: put the key, sync people, one real test ticket, then live on the user's "ok push". → live 2026-11-18 (main 012429c); keys on the server (server task link-keys); forward-sync: 473 employees, 18 branch supervisors, 3 managers; the 7 exts linked by account id later the same day (f6afec0). Left: one real test ticket from an agent.
- [x] 9. Verify: migration up/down/up, full pytest, web build; tick acceptance criteria with real output.
- [x] 10. Review: 3 subagents (bugs, security, accuracy) per part. → A: 2 low (2-day limit re-checked on an
        unchanged old time; untested path) fixed. B: 1 low (priority filter emptied the no-follow-up view) fixed.
        C: 1 low security (unbounded unsolved query) fixed, 1 medium + 2 low accuracy (oldest-first tie-break not
        tested; no state tag in the side section; no time for no_number) fixed. 7 findings in all (all confirmed + fixed), 0 rejected.
        Verify: 705 passed, 1 known `.env` failure, 9 skipped; web build OK; migration up/down/up OK.
- [~] 11. Spec status done, roadmap "Current position", push to `main` only on the user's yes (push = live deploy). → live 2026-11-18 on the user's "ok push"; status "done" after D5.

## How the user wants this built (2026-11-16)
- Build part by part: (A) priority + filter + 1-day rule, (B) "لا تحتاج متابعة" counter + KPI column, (C) "تحتاج حل"
  section + supervisor one-line view, later (D) part 4 when the API arrives.
- **After each part:** review the code with 3 subagents (bugs, security, accuracy), fix confirmed findings, then go on
  to the next part without waiting.
- **Any recommendation / open choice that comes up:** let 3 judge subagents pick the best solution, record it in the
  spec, and continue (do not stop to ask the user).
- Branch from up-to-date `main` (the PC is on `fix/live-login-gate`, whose commit 376fb94 is not on `main` yet).
- Push to `main` (= live) only on the user's "ok push".
