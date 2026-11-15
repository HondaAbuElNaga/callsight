# Spec: Private note per call ("ملاحظتي")

**Roadmap item:** Phase 7 follow-on (idea: user, 2026-11-15).
**Status:** done on the branch; approved (user, 2026-11-15: "i need note in new column in each call ... not appear to any other, just
remember thing")

## Goal
Each user (mostly agents) can keep a short reminder on any call in the follow-ups list. Only the writer sees it.

## Requirements
1. Table `call_private_note` (migration 0012): call_id (cascade), user_id (cascade), text ≤ 300, updated_at; one note
   per (call, user). Erased with the call's content (`retention.CONTENT_TABLES`).
2. `PUT /api/calls/{id}/my-note` `{text}`: only on a call the user can see; empty text deletes the note.
3. `/api/followups` items carry `my_note` = the caller's own note only (never another user's, also not for staff).
4. Follow-ups page: new column "ملاحظتي", edit in place (click, type, save); clicking it does not open the call.

## Acceptance criteria
- [x] API tests: save / change / delete; another agent and the supervisor never see it; a call the agent cannot see → 404.
- [x] Retention removes it. Migration 0012 up/down/up. Full pytest green (except the known `.env` test), web build OK.
  → 666 passed, 9 skipped, 1 failed (known `.env` test); 0012 up → down 0011 → up OK; web build in Docker OK.
  Review: done by the builder only (small change), not by independent agents.

## Scope out
Notes in the call panel, sharing notes, note history.
