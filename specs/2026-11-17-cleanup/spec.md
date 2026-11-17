# Spec: Cleanup — small bugs, open branches, docs up to date

**Status:** approved (2026-11-18, user: "approve all" — every open question = the recommendation) · **Plan step:** 1 of [finish plan](../../product/finish-plan.md)
**Roadmap items:** known small items in "Current position"; Phase 5 live board "queue waiting".

## Goal
Close every small open item before new features, so the next specs start from a clean, correct `main` and the
roadmap tells the truth.

## Scope
**In:** the bugs and open branches listed below; status of older specs; roadmap rewrite.
**Out:** new features; anything that needs a user decision not already given.

## Requirements
1. **Live board login wait:** `callsight/live/service.py` LoginGate retries after ~1 s when the lock key is gone
   (`ttl <= 0`) instead of waiting the full 600 s. Take commit 376fb94 from `fix/live-login-gate`.
2. **`/api/calls` for agents:** answers 403 for the agent role (agents have no calls list; a call still opens from
   المتابعات through its own endpoint). Every agent route still tested.
3. **KPI download log:** stores both من and إلى dates of the chosen range (today only the start date).
4. **Branch `ops/nlp-on`:** its workflow tasks (nlp-on, main-ps, gpu-update, blind-reset) merged into `main`.
5. **Queue waiting callers on the live board:** capture one real event (30011) while a customer waits in queue
   6401 (the user makes one test call into the queue while all agents are busy), save it as a test fixture
   (numbers masked), and fix the board if the waiting caller does not show.
6. **Spec status catch-up:** tick what is really done: `aws-deploy` tasks A3, 11–14; `ring-time` task 10;
   `rating-set` task 7; `asr-benchmark` task 9; `pbx-discovery` 11–13 (closed: replaced by the AWS work).
7. **Roadmap "Current position"** rewritten from today's real state (GPU live, rating set live, PR #2 live, ring
   time live, live board team-only) and pointing at [finish-plan.md](../../product/finish-plan.md).
8. **AWS spec tasks 15–16:** check every AWS acceptance criterion that does not need 7 days; 3-agent review of
   the deploy files (`deploy/aws/`, `.github/workflows/`).

## Acceptance criteria
- [ ] pytest: LoginGate retries within 2 s when the key is gone (fake Redis); agent → `/api/calls` = 403
- [ ] KPI download log row has `date_from` and `date_to` (test)
- [ ] `main` contains the `ops/nlp-on` and `fix/live-login-gate` work; both branches deleted after merge
- [ ] A real queue event is saved as a fixture; a test shows the waiting caller on the board
- [ ] Roadmap "Current position" matches the live server (checked with the `main-status` task)
- [ ] AWS spec tasks 15–16 ticked with real results; full pytest passes (except the known `.env` test)

## Technical approach
Small changes on branch `spec/2026-11-17-cleanup` from `main` (after step 0 is live, so the migrations line up).
The queue capture uses the live service's existing payload log, turned on for one test only.

## Open questions
1. Delete the old local branches and the 4 stale agent worktrees under `.claude/worktrees/`? **Recommended: yes**
   (all merged; nothing is lost, the commits stay in `main`).
