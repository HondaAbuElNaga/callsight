# Spec: Legal steps — recording announcement, keep-days, HR OK

**Status:** draft (2026-11-17) · **Plan step:** 8 of [finish plan](../../product/finish-plan.md)
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
