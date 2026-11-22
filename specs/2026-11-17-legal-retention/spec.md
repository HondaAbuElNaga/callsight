# Spec: Legal steps — recording announcement, keep-days, HR OK

**Status:** approved (2026-11-18, user: "approve all" — every open question = the recommendation) · **Plan step:** 8 of [finish plan](../../product/finish-plan.md)
**Roadmap items:** Phase 0 "Legal recording announcement (PDPL)"; overnight list step 10 "Legal decisions";
Phase 7 "Done when: the admin switches the follow-up KPIs on after the HR/legal OK".

## Goal
The system records, transcribes and scores calls and judges staff. Before Listen (step 9) and before the KPIs
count against agents, three decisions must be made by the company, and the system must follow them.

## Scope
**In:** (a) customer announcement at the start of every call, (b) how many days recordings/texts are kept,
(c) HR OK for using scores and follow-up KPIs on staff, (d) a short privacy note page in the system. Claude
builds what the system needs and gives ready English messages; the company decides.
**Out:** legal advice. Claude does not decide what the law requires.

## Requirements
1. **Announcement (YOU + supplier):** a short audio prompt ("this call may be recorded to improve service")
   played in IVR 6200 before the queue. Claude writes the text and the supplier message; the supplier uploads the
   prompt. Check: a test call hears it.
2. **Keep-days:** the company picks N days (≥ 30). `config/retention.yaml` + `RETENTION_ENABLED=true` on AWS;
   first night = dry run (list only), checked by the user, then real. Deletes the S3 copy too (step 3).
3. **HR OK:** written yes from HR → admin sets the follow-up KPI date (already built) and the score in KPIs (step 5).
   Until then KPIs stay show-only.
4. **Privacy page:** one page in the system (all users): what is recorded, why, who sees what, how long kept.
5. **Decisions written** in `docs/legal-decisions.md` (date, who decided, what).

## Acceptance criteria
- [ ] Test call hears the announcement
- [ ] Retention dry-run list checked by the user; first real run deletes only listed items (retention_run row)
- [ ] `docs/legal-decisions.md` filled; privacy page live
- [ ] Follow-up KPIs switched on only after the HR date

## Open questions
1. Keep-days: **recommended 365 days** for recordings and texts (one year of KPI history), KPI numbers kept
   forever (they hold no voice/text). The company decides.

## Decisions (user, 2026-11-22) — see `docs/legal-decisions.md`
1. Announcement: the line already has one (no supplier work).
2. Keep-days: **90**. Deleting happens in CallSight only, never on the PBX.
3. HR approved, from 2026-11-22: the admin sets the follow-up KPI date on the Users page.
4. Kept longer (asked by the user): the rating work (rating set, blind set, rated calls) and the 30 first-speaker
   calls, until the satisfaction model is trained (`keep_rating_set`); calls with open work until closed, at most
   180 days (`open_max_days`).
5. Old reports keep their numbers: before the first call of a closed week / month is erased, that period's KPI
   numbers are saved for every scope (`kpi_snapshot`, migration 0028). The report reads talk / follow-up / shares
   figures from it; counts, AHT and repeats stay live (call rows are never erased). Custom date ranges stay live.
6. Server tasks: `retention-check` (dry run, counts only) → user OK → `retention-on`; `retention-off` to stop.
7. Review (3 agents, 2026-11-22) → fixed: share figures saved for the week/month the share was sent; calls chosen
   again after saving numbers; branch supervisors' proof calls of kept forwarded calls kept too; admin delete saves
   numbers first; gold clips erased once keep_rating_set is off; first real run needs a dry run of the last 7 days;
   snapshots hold only content-based figures (no masked phone numbers); scope column 120 chars.
   Accepted limits: saved numbers are frozen at the first save (later solves of kept calls, teams.yaml or KPI-date
   changes do not update them); custom date ranges are live.
