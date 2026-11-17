# Spec: Rollout — agents on the system, the PC switched off

**Status:** approved (2026-11-18, user: "approve all" — every open question = the recommendation) · **Plan step:** 2 of [finish plan](../../product/finish-plan.md)
**Roadmap items:** Phase 7 "Create the agent accounts, set the follow-up start date"; AWS spec task 11 "stop the PC
services".

## Goal
Real agents log in every day and fill their follow-ups, so Phase 7 "Done when" (2 weeks of real forms) can start
counting. The old PC copy stops, so there is one system only.

## Scope
**In:** agent and supervisor accounts, follow-up start date, a first-login password change, a short agent guide,
a browser check of every page with a real agent account, stopping the PC services, deciding about the PC's data.
**Out:** new pages or new rules for agents.

## Requirements
1. **Accounts** on the users page (المستخدمون): agents 104, 116, 119 (names from the PBX list) and the supervisor
   login for مروان حسني. Claude creates them on the user's list; passwords go to the user once, never in GitHub logs.
2. **First login:** a new account must set its own password on first login (new small feature: flag
   `must_change_password`, one page). Admin "new password" sets the flag again.
3. **Follow-up start date** set by the admin (from that date the follow-up forms are required and counted).
4. **Agent guide:** one page `docs/agent-guide.md` (English, with the Arabic button names as on screen): log in,
   the follow-ups page, the form, priority, callback, forward, notes. The user may translate it for the team.
5. **Browser check** with a real agent account: follow-ups page, form, side panel, shared notes, تحتاج حل,
   forward form. Every problem found → fixed in this spec or listed for later.
6. **PC switch-off:** `docker compose down` on the PC after the user's yes. The PC's DB is dumped once to
   `data/db-backups/` and kept (it holds calls from before 2026-11-15 that AWS does not have).

## Acceptance criteria
- [ ] Each agent logs in, is forced to change the password, sees only المتابعات
- [ ] pytest: `must_change_password` blocks every API except change-password until done
- [ ] Follow-up start date set; the first real forms appear on the supervisor's page
- [ ] Browser check list in `tasks.md` ticked with screenshots
- [ ] PC services stopped; final PC DB dump exists and `backup verify` restores it

## Open questions
1. Old PC data (before 2026-11-15): **recommended: keep the dump on the PC only** (start-empty on AWS was the
   user's choice). Other choice: import the old calls into AWS so the KPIs show history.
2. Who else needs an account (more agents, managers)? The user gives the list.
