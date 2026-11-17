# Spec: Rules editor — word lists and limits on a page

**Status:** approved (2026-11-18, user: "approve all" — every open question = the recommendation) · **Plan step:** 6 of [finish plan](../../product/finish-plan.md)
**Roadmap items:** Phase 6 "Rules editor UI" (idea AN-6, approved; source Azure-Samples/tayra).

## Goal
Bad words, escalation words, competitors and topics live in `config/rules.yaml` / `config/topics.yaml`; changing
them needs Claude and a deploy. The admin should change them on a page, see who changed what, and the next
analysed calls use the new lists.

## Scope
**In:** admin page "القواعد" for the lists in `rules.yaml` (bad words, escalation, competitor + competitor rule
words) and `topics.yaml`, a history of changes, a "test a sentence" box, re-check of recent calls.
**Out:** editing KPI formulas; editing the follow-up time limits (they stay in code/config, decided rules);
editing `forward_people.yaml` and `teams.yaml` (later idea).

## Requirements
1. Lists stored in the DB (table `rule_list` + `rule_change` history). On first start the DB is filled from the
   YAML files; after that the DB wins and the YAML is only the starting copy.
2. Page for admin only (supervisor read-only): add / remove a word, one list at a time, save with a short reason.
3. Words normalised the same way as the matcher (no tashkeel, alef forms) and shown normalised; duplicates refused;
   max 500 words per list, 40 letters per word.
4. "Test a sentence": type a sentence → shows which lists match.
5. Re-check: button "أعد الفحص لآخر 7 أيام" queues the rule check (not the LLM) on recent calls.
6. The nlp worker reads the lists from the DB (cached, refreshed within 1 minute of a change).
7. History: who, when, added/removed words, reason; never deleted.

## Acceptance criteria
- [ ] pytest: first fill from YAML, DB wins after, normalisation, duplicates, limits, admin-only, history rows
- [ ] A word added on the page is flagged on the next analysed call (test with a fake call)
- [ ] Re-check of 7 days runs and updates the flags
- [ ] Browser check by the user

## Decisions taken while building (2026-11-18)
- One row per list (`rule_list.items` JSON), names: bad_words, escalation, competitor, competitor_own,
  competitor_ignore, topics. History in `rule_change` (+ one `admin_log` row per save).
- Seeded topics keep their labels exactly, so the prompt version does not change at deploy (no re-analysis).
- Words: letters and spaces only; competitor_own / competitor_ignore one word each. Topics: max 30, label 40,
  'other' stays, a label used twice is refused.
- Stays in `rules.yaml`: silence, max_dropped_share, competitor_rule `word` + `label`.
- Re-check: worker task on queue `ingest` (DB only), last 7 days, at most once per 5 minutes.

## Open questions
1. Also edit topics (`topics.yaml`) on the page? **Recommended: yes**, same page, second tab.
