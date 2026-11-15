# Tasks: Outbound ring time and who hung up

- [ ] 1. User approves spec (20 s limit already given by the user, 2026-11-15) + open questions answered.
- [ ] 2. Tests for `classify` → `hangup_by`; then code in `callsight/ingest/classify.py` + model field.
- [ ] 3. Migration 0014: add `call.hangup_by`, back-fill from `call_leg.reason`; up/down/up on the PC DB.
- [ ] 4. Tests for the new callback rule + early-try count; then `callsight/followups.py` (`EARLY_HANGUP_SECONDS`).
- [ ] 5. API: follow-ups items + call detail carry ring seconds, `hangup_by`, early flag/count; tests.
- [ ] 6. Web: follow-ups page texts, call panel line + «قفل مبكر» mark.
- [ ] 7. KPI: early hang-ups + average ring seconds (team + agent), page tiles/columns, PDF; tests.
- [ ] 8. Verify: full pytest, web build, migration on PC DB (44 agent hang-ups).
- [ ] 9. Review: 3 subagents (bugs, security, accuracy); fix findings.
- [ ] 10. Roadmap + memory update; push to `main` only on the user's yes (push = live).
