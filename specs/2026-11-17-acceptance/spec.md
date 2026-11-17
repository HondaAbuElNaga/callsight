# Spec: Acceptance — check every "Done when", close the project

**Status:** draft (2026-11-17) · **Plan step:** 10 of [finish plan](../../product/finish-plan.md) (last)
**Roadmap items:** each phase's "Done when"; AWS spec tasks 17–18; overnight list step 7 (hand-check one week).

## Goal
Prove with numbers that every phase does what the roadmap promised, write the guides someone else needs to run
the system, and close the project.

## Requirements (one check per phase)
1. **Phase 1:** 7 nights in a row of reconcile = OK on AWS (our call count = PBX count). Read from the health page.
2. **Phase 2:** transcript ready within the agreed time after hang-up (measured over 7 days: median and worst);
   WER of the chosen model written (see open questions 1–2).
3. **Phase 3:** from step 5 (blind test).
4. **Phase 4:** **KPI hand check (YOU):** the user counts one real week from the PBX's own report (or by hand); a
   compare page/command shows our numbers next to theirs per agent; every difference explained or fixed. PDF made
   in < 30 s; weekly report appeared on Sunday by itself.
5. **Phase 5:** from step 9.
6. **Phase 6:** restore drill (step 3) + load test: 2,000 calls/day of synthetic calls through ingest + KPI on a
   scratch DB on the main server (no PBX, no GPU), no backlog after 24 h of simulated load.
7. **Phase 7:** 2 weeks of real forms by agents (from step 2), follow-up KPIs on after HR OK (step 8).
8. **AWS:** after 7 days the real bill vs the ≈ $110/month estimate; latency; replace the estimate in `docs/aws.md`
   (AWS tasks 17–18); spec `aws-deploy` → done, merged.
9. **Guides:** `docs/admin-guide.md` (daily checks, server-task buttons, adding an agent, password reset, what to do
   if the PBX blocks us / GPU does not wake / disk full / server lost), `docs/agent-guide.md` (step 2).
10. **Close:** every spec status done, roadmap phases ticked, `product/roadmap.md` "Current position" = finished,
    memory updated.

## Acceptance criteria
- [ ] Each of requirements 1–8 has a written result (number + date) in `docs/acceptance.md`
- [ ] Any failed check has a fix spec or a written user decision
- [ ] Guides reviewed by the user

## Open questions
1. **Transcript time:** the roadmap says ≤ 15 min, the AWS design the user chose gives ≤ ~35 min (GPU wakes every
   30 min). **Recommended: change "Done when" to ≤ 35 min** in working hours (08:00–23:30); night calls at 08:00.
2. **WER:** the roadmap says ≤ 25 %; Whisper large-v3 measured 40.3 % (the user's choice, closed). **Recommended:
   change "Done when" to "the summaries and scores are good enough on the blind test"** and keep a fine-tuned
   Whisper on our own calls as a later idea, not part of this project.
