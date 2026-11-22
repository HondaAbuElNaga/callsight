# Legal decisions (spec 2026-11-17-legal-retention)

The company decides; CallSight follows. Not legal advice. One row per decision: date, who decided, what.

| Date | Who | Decision | In the system |
|---|---|---|---|
| 2026-11-22 | the admin (project owner) | **Recording announcement:** the phone line already plays a "this call is recorded" message | nothing to build; check a test call hears it |
| 2026-11-22 | the admin, after HR approval | **HR OK:** follow-up KPIs may be used to evaluate agents, from 2026-11-22 | the admin sets «احتساب مؤشرات المتابعة في التقارير من تاريخ» on the Users page |
| 2026-11-22 | the admin | **Keep-days: 90 days** for recordings and texts in CallSight (the PBX keeps its own copies by its own settings) | `config/retention.yaml` `days: 90`; `RETENTION_ENABLED=true` on AWS after the first dry run is checked |
| 2026-11-22 | the admin | **Kept longer:** the rating set, blind test set, every rated call and the 30 first-speaker-check calls, until the satisfaction model is trained | `keep_rating_set: true`; set to false with the admin's OK after training |
| 2026-11-22 | the admin | **Open work kept:** a call with an open follow-up / forwarded ticket / unclosed shared call is kept until closed, **at most 180 days** | `open_max_days: 180` |
| 2026-11-22 | the admin | **Old reports keep their numbers:** before deleting, each closed week's and month's KPI numbers are saved (numbers only, kept forever) | table `kpi_snapshot`: talk, follow-up and shared-call figures (counts, AHT, repeats stay live from the call rows) |

## What is deleted after 90 days (CallSight only, never the PBX)
Recording file (and its backup copy), transcript, summary / flags / silence numbers, follow-up forms and notes,
shared-call notes and replies, forward-ticket texts, private notes, satisfaction ratings of that call
(except the kept calls above).
**Kept:** the call row (time, agent, duration, status) for the KPIs, and the saved KPI numbers.

## Limits of the saved numbers
- Saved once, the first night a call of that week / month is deleted: a kept open call solved later, a later
  team change in `teams.yaml` or a later «احتساب مؤشرات المتابعة» date do not change old reports.
- Custom date ranges (من … إلى …) are not saved: over deleted days they show smaller talk / follow-up figures.

## Copies that live a little longer
- Nightly database dumps on the server: about 8 weeks, then rotated out.
- AWS daily disk snapshots: 7 days.
