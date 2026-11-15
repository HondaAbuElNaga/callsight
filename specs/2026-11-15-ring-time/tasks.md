# Tasks: Outbound ring time and who hung up

- [x] 1. User approves spec → approved 2026-11-15: 30 s, one try enough, early hang-up lowers KPI.
- [x] 2. Tests for `classify` → `hangup_by`; then code in `callsight/ingest/classify.py` + model field. → 7 new classify tests; 52 classify tests pass.
- [x] 3. Migration 0014: add `call.hangup_by`, back-fill from `call_leg.reason`; up/down/up on the PC DB. → PC DB went 0008 → 0014 (add-only), 0014 down/up OK; 44 calls hangup_by=agent + no_answer (41 under 30 s).
- [x] 4. Tests for the new callback rule + early-try count; then `callsight/followups.py` (`EARLY_HANGUP_SECONDS`). → old 10 s rule replaced by proper_try_sql/early_hangup_sql (NULL-safe); 47 follow-up tests pass.
- [x] 5. API: follow-ups items + call detail carry ring seconds, `hangup_by`, early flag/count; tests. → `ring` on every call row (null for non-outbound); callback/history carry ring_seconds, hangup_by, early_tries.
- [x] 6. Web: follow-ups page texts, call panel line + «قفل مبكر» mark. → format.ts ringText/earlyText; FollowupsPage, WorkPanels history, CallsPage panel «الرنين» + «قفل مبكر».
- [x] 7. KPI: early hang-ups, "محاولات صحيحة %" + average ring seconds (team + agent), page tiles/columns, PDF; tests. → early_hangups, proper_rate, outbound_avg_ring (team/agent/one agent); KPI page tiles + columns; PDF rows, columns, footnote.
- [x] 8. Verify: full pytest, web build, migration on PC DB (44 agent hang-ups). → full pytest 687 passed, 9 skipped, 1 failed (known `.env` test); web build (tsc + vite) in Docker OK.
- [x] 9. Review: 3 subagents (bugs, security, accuracy); fix findings.
  → security: 0. bugs + accuracy: 3 medium + 8 low, all confirmed and fixed: unknown hang-up no longer blamed on the
    agent (early = hangup_by agent only, as spec req 2); outbound ring seconds never fall back to the whole leg time
    (PC data: 0 calls affected); avg ring + proper % added to every team/agent tile and table (page + PDF); round-1
    history no longer shows ring text next to 'answered'; PDF test checks the tables, not the footnote; API test checks
    agent access; spec numbers/wording fixed (91 = 55-70 s, 87 at 61-63 s; 44 = not answered). Re-test: 687 passed,
    9 skipped, 1 failed (known `.env` test); web build OK.
- [ ] 10. Roadmap + memory update; push to `main` only on the user's yes (push = live).
