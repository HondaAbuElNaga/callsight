# Tasks: Call analysis (LLM summary, first estimate, flags, rating page)

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–14 in spec.md)
- [ ] 2. Tech stack + roadmap (Ollama ✅ when adopted, CAMeLBERT-DA instead of MARBERT, pydantic if new)
- [ ] 3. Tests first (fake Ollama HTTP): schema/validation/retry, injection, num_ctx/trim/skip, idempotency, GPU lock,
         flags, blind set frozen + hidden everywhere + excluded from training, rating API + progress, roles, cascade,
         no text in logs/results, LLM score never in KPIs
- [ ] 4. Migration 0007 (after 0006): call_analysis, call_flag, satisfaction_label, blind_set (+ silence metrics)
- [ ] 5. `callsight/analysis/` llm.py, flags.py, tasks (queue nlp, GPU lock), CLI `analyze --call/--days/--rescore`
- [ ] 6. Ollama compose service (profile llm, internal network) + pull container; pull qwen2.5:7b (+ qwen3:8b)
- [ ] 7. Call detail block + rating page (blind-aware)
- [ ] 8. [may be ~] silence metrics; train.py + tiny Docker test
- [ ] 9. Run on 5 real transcribed calls; qwen2.5 vs qwen3 note
- [ ] 10. ⚠ NEEDS USER: word lists; rate 500 calls (100 blind first); legal/HR OK; then run training
- [ ] 11. Verify; review with 3 subagents; fix; spec → done/[~]; roadmap; merge (--no-ff)
