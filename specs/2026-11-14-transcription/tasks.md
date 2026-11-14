# Tasks: Transcribe every answered call (asr worker)

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–11 in spec.md)
- [x] 2. Tech stack + roadmap: STT serving = our own asr worker (no speaches); roadmap text "stereo" → mono
- [x] 3. Move model adapters from bench.py to `callsight/asr/models.py` (bench imports them; bench tests still pass)
         → done: adapters + `read_wav`/`to_16k` in models.py; 37 asr bench/tool/report tests pass
- [ ] 4. Tests first: engine with a fake adapter (windows, de-overlapped times, silence skip, hallucination filter,
         merge, errors), queueing rules (min talk, max length, start date unset/ set, batch cap, newest first,
         ON CONFLICT no duplicates), reaper, task idempotency, latency formula, no transcript text in results
- [ ] 5. Migration 0005 (after 0004) `transcript` + `transcript_segment` (additive); pg_dump before live migrate
- [ ] 6. `callsight/asr/engine.py` + `callsight/asr/tasks.py` (Celery, queue asr, lazy torch, idle unload)
- [ ] 7. Queue hooks: after recording download (send_task) + beat `queue_transcripts` + reaper; task_routes
- [ ] 8. CLI `transcribe --call ID [--force] | --days N --limit M | status`
- [ ] 9. Docker: `docker/asr-worker/Dockerfile` (bench base + app requirements + code), compose `asr-worker`
         (profile asr, GPU, internal network, read-only audio) + `asr-worker-jais` (profile asr-jais, server-only)
- [ ] 10. Run on this PC: 5 real calls transcribed (after the benchmark run frees the GPU); `transcribe status`
- [ ] 11. ⚠ NEEDS USER: set `ASR_START_DATE` in `.env` when you want automatic transcription; check latency on LAN
- [ ] 12. Verify acceptance criteria; review with 3 subagents; fix; spec → done; roadmap; merge (--no-ff)
