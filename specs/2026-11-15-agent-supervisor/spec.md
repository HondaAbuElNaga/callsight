# Spec: Agent + supervisor work — users page, call follow-up form, callback check + KPIs, shared calls, delete call content

**Roadmap item:** Phase 7 "Agent + supervisor work" (idea: user, 2026-11-15; `product/ideas.md` last section).
**Status:** approved (2026-11-15: the user asked that every question for them is decided by 3 judge agents and the
best answer taken; decisions below are the judges' answers, all three agreed unless noted)

## Goal
Agents get their own login and close the loop on every real customer call: a 20-second form after the call says
what happened and, if the problem is not solved, when the customer will be called back. The system checks the
callback itself from the outgoing calls, so the supervisor sees promises kept or missed without asking. The
supervisor can send a call with a note to an agent (coaching), and the admin manages accounts and can erase one
call's personal content on request (PDPL) from the web page instead of the command line.

## Facts (checked 2026-11-15)
- Roles `admin`, `supervisor`, `agent` exist (`callsight/api/auth.py`); an agent needs an extension that is a team
  extension in `config/teams.yaml` (104, 119 customer service; 116 after-sales). Agents see only calls with
  `call.agent_ext = their ext` (`visible_scope`); analysis is 403 for agents. Accounts are made only with the CLI.
- Inbound calls store the caller as `customer_number`; outbound calls store the dialled number there and the
  calling extension as `agent_ext` (`ingest/classify.py`). Index `ix_call_norm_number(norm_number(customer_number),
  start_time)` exists: an outbound call can be matched to an earlier inbound call by number (last 9 digits).
- AWS has calls only; no transcripts/summaries until the GPU server exists (summaries come ≤ ~35 min after a call).
- `retention.py` already erases one call's content (recording file + backup copy + `CONTENT_TABLES`) in a loop.
- The API container can write `AUDIO_DIR`; `BACKUP_DIR` is read-only there (the worker writes it).
- Pushing to `main` deploys to the live AWS server.

## Decisions (3 judges, 2026-11-15)
1. **Agents see the AI summary only** (summary, topic, action items) on their own and on shared calls; never the
   1-10 estimate, its reason or the word flags. (3/3)
2. **Callback by any team member counts**, credited to the agent who owns the follow-up; the page shows who called.
   (3/3)
3. **A form is needed for** answered inbound team calls with talk > 20 s, with one click "no follow-up needed". (3/3)
4. **Grace time 60 min** after the chosen callback time. (3/3)
5. **An unanswered callback attempt counts** when it really rang: answered, or total time ≥ 10 s. (2/3 "counts";
   the 10 s floor from judge 3 stops a 1-second dial-and-hang-up.)
6. **New KPIs are show-only until the admin switches them on** (a date in Settings; counting starts at that date).
   The page says: switch on after ~14 days and after HR/legal OK. (2/3; judge 1's "after 14 days" is the hint text)
7. **Delete = the call's content, not the call:** recording (+ backup copy), transcripts, analysis, flags, silence
   metrics, ratings, gold clip, follow-up, shares. The call row stays (counts stay right) and is marked; an admin log
   row keeps who / when / which call / reason. Confirmation needed. (3/3)
8. **Only the admin creates users** (supervisors and agents). Users are disabled, never hard-deleted. (3/3)
9. **"Who must solve it"** = a list: active supervisors and agents + "the customer" + "another department". (3/3)
10. **Supervisor note is one-way**; the agent marks it seen (time stored). (3/3)
11. **Form on time** = filled by 23:00 Riyadh on the call's day (calls ending after 21:00: 2 h after the call end).
    (3/3 "same working day"; the late-call rule is ours so a 22:50 call is not late at once)
12. Extra (from the judges' Q12): numbers are matched by the last 9 digits; a call with no customer number gets
    status "no number" and stays out of the callback KPI; a new inbound call from the same number within 72 h after
    a follow-up marked "solved" is shown as **"came back"** (show-only, never a KPI that judges); a disabled
    agent's open follow-ups stay on the supervisor's list; follow-up text is personal data (same delete rules).

## Scope
- In:
  1. **Users page (admin):** list (username, role, ext, active, locked), create supervisor/agent (agent: team ext
     required; password generated and shown once), reset password (generated, shown once, old logins end), change
     ext, disable / enable. The admin cannot disable or demote themself; the last active admin cannot be removed.
     Every change → `admin_log`.
  2. **Settings (admin, on the users page):** `followup_start` = date from which forms are needed (empty = feature
     off, nothing is "missing"); `followup_kpi_since` = date the new KPIs start to count (empty = show-only).
  3. **Follow-up form** on a needed call (owner agent, or staff on behalf): "no follow-up needed", or: what happened
     (text ≤ 500, required), solved yes/no; if not solved: who must solve it (list), expected solve time and
     callback time (both required, callback time in the future and ≤ 14 days ahead). The owner may change it until
     the form deadline (decision 11); later only staff. Stored: who filled, first filled time, last change.
  4. **Callback check** (computed when read, nothing stored): for a not-solved form, the first outbound team call
     to the same number after the original call ended. Status: `waiting` (not called yet, deadline not passed),
     `on_time` (called ≤ callback time + 60 min), `late` (called later), `missed` (deadline passed, no call),
     `no_number`. Shows the extension that called.
  5. **Follow-ups page** ("المتابعات"): agent: my calls still missing a form, my open callbacks (time + status),
     calls shared with me (note, mark seen). Staff: the same for all agents, filter by agent / status / day, plus all
     shares with seen time.
  6. **KPIs:** per agent and team: forms needed, forms on time, forms on-time %, callbacks due, callbacks on time,
     callbacks on-time %, "came back after solved". KPI page shows them (labelled "للعرض فقط" until
     `followup_kpi_since` is set); PDF reports include them only after it is set and count only from that date.
  7. **Share a call (staff):** from the call panel, pick an active agent of the call's team + a note (≤ 1000,
     required). The agent may then open that call (detail, recording, transcript, summary), with the same audit rows
     as own calls. Staff can withdraw a share. Shared calls do not appear in the agent's call list (only in
     "shared with me").
  8. **Agent summary:** `GET /api/calls/{id}/summary` (own or shared calls; summary/topic/action items only; audited
     as `analysis`).
  9. **Delete call content (admin):** button in the call panel, reason (≥ 5 characters) + type the call number to
     confirm. Deletes as decision 7, API side at once; the backup copy is deleted by a worker task (queue `maint`).
     Deleted calls are left out of the follow-up KPIs.
- Out: replies/threads on notes; reassigning a follow-up to another agent; automatic reminders (no push/SMS);
  supervisors creating users; deleting whole call rows or users; changing the score/estimate rules; agents seeing
  colleagues' forms or KPIs.

## Requirements
- R1 Every new `/api` route is listed in `tests/test_agent_role.py` (deny-by-default route walk) with its role.
- R2 Agents: own follow-ups and own shares only; another agent's call/form/share id → 404; staff-only routes 403.
- R3 Form validation on the server (not only the page): field lengths, responsible user must be an active
  supervisor/agent, callback time rules, the owner's deadline lock, form only on needed calls, not on deleted calls.
- R4 The callback check and KPIs follow decisions 2, 4, 5, 11, 12 exactly, tested with hand-made calls at the edges
  (59 / 61 min, 9 / 10 s, 22:59 / 23:01, different number formats +966 / 05).
- R5 User admin: password ≥ 10 chars generated by the server, returned once, never logged; `token_version` +1 on
  reset / disable / role or ext change; self-disable and last-admin removal refused.
- R6 Delete: files only inside `AUDIO_DIR` (path from the DB row), all content rows of that call removed, the call
  row kept with `content_deleted_at`, `recording_status = 'deleted'`; `admin_log` row; backup-copy task sent.
- R7 No new tool or library (tech stack unchanged). Migration 0010 is additive only.
- R8 Arabic UI, right-to-left, same look as the other pages; filters apply at once (no "show" button).

## Acceptance criteria
- [ ] Full pytest passes (TEST_DATABASE_URL), incl. new tests for R1–R6
- [ ] Route walk covers every new route
- [ ] Callback/KPI edge tests (R4) pass
- [ ] `alembic upgrade head` then `downgrade 0008` then `upgrade head` works on the test DB
- [ ] Web page builds in Docker (`tsc` + `vite build`) without errors
- [ ] 3 review agents (bugs, security, accuracy): no open critical/high finding

## Technical approach
- **DB (migration 0010):** `call.content_deleted_at`; `call_followup` (call_id unique FK cascade, owner_ext,
  no_followup, what_happened, solved, responsible_kind user/customer/other_department, responsible_user_id,
  expected_at, callback_at, filled_by, first_filled_at, updated_by, updated_at); `call_share` (call_id FK cascade,
  from_user_id, to_user_id, note, created_at, seen_at); `app_setting` (key, value, updated_at, updated_by);
  `admin_log` (user_id, action, target, reason, at; no FK on the target so it outlives a call).
- **Code:** `callsight/followups.py` (needed-calls rule, deadline, callback check, list queries, KPI numbers — one
  place used by the page, the KPI page and the PDF); `callsight/api/admin.py` (users, settings, delete);
  `callsight/api/work.py` (follow-ups, shares, agent summary); `visible_call` gets "or shared with this agent";
  retention's per-call erase moved to one shared function used by retention and by delete.
- **Web:** `UsersPage.tsx` (users + settings), `FollowupsPage.tsx`, `FollowupPanel.tsx` + share box + delete box in
  the call panel, agent summary box, KPI columns.
- **Risks:** wrong callback matching (tested at the edges); agents gaming the form (deadline lock, callback ≤ 14
  days); privacy (shares audited, summary only, deleted content really gone except old DB dumps until rotation
  — the same note as retention).

## Open questions
None left for the user: all decided by the judges (above). Before the KPIs judge staff, the user still needs the
HR/legal OK (the switch stays off until the admin sets the date).
