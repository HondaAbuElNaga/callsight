# Tasks: Transcribe every answered call (asr worker)

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–11 in spec.md)
- [x] 2. Tech stack + roadmap: STT serving = our own asr worker (no speaches); roadmap text "stereo" → mono
- [x] 3. Move model adapters from bench.py to `callsight/asr/models.py` (bench imports them; bench tests still pass)
         → done: adapters + `read_wav`/`to_16k` in models.py; 37 asr bench/tool/report tests pass
- [x] 4. Tests first: engine with a fake adapter (windows, de-overlapped times, silence skip, hallucination filter,
         merge, errors), queueing rules (min talk, max length, start date unset/ set, batch cap, newest first,
         ON CONFLICT no duplicates), reaper, task idempotency, latency formula, no transcript text in results
         → `tests/test_asr_engine.py` (23), `tests/test_asr_queue.py` (28, incl. CLI + migration up/down),
           `tests/test_asr_isolation.py` (3: ingest worker/API/CLI import no torch/numpy/engine), hook test in
           `tests/test_service.py`. Full suite: 224 passed.
- [x] 5. Migration 0005 (after 0004) `transcript` + `transcript_segment` (additive); pg_dump before live migrate
         → `0005_transcripts.py`, down_revision '0004' (KPI branch must merge first). Tested by running its
           upgrade()/downgrade() on the test DB (alembic chain can't run on this branch alone: 0004 missing).
           NOT run on the live DB: do `pg_dump` first, then `alembic upgrade head` (worker does it on start).
- [x] 6. `callsight/asr/engine.py` + `callsight/asr/tasks.py` (Celery, queue asr, lazy torch, idle unload)
         → also `callsight/asr/queueing.py` (DB-only rules, reaper, status). Idle unload = daemon timer thread
           in the worker child (prefork, concurrency 1; solo pool can't enforce the 15-min soft limit).
- [x] 7. Queue hooks: after recording download (send_task) + beat `queue_transcripts` + reaper; task_routes
         → hook = `after_ok` callback of `service.fetch_recordings` (used by the 5-min job only; recordings
           fetched by reconcile/CLI are picked up by the beat job). Beat every 5 min, reaper every 10 min.
- [x] 8. CLI `transcribe --call ID [--force] | --days N --limit M | status`
         → needs Redis to send, so run it inside a container (Redis is not published to the PC).
- [x] 9. Docker: `docker/asr-worker/Dockerfile` (bench base + app requirements + code), compose `asr-worker`
         (profile asr, GPU, internal network, read-only audio) + `asr-worker-jais` (profile asr-jais, server-only)
         → built without GPU as `callsight-asr-worker-test` (13.6 GB): torch 2.8.0+cu128, transformers 4.57.6,
           numpy 2.3.2 kept after app requirements; task imports; `to_16k` works; pinned Whisper processor
           loads with `--network none` from the hf-models cache (`HF_HUB_OFFLINE=1`). Main image built as
           `callsight-s2-check`: `pip show torch` fails, numpy absent, jobs+API import without torch.
           `docker compose config` OK; default services = postgres, redis, worker, api, beat (no GPU).
           Note: postgres + redis now also join network `asr-internal` → next `up -d` recreates them (data in
           volume, safe). Jais image not built (server only, untested on this PC).
- [ ] 10. Run on this PC: 5 real calls transcribed (after the benchmark run frees the GPU); `transcribe status`
         → orchestrator, once the GPU is free (after merging 0004 + this branch, pg_dump first):
           `docker compose up -d --build worker api beat` (runs alembic 0005), then
           `docker compose --profile asr up -d --build asr-worker`,
           `docker compose --profile asr exec asr-worker python -m callsight transcribe --days 2 --limit 5`,
           wait ~2 min, `docker compose --profile asr exec asr-worker python -m callsight transcribe status`,
           then `docker compose --profile asr stop asr-worker` and check `nvidia-smi` shows the memory freed.
- [ ] 11. ⚠ NEEDS USER: set `ASR_START_DATE` in `.env` when you want automatic transcription; check latency on LAN
- [ ] 12. Verify acceptance criteria; review with 3 subagents; fix; spec → done; roadmap; merge (--no-ff)
         → [x] review part done (3 reviewers, findings verified by the orchestrator, all fixed 2026-11-14):
           HIGH model-load failure / wrong model no longer terminal (row stays queued, no try used, holder
           back-off, unknown ASR_MODEL refuses worker start) + `transcribe --retry-errors --since D [--limit N]`;
           MEDIUM queue length via Redis LLEN (missing key = 0), reaper change written into spec amendments;
           `hide_parameters=True`, guarded final save, NUL stripped; ASR config typo can't break recording download;
           LOW overlap merge only between neighbouring windows; `first_queued_at` for latency (in 0005);
           models mounted read-only + `HF_MODULES_CACHE` for Jais; asr services without env_file (no PBX secret;
           Yeastar settings now only required by `app.pbx()`); tech-stack "faster-whisper" → transformers.
           Round 2: 3 more fixed: not-ready/OOM/wrong-model paths set `queued_at` to when the delayed re-send is
           due (no try used), so the reaper no longer counts unacked countdown messages as lost (one message per
           row); an empty model window also breaks the overlap merge; `redo` flag (set by --force /
           --retry-errors) keeps redone rows out of the latency figure.
           Still open: acceptance verify, spec → done, roadmap, merge.

## Assumptions (builder, 2026-11-14)
- "n-gram repeats > 4 times" = a 1–4 word unit repeated more than 4× **in a row** (normal words like "في" recur
  non-adjacently). Blocklist match = the normalised window text *contains* the phrase. "Too fast" = > 25 letters/s.
- Silence threshold RMS 0.003 (~ −50 dBFS); silent windows are stored as segments with flag `silence`, empty text.
- `speech_seconds` = display time of windows that kept text. Column `order` is named `seq` (SQL keyword).
- The after-download hook also respects `ASR_START_DATE` (unset → nothing queued), so nothing piles up in Redis
  while no asr worker runs. `ASR_MAX_SECONDS` / `ASR_MIN_TALK` compare against `talk_seconds`.
- `--force` re-queues done/error/skipped rows and ignores the length rules; without it, rules apply.
- Reaper: running rows > 30 min → queued again if tries < 2 else error; queued rows > 30 min only count as lost
  when the asr queue is empty (or unknown → skipped), and each re-send counts a try (max 3 sends, no loop).
- Latency in `status` = only transcripts queued within 1 h of hang-up (live calls), last 7 days; backfills excluded.
- A task whose row was queued for another model than the worker's `ASR_MODEL` marks the row `error`.
- Unknown model errors store only `model failed (<ExceptionType>)` (exception text could quote transcript words).
