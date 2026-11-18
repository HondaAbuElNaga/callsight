# Tasks: rules editor
- [x] 1. User approves. (2026-11-18 "approve all"; topics tab = yes)
- [x] 2. Branch `spec/2026-11-17-rules-editor`. Tests: `tests/test_rules_editor.py`.
- [x] 3. Migration 0023 `rule_list`, `rule_change`; first fill from YAML (also on first page open if the migration
      could not read the files).
- [x] 4. API (admin write, supervisor read, API keys never write), test-a-sentence, re-check job
      (`callsight.jobs.rules_recheck`, queue ingest, once per 5 minutes).
- [x] 5. nlp worker reads DB lists (1-minute cache, `analysis/rulestore.cached`); API, CLI, forward, asr, jobs read
      topics from the DB too (same prompt version everywhere).
- [x] 6. Page "القواعد" (tabs: word lists + test + re-check, topics, history).
- [x] 7. 3-agent review; fix. "ok push" → live 2026-11-18 (main d8c5cfb); gpu-update done 14:54 Riyadh.
- [ ] 8. User reviews the starter word lists on the page (closes call-analysis task 10 lists part).
- [ ] 9. Roadmap: Phase 6 rules editor ticked.
