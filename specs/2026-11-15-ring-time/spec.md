# Spec: Outbound ring time and who hung up ("رن كام ثانية ومين قفل")

**Roadmap item:** Phase 7 follow-on (idea: user, 2026-11-15).
**Status:** draft

## Goal
Supervisors need to see whether an agent really tried to reach a customer, or rang for 1-10 seconds and hung up.
The PBX already sends, for every outgoing call, the ring seconds (`ring_duration`) and who ended it (`reason`:
`src hangup` = our agent, `dst hangup` = customer side, `dst extension_busy_no_forward` = busy). We store both on
`call_leg` but no page shows them, and the callback check counts a 10-second try as a real callback.

## Facts (PC database, outgoing calls up to 2026-11-14, 387 calls, one leg each)
- Not answered, agent hung up: 44 calls, **37 under 15 s** of ringing (shortest 1 s). All from after-sales (ext 116).
- Not answered, customer side ended: 109 calls; **91 ended at 61-63 s** = the line closes by itself after
  **about 62 s** (phone network limit, not the agent).
- Busy: 61 calls (1-11 s).
- Some legs have no `ring_duration` (agent hung up while dialling) → treated as 0 s.

## Scope In
1. Who hung up + ring seconds on every outgoing call.
2. Callback rule: an agent hang-up before **20 s** of ringing is not a callback.
3. Show it on the follow-ups page, in the call panel and in the after-sales KPI (page + PDF).

## Scope Out
Inbound calls (rings to agents are already in "missed rings"); changing the PBX ring time; alerts/notifications;
a separate list page of short calls.

## Requirements
1. New column `call.hangup_by` (migration 0014): for outbound calls `agent` / `customer` / `busy`, from the
   external leg's `reason` (`src hangup` → agent, `dst extension_busy...` or status busy → busy, other `dst ...` →
   customer); NULL for inbound/internal or unknown reason. Filled by ingest (`classify`) and back-filled for old
   calls by the migration. Outbound `wait_seconds` stays = ring seconds (already true).
2. Constant `EARLY_HANGUP_SECONDS = 20`. An outbound call is an **early hang-up** when it was not answered,
   `hangup_by = agent` and ring seconds < 20.
3. Callback check (`followups._callback_lateral`): a callback counts when the call was answered, OR it was not an
   early hang-up (customer declined / busy / line closed by itself / agent waited ≥ 20 s). This replaces the old
   rule "answered or total ≥ 10 s". So early hang-ups never close a callback; busy and customer-declined tries do.
4. Follow-ups API + page: per callback round, besides the counted callback, the number of early hang-ups to that
   customer in that round and the latest one's ring seconds. Text on the page (column "رد العميل؟" / round history):
   - counted, not answered: «لم يرد · رن 62 ث · انقطع الخط» / «· العميل رفض» / «· مشغول» / «· الموظف قفل بعد 25 ث»
   - early tries: «محاولة لم تُحسب: رن 6 ث والموظف قفل» (+ «×3» when more than one).
5. Call panel (`/api/calls/{id}` + panel): outbound calls show «رن X ث · قفل: الموظف / العميل / مشغول», and a red
   mark «قفل مبكر» on an early hang-up.
6. KPI (page + PDF), outbound numbers, team and per agent: «قفل مبكر (أقل من 20 ث)» = count of early hang-ups and
   «متوسط ثواني الرنين» = average ring seconds of outbound calls that were not answered. Shown on the after-sales
   view and in the outbound part of other teams. Lower early hang-ups = better.
7. "Customer side ended at ≥ 55 s" is shown as «انقطع الخط» (line closed by itself), under 55 s as «العميل رفض».

## Acceptance criteria
- [ ] classify tests: src hangup / dst hangup / busy / no ring_duration → right `hangup_by`; inbound → NULL.
- [ ] Migration 0014 up/down/up; after up, the PC DB has `hangup_by=agent` on exactly the 44 calls above.
- [ ] Follow-up tests: an agent try of 6 s does not close the callback (step stays waiting / missed); a 25 s
      agent try closes it; a 2 s busy or customer-declined try closes it; answered closes it; early-try count shown.
- [ ] KPI tests: early hang-up count and average ring seconds per agent and team; PDF has the two columns.
- [ ] API test: call detail has ring seconds + `hangup_by` + early flag; agents see it only on calls they may see.
- [ ] Full pytest green (except the known `.env` test), web build OK.

## Technical approach
- Only Python/SQLAlchemy/React already in use; no new tool.
- `hangup_by` as a stored column (not computed from legs each time) so the follow-up lateral query and KPI stay simple
  and indexed queries do not join `call_leg`.
- Risk: changing the callback rule changes past follow-up results (some "on time" rounds may become "missed"). The KPI
  is show-only until the admin's start date, so this is acceptable; noted in the release message.

## Open questions (with recommendation)
1. Busy or customer declined after 1-2 s: does that count as a real callback try? **Recommended: yes** (the agent did
   try; the customer side ended it).
2. Agent waited ≥ 20 s and then hung up (before the 62 s limit): count it? **Recommended: yes** (20 s = ~4 rings).
3. Should early hang-ups also lower the KPI on their own (not only through missed callbacks)? **Recommended: show the
   count only**, no extra penalty, so a supervisor judges it.
