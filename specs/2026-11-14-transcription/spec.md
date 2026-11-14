# Spec: Transcribe every answered call (asr worker)

**Roadmap items:** Phase 2, "Local OpenAI-compatible STT server, model chosen by config" (changed: no separate server,
see decision R2 in `docs/overnight-plan.md`) and "`asr` worker queue: … transcribe → store segments with speaker +
timestamps"
**Status:** approved (by 2 judge agents on the user's behalf, 2026-11-14 — user may revise)

## Goal
Readable text for every answered team call, so supervisors can read/search calls and Phase 3 can score them.
Phase 2 **Done when:** chosen model WER ≤ 25% on the gold set (benchmark spec) **and every new answered call has a
transcript within 15 min of hang-up**.

## Facts (checked 2026-11-14)
- Recordings: mono WAV 8 kHz 16-bit in `AUDIO_DIR/YYYY/MM/DD/`, `call.recording_status = ok`, `recording_path`.
- Adapters for 3 models already exist and run on this PC (`callsight/asr/bench.py`): Whisper large-v3 (60 s clip in
  7.4 s, 3.2 GB), Audar Turbo (bf16 ~5 GB), Jais 4-bit (RTF 3.8, 11 GB peak → server only).
- Two incompatible Python environments: Whisper + Audar (transformers 4.57.6 / qwen-asr) and Jais (transformers 5).
- Docker has ~7.5 GB RAM on this PC; GPU 8 GB; the PC is off the office LAN tonight (no new calls arrive).
- ~200 calls/day max; today the DB holds the calls ingested since 2026-11-12.

## Scope
- In:
  - Tables (migration **0005**, additive): `transcript` (one per call: model, model revision, status
    queued/running/done/error/skipped, full text, audio seconds, compute seconds, queued/started/finished times,
    error, `diarization_version` NULL) and `transcript_segment` (transcript, order, start s, end s,
    `speaker` NULL, text).
  - `callsight/asr/engine.py`: load the model named by `ASR_MODEL` once per worker process, transcribe a whole call:
    read WAV → 16 kHz → 30 s windows with 1 s overlap → one segment per window (start/end from the window) → full
    text = merged windows. Reuses `bench.py` adapters (moved to `callsight/asr/models.py`, bench imports them).
  - Celery queue **`asr`** + task `transcribe_call(call_id)` (idempotent: skips when a `done` transcript with the same
    model exists; marks `error` with message on failure, never retries in a loop: max 2 tries).
  - Queueing: (a) right after a recording is downloaded OK for an answered call with talk ≥ `ASR_MIN_TALK` (5 s);
    (b) beat job every 5 min `queue_transcripts` picks answered + recording ok + no transcript, **only calls that
    started after `ASR_START_DATE`** (default: the day the worker is first enabled) and at most `ASR_BATCH` (50) per
    run → no surprise overnight backfill of every old call.
  - CLI: `python -m callsight transcribe --call ID` / `--days N --limit M` (backfill, capped) / `status` (queue
    length, done/error counts, median + max latency hang-up → transcript).
  - Docker: `asr-worker` service (profile `asr`, GPU, concurrency 1, `HF_HUB_OFFLINE=1`) built from the bench
    Dockerfile + app requirements; `asr-worker-jais` (profile `asr-jais`) for the Jais image. Only one runs.
  - Latency logged per transcript (finished_at − call.end_time) for the "within 15 min" target.
- Out:
  - Speaker separation: segments have `speaker = NULL`; pyannote diarization is a later item ⚠ NEEDS USER
    (accept the gated model licence on Hugging Face + give an HF token).
  - Transcript page / export / search (next spec S3).
  - Word-level timestamps, voice-activity cutting (later idea).
  - Choosing the production model: `ASR_MODEL=whisper-large-v3` (MIT) until the benchmark names a winner;
    Audar is never made default without the user (licence).

## Requirements
1. A call with a 3 min recording gets one `transcript` row (done) and ⌈(180−1)/29⌉ = 7 segments with increasing,
   non-overlapping display times and non-empty full text (on this PC with Whisper).
2. Re-running the task for the same call and model changes nothing (no duplicate rows).
3. Missing/broken audio file → `error` status with a readable message; worker keeps running.
4. Beat job never queues more than `ASR_BATCH` calls per run and never calls older than `ASR_START_DATE`.
5. Worker never downloads models at run time when `HF_HUB_OFFLINE=1`; no audio or text leaves the PC.
6. The normal `worker` (ingest queue) and `api` images do **not** get torch (stay small); only asr images do.
7. `transcribe status` prints queue length, counts and latency.

## Acceptance criteria
- [ ] `pytest` passes, incl. engine tests with a fake model adapter (segments/times/merge, errors), task idempotency,
      queueing rules (min talk, start date, batch cap, no duplicates), migration up/down on the test DB
- [ ] On this PC: `docker compose --profile asr up -d asr-worker` + `transcribe --days 2 --limit 5` → 5 calls done,
      segments stored; GPU memory released when the worker stops
- [ ] Latency shown by `transcribe status` (real new-call latency ⚠ NEEDS USER: needs the office LAN for new calls)
- [ ] `docker compose up -d` (default profile) starts no GPU container

## Technical approach
- Risks: RAM (Docker 7.5 GB: asr worker ~3–4 GB with Whisper; run with ingest worker + api + db, not with Ollama);
  WDDM GPU memory on Windows; long calls (30 min) → 62 windows, ~4 min with Whisper — fine for 200 calls/day.
- Model choice per env var; the compose profile must match the model family (documented in `.env.example`).

## Open questions (decided by 2 judges on the user's behalf)
1. **Segment size:** one segment per 30 s window (recommended now; simple, works for every model) vs voice-activity
   segments (needs a VAD model = new tool).
2. **Which calls:** answered team calls with talk ≥ 5 s, inbound + outbound (recommended) vs inbound only.
3. **Start date:** only calls from the day the worker is enabled + an explicit, capped backfill command (recommended)
   vs automatic backfill of all stored calls.
4. **Default model until the benchmark is scored:** Whisper large-v3 (recommended: MIT, fast on 8 GB) vs Audar
   (better public WER, licence limits).

## Decisions (2 judges, 2026-11-14; override the text above where different)
1. **Segments:** one segment per window from `chunk_bounds` (30 s max, 1 s overlap, now spread evenly — no tiny tail
   window). Display times de-overlapped: a boundary sits in the middle of the overlap. Before transcribing, a window
   with RMS energy below a threshold is skipped (no text, counted as dropped). After transcribing, a
   **hallucination filter** drops a window's text when it matches a blocklist of known Whisper phrases
   ("اشتركوا في القناة", "ترجمة نانسي قنقر", "شكرا للمشاهدة", …), when one n-gram repeats > 4 times, or when
   characters per second are implausible; dropped windows keep a `flag` and empty text. `segmenter_version` stored.
   Silero VAD = later item.
2. **Calls:** answered team calls, inbound + outbound, talk ≥ `ASR_MIN_TALK` (5 s); shorter → `skipped` row
   (so beat never re-picks them). Calls longer than `ASR_MAX_SECONDS` (3600) → `skipped` with reason.
3. **Start date:** `ASR_START_DATE` must be set explicitly in `.env`; unset → beat queues nothing and logs a
   warning. Older calls only through the capped CLI backfill. Beat queues **newest first**.
4. **Model:** Whisper large-v3 (MIT), pinned revision stored on each row. Audar never default (licence).
   `asr-worker-jais` = config only, marked server-only, untested on this PC.
5. **Schema:** one transcript per **(call, model)** — unique constraint; FK `ON DELETE CASCADE`; columns also
   `tries`, `dropped_windows`, `speech_seconds`, `segmenter_version`, `diarization_version` (NULL).
6. **Queueing safety:** `INSERT … ON CONFLICT DO NOTHING` creates the `queued` row; only the inserter enqueues
   (after the recording commit). `task_routes` sends `transcribe_call` to queue `asr`; the task lives in
   `callsight/asr/tasks.py`, engine/torch imported lazily inside it; ingest code enqueues with
   `send_task("callsight.asr.tasks.transcribe_call")`, so the ingest worker and API never import torch
   (test: worker image has no torch). `queue_transcripts` (beat, DB only) stays on `ingest`.
7. **Failures:** reaper (beat) sets `queued`/`running` rows older than 30 min back to `queued` while tries < 2,
   else `error`. CUDA out-of-memory → retry once; missing/bad file → `error` at once. `error` and `skipped` are
   terminal for beat; retry only with `transcribe --call ID --force`. Task `soft_time_limit` 15 min.
8. **GPU sharing (Ollama later):** `ASR_IDLE_UNLOAD_S` (default 300) frees the GPU after idle time.
9. **Privacy (PDPL):** task results and logs hold only ids/counts — never transcript text, also not in exception
   messages. The asr worker joins an **internal Docker network** (no internet) with postgres + redis only;
   `HF_HUB_OFFLINE=1`; audio mounted read-only. Transcripts follow recording access rules; backup/retention in S7.
10. **Measurability:** latency formula tested with fixed timestamps; real "within 15 min" ⚠ NEEDS USER (office LAN).
    Acceptance run waits until the overnight benchmark has freed the GPU.
11. The Phase 2 "Done when" quality gate is the **gold-set WER** (8 kHz phone audio upsampled to 16 kHz), never the
    public WER.

## Amendments (builder + 3-reviewer pass, 2026-11-14; user may revise)
- **To decision 7 (reaper):** `running` rows older than 30 min go back to `queued` while tries < 2, else `error`.
  `queued` rows older than 30 min only count as lost when the asr queue is **empty** (Redis LLEN = 0; a long
  backlog is not a failure; queue length unknown → not reaped); each such re-send **counts a try**, so a lost
  message can be re-sent at most twice before the row becomes `error` — no endless loop.
- **To decision 7 (worker faults):** only errors of the call itself are terminal (bad/missing audio, the model
  failing on this audio, 15-min limit, 2nd CUDA OOM). A worker that cannot load its model (no offline cache, no
  CUDA, GPU held by another job) leaves the row `queued` **without counting a try** and re-sends it after 5 min
  (load re-tried at most every 2 min); a message for another model than the worker's `ASR_MODEL` is left queued.
  An unknown `ASR_MODEL` stops the worker at start. Bulk recovery: `transcribe --retry-errors --since D [--limit N]`.
- **To decision 9 (privacy):** DB engine created with `hide_parameters=True` app-wide; a failed transcript save is
  stored as `saving transcript failed (<type>)`; the asr worker gets no PBX secret / APP_SECRET (explicit
  environment, Yeastar settings only required by the PBX adapter); model cache mounted read-only.
- **Latency:** measured from `first_queued_at` (never rewritten by retries) to decide "live call"; value =
  `finished_at − call.end_time`.
