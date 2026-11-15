# Spec: Follow-up stages per call + agent page fixes

**Roadmap item:** Phase 7 "Agent + supervisor work", follow-on (idea: user, 2026-11-15).
**Status:** done on the branch (built + 3 reviews, 2026-11-15; NOT live until merged to `main`). Approved (user, 2026-11-15: "Yes, start" with Claude's recommended answers to the 4 open questions)

## Goal
Every call that needs follow-up goes through clear **steps**, and the agent always sees which step each call is at
and what they must do next. Today a call has one form and one callback check. After that, nothing tracks whether the
problem was solved after the callback. The user asked (2026-11-15) for this:
"first مطلوب → if noted solved, change → if not solved, add more notes → when the callback happens, change", plus
different KPI numbers for each path. The same work fixes the agent page problems found on 2026-11-15 (C–L).

## Facts (checked 2026-11-15)
- One form per call: table `call_followup` (unique `call_id`), with `solved`, `callback_at`, `responsible_*`
  (`callsight/followups.py`, `callsight/api/work.py`). The callback is the first outbound team call to the same
  number after the call ended (`_callback_lateral`). There is no record of what happened **after** the callback.
- The follow-ups list shows only calls whose start date is inside من/إلى (default: the last 7 days).
- The follow-up KPIs are: forms on time %, callbacks on time %, and "came back". They are show-only until
  `followup_kpi_since` is set, and the PDF shows them only after that.
- `retention.CONTENT_TABLES` erases follow-up text with a call's content. A new note table must be added there.
- AWS has no transcripts or summaries yet (no GPU server). Pushing to `main` deploys to the live server.

## The steps of one call (computed when read, never stored)

| Step | Arabic label | When |
|---|---|---|
| `required` | مطلوب | no form yet, deadline not passed |
| `overdue` | لم يُعبّأ في وقته | no form, deadline passed |
| `no_followup` | لا تحتاج متابعة | form = "no follow-up needed" (end) |
| `solved_first` | حُلّت من أول مكالمة | first form says solved (end) |
| `waiting_callback` | بانتظار معاودة الاتصال (جولة n) | latest note not solved, no callback call yet, grace not passed |
| `callback_missed` | معاودة فائتة | latest note not solved, callback time + 60 min passed, no callback call |
| `note_needed` | تم الاتصال – أضف ملاحظة | a callback call was found after the latest open note, no note for it yet |
| `solved_after` | حُلّت بعد المتابعة | latest note after a callback says solved (end) |
| `no_number` | بلا رقم للمعاودة | latest entry not solved but the call has no customer number (no callback can be checked; not counted as "open" in the KPI) |

- **Round 1** = the first form; its callback = first outbound team call after the original call ended (as today),
  except when the form says "not solved": then a call made **before the form was saved** does not count for the
  promise in the form (review finding, 2026-11-15) and the search starts at the form's save time.
- **Round n ≥ 2** = a note written after the callback of round n-1. If not solved, it sets a new callback time; its
  callback = first outbound team call to the same number that **starts after the note was saved and after the round
  n-1 callback call ended** (a second call made before the note was written is not the new round's callback).
- A late callback call (after `callback_missed`) still moves the call to `note_needed`; the KPI keeps it as late.
- The note after a callback has a deadline: the same rule as the form (23:00 Riyadh on the callback call's day, or
  2 h after it when that is later).

## Requirements
1. New table `call_followup_note` (migration 0011): call_id (cascade), round (≥ 2), cb_call_id (the callback call the
   note reports on), what_happened (≤ 500), solved, responsible_kind / responsible_user_id, expected_at,
   callback_at, created_by, created_at. Unique (call_id, round). Added to `retention.CONTENT_TABLES`.
2. Once a note exists, the round-1 form cannot be changed any more (PUT → 409; review finding).
   `POST /api/calls/{id}/followup/notes`: allowed only when the step is `note_needed` (the agent who owns the call or
   staff). Same checks as the form (text required; not solved → responsible + expected + callback in the future,
   ≤ 14 days). Notes cannot be edited (append-only; a mistake is fixed by the next note or by a supervisor note).
   409 in any other step.
3. Every follow-up row (list, call panel, KPI) carries `step`, `round`, `notes` (history), the current callback
   (round n) and the note deadline when `note_needed`.
4. **Call panel (agents first):** the step badge and the next action at the top. Then the history (form + notes,
   each with its callback result: rang / answered / who called). Then the form or the "add note" box. The form or
   note box is the first box for agents (fix I).
5. **Follow-ups page:**
   - (C) The default view "يحتاج عملاً" (needs action: `required`, `overdue`, `note_needed`, `callback_missed`, and
     `waiting_callback` due today) looks back 62 days and ignores من. Other views: open cases, solved, all, came back.
   - (D) Counters at the top: forms needed, notes needed after a callback, callbacks due today, missed callbacks
     (same 62-day look-back, same scope as the list).
   - (E) Callback due within 60 min → yellow row; missed → red row; sorted with the most urgent first in "needs
     action".
   - (F) Reloads by itself every 60 s while the tab is visible.
   - (J) Agents: no "employee" column. "Callback" and "did the customer answer?" are joined in one cell. A "step"
     column replaces "form".
   - (K) Agents: the supervisor-notes box is hidden when empty.
   - (L) The customer number has a copy button and a `tel:` link.
   - (H) An agent with no extension, or an extension that is not in `teams.yaml`, sees a clear message
     ("your account is not linked to a team extension, ask the admin") instead of an empty table.
6. (G) When an agent opens a call that a supervisor shared with them, the share is marked seen automatically.
7. **KPIs** (`followups.kpi`, KPI page; the PDF only when `followup_kpi_since` is set, as today), per agent and in
   total:
   - **حل من أول مكالمة %** = `solved_first` / (forms filled − `no_followup`)
   - **حُلّت بعد المتابعة** (count) and **ما زالت مفتوحة** (count: waiting + missed + note needed)
   - **متوسط جولات المعاودة حتى الحل** (solved_after only)
   - **ملاحظة بعد المعاودة في الوقت %** = notes saved by their deadline / notes due
   - **معاودات في الوقت %**: now counts the callbacks of every round, not only round 1
   - existing: forms on time %, came back (kept)

## Acceptance criteria
- [x] Unit tests for each step and every move between steps (form solved / not solved, callback on time / late /
      missed, note solved / not solved → round 3), the round ≥ 2 callback anchor, and the note deadline at its edges.
- [x] API tests: note only in `note_needed` (409 otherwise); an agent cannot add a note to another agent's call (404);
      a note in the wrong round or a double save → 409/422, never 500; the round-2 callback is not the round-1 call.
- [x] The list in "needs action" shows a 30-day-old overdue form (test); counters match the list (test).
- [x] KPI tests: the numbers in requirement 7 on a fixed set of calls, including the per-agent split.
- [x] Erasing a call's content removes its notes (retention test).
- [x] Migration 0011: up → down → up on a scratch DB.
- [x] Opening a shared call as the agent marks it seen (test).
- [x] Full pytest green (except the known `.env` test), web build in Docker OK.
- [ ] Agent page checked by hand in the browser (local), with a screenshot of each step. (NOT done: after the deploy)

## Technical approach
- `callsight/followups.py`: a `step()` function over (call, form, notes, callback calls, now). Round 1 keeps the
  existing lateral SQL query. Rounds ≥ 2 are looked up in one extra query for the calls that have notes (few rows).
- KPI rows: the same `rows()` plus notes, so the page, PDF and list use one rule (as today).
- No new tools (FastAPI, SQLAlchemy, Alembic, React: already in the tech stack).
- Risk: changing how callbacks are counted changes "callbacks on time %". It is still show-only (no PDF) until the
  admin sets the date, so nothing already reported changes.

## Scope out
- Editing or deleting a note (append-only), notifications (SMS, WhatsApp, browser push), a limit on rounds,
  the transcript rule for agents (unchanged: agents see their own call's transcript and the summary only).

## Decisions (open questions answered by the user, 2026-11-15: recommendations taken)
1. Deadline for the note after a callback: **same rule as the form (23:00 that day / 2 h after)**.
2. Can a note be edited? **No, append-only** (a clean history for the KPI and for the supervisor).
3. Is a round limit needed? **No.** The round number shows on the row, and the supervisor sees it.
4. Agents keep seeing the transcript of their own calls? **Yes** (it helps them write the note; unchanged).
