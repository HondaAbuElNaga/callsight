# Finish plan: every step until CallSight is finished (written 2026-11-17)

One list, in build order. Each line is one spec folder under `specs/`. Build one at a time: approve → build →
3-agent review → "ok push" → live → next. Steps marked **YOU** need the user; Claude cannot do them alone.

| # | Spec | What it finishes | Needs from you first | Status |
|---|---|---|---|---|
| 0 | [2026-11-16-priority-no-followup](../specs/2026-11-16-priority-no-followup/spec.md) | Priority, "تحتاج حل", forward chain, training lookup | one real test forward by an agent (D5) | **live 2026-11-18**, D5 left |
| 1 | [2026-11-17-cleanup](../specs/2026-11-17-cleanup/spec.md) | Small bugs, open branches merged, docs/roadmap up to date, queue-waiting check | one test call into queue 6401 | built + reviewed, waits for "ok push" |
| 2 | [2026-11-17-rollout](../specs/2026-11-17-rollout/spec.md) | Agents really use the system; the PC is switched off | agent names, "yes stop the PC" | approved |
| 3 | [2026-11-17-offsite-backup](../specs/2026-11-17-offsite-backup/spec.md) | Encrypted copy outside the server + restore drill on AWS (Phase 6 "Done when", part 1) | one CloudFormation update (click by click) | approved; **moved to the end** (user 2026-11-19), build before step 10 |
| 4 | [2026-11-17-diarization](../specs/2026-11-17-diarization/spec.md) | Agent / customer separated in every transcript; talk ratio, interruptions | a free Hugging Face token (click by click) | approved |
| 5 | [2026-11-17-satisfaction-model](../specs/2026-11-17-satisfaction-model/spec.md) | Trusted 1–10 score (Phase 3 "Done when") | the 500 ratings | approved |
| 6 | [2026-11-17-rules-editor](../specs/2026-11-17-rules-editor/spec.md) | Admin edits word lists and limits on a page, no code | nothing | approved |
| 7 | [2026-11-17-redaction](../specs/2026-11-17-redaction/spec.md) | Spoken ID / card / phone numbers hidden in texts (PDPL) | nothing | approved |
| 8 | [2026-11-17-legal-retention](../specs/2026-11-17-legal-retention/spec.md) | Recording announcement, keep-days on, HR OK → follow-up KPIs on | legal / HR answers | approved |
| 9 | [2026-11-17-listen](../specs/2026-11-17-listen/spec.md) | "Listen" button rings the supervisor's phone (Phase 5 "Done when") | step 8 done; one PBX setting | approved |
| 10 | [2026-11-17-acceptance](../specs/2026-11-17-acceptance/spec.md) | Every phase "Done when" checked, guides written, AWS cost check, project closed | one week of your own call count | approved |

**Why this order:** 0–1 clear what is already built. 2 gets real users on the system (their data feeds everything
after). 3 protects that data. 4 must come before 5 (the model should read the customer's words only). 6–7 are
independent and short. 8 unblocks 9 and the follow-up KPIs. 10 checks everything and closes the project.

**Can run in parallel while waiting for you:** 1, 3, 6, 7 need nothing from you. Your rating of the 500 calls
(needed for 5) can start today and run while 1–4 are built.

**Not in the plan (ideas, not approved):** full-text search, agent script checklist, live transcript, drop-folder
import, a fine-tuned Whisper (see the acceptance spec, open question 2).
