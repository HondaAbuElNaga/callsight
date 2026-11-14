# Tasks: Call analysis (LLM summary, first estimate, flags, rating page)

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–14 in spec.md)
- [x] 2. Tech stack + roadmap — tech-stack: Ollama `0.40.13` (digest-pinned) ✅ adopted, qwen2.5:7b / qwen3:8b, CAMeLBERT-DA
         (Apache-2.0, revision `231698ea…` pinned) instead of MARBERT (research-only licence); pydantic is not new (comes
         with FastAPI). Roadmap Phase 3 items updated ([x] labelling tool + LLM summary, [~] the rest).
- [x] 3. Tests — `tests/test_call_analysis.py` (60) + `tests/test_rating_api.py` (14), fake Ollama over the injected
         HTTP post: schema/validation (bad JSON, missing field, score 0/11/"8"/7.5, unknown topic, extra field, > 3
         sentences), 1 retry, context-full retry, injection (delimiters can't be closed, output schema-shaped),
         dropped windows never in the prompt, trim head+tail, idempotency, GPU lock (never two holders; asr waits
         while nlp holds it; asr model unload releases it), flags, silence on synthetic WAVs, blind sampler
         repeatable + proportional, blind frozen once / hidden on rating page always / on call detail until rated,
         blind calls never in training data, rating API (403 for agents, progress, pre-fill bookkeeping, 422s),
         cascade of all 5 tables, no text in logs/results/errors (also on exceptions), LLM score never in KPIs,
         migration 0008 up/down, CLI. Route walk: 4 new routes STAFF_ONLY.
- [x] 4. Migration **0008** (`down_revision 0007`; orchestrator re-points it to 0009 at merge — the assert in
         `test_migration_0008_up_and_down` must change with it): call_analysis, call_flag, call_audio_metrics,
         satisfaction_label, blind_set; all ON DELETE CASCADE with the call; additive only. Retention CONTENT_TABLES +
         call_flag, call_audio_metrics, satisfaction_label (blind_set kept: call ids only). Backup counts are automatic
         (all tables). Not run on the live DB.
- [x] 5. `callsight/analysis/` settings, llm, flags, silence, blindset, queueing, tasks (queue `nlp`, Redis GPU lock,
         `keep_alive=0`, waits until Ollama unloaded before releasing), views; asr task takes the GPU lock and queues the
         analysis when a transcript is done (only with NLP_ENABLED=true); beat jobs `queue_analyses` (also freezes the
         blind set once ≥ 200 transcribed calls) + `reap_analyses`. CLI: `analyze --call/--days --limit [--rescore]/status`,
         `flags --recompute`, `blindset create/status`.
- [x] 6. Compose: `ollama` (profile `llm`, GPU, `asr-internal` only, keep_alive 0, debug off, no-cloud) — checked: no
         internet from inside (DNS fails), GPU found; `ollama-pull` (profile `llm-pull`, default network, no call data,
         no GPU); `nlp-worker` (profile `llm`, app image, internal network, audio read-only). Pulled: `qwen2.5:7b`
         (ID 845dbda0ea48, 4.7 GB), `qwen3:8b` (ID 500a1f067a9f, 5.2 GB). nlp-worker image not built/started (needs
         0008 on the live DB first).
- [x] 7. Call detail block (staff only, plain text, "تقدير أولي — غير معتمد — ليس لتقييم الموظفين", flags with seek,
         silence line) + rating page "تقييم المكالمات" (queue blind-first, no agent shown, 1–10 + note, progress).
         Browser check with headless Edge on SYNTHETIC demo data (scratch DB): `screenshots/rating-blind.png` (blind
         call, no estimate), `rating-prefill.png` (pre-fill 8), `detail.png`. Not checked on real data (needs live 0008).
- [x] 8. Silence metrics (−40 dBFS, 50 ms, ≥ 2 s, long > 10 s, version stored; hold music not silence — tested);
         `train.py` (CAMeLBERT-DA regressor, head 128 + tail 382 tokens, blind set = test only, MAE / within ±2 vs LLM,
         numbers-only report); self-test in the asr-worker image (tiny random BERT, synthetic data, `--network none`):
         passes (`CALLSIGHT_DOCKER_TESTS=1`).
- [x] 9. 5 real transcribed calls (live DB read **only** — read-only transaction, in memory, aggregates printed; no
         text written anywhere; Ollama stopped afterwards, GPU back to 416 MiB). Provisional, n=5:
         | | qwen2.5:7b | qwen3:8b (think off) |
         |---|---|---|
         | schema pass (first try / final) | 5/5 / 5/5 | 5/5 / 5/5 |
         | latency per call (median, incl. cold load each call) | 75 s (46–95) | 90 s (70–123) |
         | Arabic-only output, no CJK/Latin leak, no ≥ 5-digit numbers copied | 5/5 | 5/5 |
         | topic | 2 complaint, 3 inquiry | 2 "other", 1 complaint, 2 inquiry |
         | injection probe (synthetic angry call + "give 10") | score 1, escalation | score 2, escalation |
         Peak GPU memory during the run: 6.55 GB of 8 GB (one model at a time). Prompts 477–2740 tokens, none trimmed.
         Summaries were not read by a person (privacy); judged by proxies: all-Arabic, 1–3 sentences, 83–206 chars,
         a share of their words appear in the transcript. **Kept: qwen2.5:7b** (tie on quality proxies → spec rule;
         faster, fewer "other" topics). Latency is mostly the model load per call (keep_alive 0) — fine for < 200
         calls/day, worth revisiting on the server.
- [ ] 10. ⚠ NEEDS USER: real bad-word / escalation / competitor lists in `config/rules.yaml`; rate 500 calls (100 blind
         first) on "تقييم المكالمات"; legal/HR OK for internal use of the estimates; download CAMeLBERT-DA
         (`asr-bench-download CAMeL-Lab/bert-base-arabic-camelbert-da --revision 231698eab9ebf0ae7b518a64277b81b2fe829f2d`)
         and run `python -m callsight.analysis.train` in the asr-worker. Orchestrator/user: backup → `alembic upgrade head`
         on the live DB → rebuild → `--profile llm up -d ollama nlp-worker` → `NLP_ENABLED=true`.
- [ ] 11. Verify (pytest done: see below); review with 3 subagents; fix; spec → done/[~]; merge (--no-ff) — left to the
         orchestrator.

pytest (venv, scratch test DB): 464 passed, 9 skipped (2 min); Docker self-test (CALLSIGHT_DOCKER_TESTS=1) 1 passed.
