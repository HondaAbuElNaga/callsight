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
- [x] 4. Migration **0008** (`down_revision 0009`: the live-board spec's 0009 was merged first, so the chain is
         0007 → 0009 → 0008; `test_migration_0008_up_and_down` asserts it): call_analysis, call_flag, call_audio_metrics,
         satisfaction_label, blind_set; all ON DELETE CASCADE with the call; additive only. Retention CONTENT_TABLES +
         call_flag, call_audio_metrics, satisfaction_label (blind_set kept: call ids only). Backup counts are automatic
         (all tables). Not run on the live DB.
- [x] 5. `callsight/analysis/` settings, llm, flags, silence, blindset, queueing, tasks (queue `nlp`, Redis GPU lock,
         `keep_alive=0`, waits until Ollama unloaded before releasing), views; asr task takes the GPU lock and queues the
         analysis when a transcript is done (only with NLP_ENABLED=true); beat jobs `queue_analyses` (also freezes the
         blind set once ≥ 200 transcribed calls) + `reap_analyses`. CLI: `analyze --call/--days --limit [--rescore]/status`,
         `flags --recompute`, `blindset create/status`.
- [x] 6. Compose: `ollama` (profile `llm`, GPU, `asr-internal` only, keep_alive 0, debug off, no-cloud) — checked: no
         internet from inside (DNS fails), GPU found; `ollama-pull` (profile `llm-pull`, own network `llm-pull` since
         the review: internet but no postgres/redis; no call data, no GPU); `nlp-worker` (profile `llm`, app image, internal network, audio read-only). Pulled: `qwen2.5:7b`
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
         text written anywhere; Ollama stopped afterwards, GPU back to 416 MiB). Provisional, n=5.
         **Unverified in repo:** the one-off comparison script and its output were not kept, so the numbers below are
         the builder's record of that run and can't be reproduced from the repository (review finding). Model IDs of
         that run, as printed by `ollama list`: `qwen2.5:7b` = `845dbda0ea48`, `qwen3:8b` = `500a1f067a9f` (compare
         with `python -m callsight analyze status` → "model digest"). Disk was checked before the pulls: 149 GB free
         on C:, 415 GB on D: (models 4.7 + 5.2 GB, Ollama image ~3.4 GB).
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
- [ ] 10. ⚠ NEEDS USER: real bad-word / escalation / competitor lists in `config/rules.yaml`; rate 500 calls in
         total = the 100 blind test calls first (their rating is final, no LLM summary/estimate shown) + 400 more
         (training) on "تقييم المكالمات"; legal/HR OK for internal use of the estimates; download CAMeLBERT-DA
         (`asr-bench-download CAMeL-Lab/bert-base-arabic-camelbert-da --revision 231698eab9ebf0ae7b518a64277b81b2fe829f2d`)
         and run the training in the asr-worker with a writable host folder:
         `docker compose --profile asr run --rm -v ./data/train:/out asr-worker python -m callsight.analysis.train --out /out`.
         ⚠ If retention is ever switched on: ratings are deleted together with the call content, so finish the
         rating AND the training inside the retention window (else the test set drops below 100). Orchestrator/user: backup → `alembic upgrade head`
         on the live DB → rebuild → `--profile llm up -d ollama nlp-worker` → `NLP_ENABLED=true`.
- [ ] 11. Verify (pytest done: see below); review with 3 subagents; fix; spec → done/[~]; merge (--no-ff) — left to the
         orchestrator.
         → 3 reviewers (bugs, security, accuracy): 0 critical, 0 high, 8 medium, ~19 low; all confirmed. Fixed:
           **medium** — schema errors never echo a model-written key (`extra_field`); a blind label is final (409 on a
           second PUT); the retry is a different request (new seed + field-name note); GPU priority for transcription
           (`gpu:wanted` flag, nlp requeues itself); "500 labels" = 400 training + 100 blind (`MIN_TRAIN = 400`,
           page wording); roadmap "Labelling tool" + "LLM call summary" back to [~] (not deployed yet); real-run
           numbers marked "unverified in repo" + model IDs and disk check recorded (task 9).
           **low** — blind calls: no analysis block on the rating page / in call detail until rated; train.py refuses
           to run without `HF_HUB_OFFLINE=1` and outside the base-model allow-list (MARBERT refused), `SAT_BASE_*` in
           .env.example; analysis reads audited (kind `analysis`); 7+ digit runs in LLM text rejected; `analyze status`
           prints the model digest, `ollama-pull` on its own network; retention.yaml + task 10 say ratings go with
           the call; training output to a mounted host folder; LLM baseline = one model + one prompt version (in the
           report); silence label shows the real threshold (`long_s`); asr lock re-taken inside the model holder;
           `ASR_IDLE_UNLOAD_S` ≥ 1; nlp maintenance wait moves `queued_at`; whitespace-only text invalid;
           `blindset create --n` ≥ 1; pre-fill = what the page displayed, first pre-fill kept; stale texts (0008
           docstring, task 4, pytest line); tests for the real `_gpu_lock` and the `analyze_call` wrapper.
           **Not fixed in code (documented):** asr transcribes without the GPU lock when Redis itself fails
           (deliberate, spec review decision 19); no migration was needed for the audit kind (`String(10)`, no
           check constraint).

pytest (venv, scratch test DB): 464 passed, 9 skipped when built; 540 passed, 9 skipped after merging main (live
board); **566 passed, 9 skipped** after the review fixes (26 new tests: `tests/test_call_analysis_review.py` + additions
in `tests/test_rating_api.py`). Web build (`npm ci && npm run build`, node:22-alpine) OK. Docker self-test
(CALLSIGHT_DOCKER_TESTS=1): 1 passed when built; not re-run after the review fixes (the self-test path of train.py
is unchanged; the new guards only apply to the real run).
