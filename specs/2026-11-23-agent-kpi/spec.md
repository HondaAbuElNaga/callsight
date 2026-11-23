# Customer-service employee KPI % (2026-11-23)

Status: done (2026-11-23, pushed to main)

User's request (2026-11-23): one KPI % for each customer-service employee, on the KPI page («المؤشرات»), made of 3
parts, each scored in bands of 10, and the final KPI = the average of the 3. The work day ends at **21:30**.

## Goal
The supervisor sees one fair number per employee: did he cover his calls, did he fill his forms on time, and is his
handling time short. Mission link: measurable service quality per agent.

## Decided with the user
1. **Part 1 – Answering (الرد):** (answered + recalled) ÷ (answered + missed), **rounded up** to the next 10.
   Answered 90% + the other 10% recalled = 100% → **100**. 85% answered + 5% recalled = 90% → **90**.
   - *Missed* = an inbound call that rang his phone and **nobody** answered (if a colleague answered, it does not count
     against him). Once per call.
   - *Recalled* = **anyone** in the customer-service team called that customer back (a real try, not an early
     hang-up < 30 s ring; same rule as the follow-up callbacks) **in the same work day**.
2. **Part 2 – Forms (النماذج):** forms filled **on time** ÷ forms due, **rounded up** to the next 10. Every form
   counts as filled (forwarded, needs solving, «لا تحتاج متابعة» question only). A late form = not filled.
3. **Part 3 – AHT:** average handling time (talk + hold) of his answered inbound calls:
   ≤ 7:00 → 100, 7:00–10:00 → 90, 10:00–13:00 → 80, … −10 for every 3 more minutes, never below 0.
4. **Final KPI** = average of the 3 parts.
5. **Work day ends at 21:30.**
   - The form for a call is due by **21:30 that day**; a call after 21:30 → due **21:30 the next day**. The old
     "23:00, or 2 h after a late call" rule is removed.
   - In the employee KPI %, a call after 21:30 counts as the **next day's** work. The other KPI tables and pages keep
     the real calendar day.

## Scope
**In:** the score engine, a new panel «نسبة KPI للموظف» on the KPI page (staff only, like the page), the new 21:30
form deadline, page/doc texts that state the deadline, tests.
**Out:** the PDF report (page only, like the shares KPI; can be added later), agents seeing
their own score, after-sales team, changing the other KPI tables.

## Requirements
1. R1 Work-day window: period days D1..Dn → from (D1−1 day) 21:30 to Dn 21:30 Riyadh.
2. R2 Part 1 per agent in the window: answered = his answered inbound calls; missed = inbound calls not answered by
   anyone whose PBX legs show his extension rang without answer; recalled = missed calls with a real-try outbound
   call by a customer-service extension to the same number (last 9 digits) after the call and before the end of that
   call's work day (21:30). A missed call with no customer number cannot be recalled and is **left out** of part 1.
   Score = ceil(10 × (answered + recalled) ÷ (answered + missed)) × 10; no calls → no score.
3. R3 Part 2: forms due = calls that need a form (same rule as the follow-up page: answered inbound, talk > 20 s,
   from the follow-up start date) in the window whose deadline passed or that are filled; on time = filled by the
   deadline. Score = ceil(10 × on time ÷ due) × 10; none due → no score.
4. R4 Part 3: AHT ≤ 420 s → 100, else max(0, 100 − 10 × ceil((AHT − 420) ÷ 180)); no answered calls → no score.
5. R5 Final = average of the parts that have a score (a part with no data is left out and shown «—»); none → «—».
6. R6 Form deadline: call ends before 21:30 Riyadh → 21:30 that day; at/after 21:30 → 21:30 next day. The same
   function is used for the note after a callback and the forward-question replies (they already share the rule).
   **Applies to calls from the go-live day** (`DEADLINE_2130_FROM`); older calls keep the old rule so past numbers
   don't change.
7. R7 Only customer-service extensions (teams.yaml `customer_service`) get a score. Shown for the page's scope
   (all / team / one agent) and period; the panel says calls after 21:30 count in the next day.
8. R8 Texts that say "23:00 / 2 h" (KPI page, follow-up docs, code docstrings) say 21:30.

## Acceptance criteria
- [ ] Unit tests: band rounding (100, 95→100, 90→90, 85→90, 0→0), AHT bands (420→100, 421→90, 600→90, 601→80,
      huge→0), average with a missing part.
- [ ] DB test: a fixture day gives hand-calculated answered / missed / recalled / forms / AHT and the scores; a missed
      call answered by a colleague is not counted; a recall the next work day does not count; a call at 21:45
      counts in the next day.
- [ ] Deadline tests: call 14:00 → 21:30 same day; 21:45 → 21:30 next day; a call before `DEADLINE_2130_FROM`
      keeps 23:00.
- [ ] `GET /api/kpi` returns `agent_score` with the parts; agents still get 403.
- [ ] Full test suite passes; `tsc` + web build pass.
- [ ] Panel shows on the KPI page with the 3 parts and the final %.

## Technical approach
- New module `callsight/agent_score.py` (pure scoring helpers + one `compute(session, period, teams, exts, now)`);
  `kpi.report()` adds `"agent_score"`. Queries reuse `_missed_rings_where` style legs, `proper_try_sql`,
  `norm_number`, `followups.rows()` (it already takes lo/hi datetimes) and `_HANDLE`.
- `followups.after_deadline()` gets the 21:30 rule with the go-live cut-over constant.
- `web/src/KpiPage.tsx` + `api.ts` types: new panel. No migration, no new tool.
- Risk: a call at 21:25 has only 5 minutes for its form (accepted by the user: "Yes, exactly").

## Open questions (my recommendation in bold)
1. Missed call without a customer number: **leave it out of part 1** (no one can call back) / count as missed.
2. Apply the 21:30 deadline to old calls too? **No — only from the go-live day**, so past % don't change.
3. Final % when one part has no data (e.g. forms not switched on): **average of the parts that have data** / count it as 0.

## Review (2026-11-23, 3 agents: bugs, security, accuracy)
- Security: no findings.
- Bugs 1 (low, fixed): `report()` computed the score for the PDF / CLI too, where nobody shows it →
  `report(..., with_agent_score=False)`; only `/api/kpi` and `save_snapshots` ask for it.
- Bugs 2 (low, fixed): after the 90-day erase the forms part of an old week/month would change → the score is saved
  in the closed-period snapshot (`_content_figures`) and read back like `followup` / `shares`.
- Accuracy 1 (low, fixed): roadmap line added.
- Accuracy 2 (low, open until push): `DEADLINE_2130_FROM` = 2026-11-24; **set it to the real go-live day before
  pushing** if the push is later, so no past form turns late after the fact.
