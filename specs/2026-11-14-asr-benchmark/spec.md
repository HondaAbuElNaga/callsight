# Spec: Gold set + speech-to-text benchmark (on this PC)

**Roadmap items:** Phase 2, "Collect & hand-correct 30 representative calls (gold set)" and
"Benchmark Jais-ASR / Audar-ASR / Whisper-large-v3 on gold set, pick the winner"
**Status:** approved

## Goal
Choose the speech-to-text model for our own Saudi calls by measuring it, not by trusting public numbers. Our recordings are
phone quality (mono, 8 kHz, agent and customer mixed), much harder than public test sets. The winner must reach
**WER ≤ 25%** (mission success metric) on our gold set, and run fast enough for ~200 calls/day.

## Facts checked 2026-11-14
| Model | Size / GPU memory | Public Saudi WER | Runs with | License |
|---|---|---|---|---|
| `IFM/Jais-ASR` | ~6B, ~12 GB bf16 | 20.9% | transformers (`trust_remote_code`) | Apache 2.0 |
| `audarai/Audar-ASR-V1-Turbo` | 2.35B, ~4.7 GB bf16; 30 s window (chunking needed) | 23.8% | `qwen-asr` package | AudarAI Community License v1.0: limited commercial use, Enterprise licence for larger use |
| `openai/whisper-large-v3` | 1.55B, ~3–4 GB fp16 | 31.1% | faster-whisper / transformers | MIT |
Source: [echonova-ai-solutions/arabic-asr-benchmark](https://github.com/echonova-ai-solutions/arabic-asr-benchmark) (16 kHz test audio) and the model cards.
This PC: NVIDIA RTX 5050 Laptop, **8 GB** GPU memory, 16 GB RAM, Docker GPU access works. Server not bought yet.

## Scope
- In:
  - **Gold set**: 30 clips of **60 s** from real team calls (answered, inbound + outbound, agents 104 / 119 / 116, short and
    long calls, picked by a script with a fixed seed), cut from the middle of the call. Stored with the call id.
  - **Transcription page** in our web app (admin only): play a clip, type exactly what is said, save; shows progress (x / 30).
  - **Benchmark runner**: a GPU Docker container that runs each model on the 30 clips (8 kHz → 16 kHz), measures
    WER and CER after Arabic normalisation, speed (seconds of audio per second) and peak GPU memory.
  - Models: Whisper large-v3, Audar-ASR-V1-Turbo, Jais-ASR (4-bit, only if it fits in 8 GB; otherwise marked "needs server").
  - Report `docs/asr-benchmark.md`: table per model, 3 example clips side by side, recommendation.
- Out:
  - Production speech-to-text service, transcription of all calls, worker queue, transcript view (later Phase 2 items).
  - Speaker separation (agent vs customer); the gold set is the mixed text of both voices.
  - Any cloud service: audio never leaves this PC (only model files are downloaded from Hugging Face).

## Requirements
1. `python -m callsight goldset pick --n 30 --seconds 60 --seed 1` picks and cuts the clips into `AUDIO_DIR/goldset/`,
   records them in a `gold_clip` table (call id, start offset, length, file), and is repeatable (same seed → same clips).
2. Transcription page: list of the 30 clips with status (empty / done); per clip an audio player (with 5-second back
   button), a right-to-left text box, Save. Text saved in `gold_clip.reference_text` with who and when. Admin role only.
3. Normalisation before scoring follows the benchmark's `echonova-v1` rules (remove punctuation and harakat, unify
   alif/hamza forms, ة→ه, Eastern→Western digits), so our numbers compare with the public table.
4. `asr-bench` container (PyTorch built for CUDA 12.8+, needed by this GPU) runs one model at a time
   (`docker compose run --rm asr-bench --model whisper-large-v3`), writes per-clip hypotheses + timings to
   `data/asr-bench/<model>.jsonl` and never sends audio anywhere.
5. Report per model: WER, CER, real-time factor, peak GPU memory, failures (empty / truncated / repeated text).
6. Audio longer than a model's window is cut into ≤ 30 s chunks with 1 s overlap and joined.

## Acceptance criteria
- [ ] `pytest` passes, incl. tests for the normaliser (each rule), WER/CER on known examples, clip picking (repeatable,
      balanced across the 3 agents), chunking, and the transcription page API (admin only, save/load)
- [ ] 30 clips exist, each 60 s, from ≥ 3 different agents and both directions
- [ ] All 30 clips have a reference text typed by the user's team
- [ ] Whisper large-v3 and Audar-ASR-V1-Turbo each produce a hypothesis for 30/30 clips on this PC
- [ ] Jais-ASR: either 30/30 hypotheses (4-bit) or a written reason why it needs the server
- [ ] `docs/asr-benchmark.md` shows WER, CER, speed and GPU memory per model and names a winner (or says none reaches 25%)

## Technical approach
- `callsight/asr/`: `normalize.py`, `metrics.py` (word/character edit distance, corpus-level), `goldset.py` (pick + cut with the standard
  `wave` module; resampling with `soxr` in the bench container), `chunking.py`, `bench.py` (one entry point, one adapter
  per model).
- New DB table `gold_clip` (migration 0003); API `GET /api/goldset`, `GET /api/goldset/{id}/audio`, `PUT /api/goldset/{id}`
  (admin only); a "Gold set" page in the existing web app.
- `asr-bench` service in `docker-compose.yml` under a `bench` profile (not started by `up -d`), GPU reserved, model cache in
  a Docker volume. Runs only when the web/worker containers can spare the RAM (this PC has 16 GB, ~2 GB free today).
- Risks: (a) 8 GB GPU is too small for Jais in bf16; 4-bit may lower its accuracy, so its result is marked "4-bit".
  (b) Audar's licence may not allow our production use; benchmarking is fine, the licence is checked before choosing it.
  (c) Hand transcription takes time: ~30 minutes of audio ≈ 3–4 hours of typing. (d) RTX 50-series needs a recent
  PyTorch (CUDA 12.8+); older images won't see the GPU.

## Open questions
1. **Who types the 30 texts?** Recommendation: one person who knows the customers' dialect (e.g. an agent or the supervisor),
   ~3–4 hours in total, can be split over several days. The page saves after each clip.
2. **Clip length:** 30 × 60 s (≈ 30 min to type) or 30 full calls (≈ 75 min of audio, ~8–10 hours)? Recommendation: 60 s clips.
3. **Audar's licence** limits business use. Recommendation: include it in the test; if it wins, I prepare the question for
   AudarAI before we use it in production.
4. **New tools:** PyTorch (CUDA 12.8 build), transformers, faster-whisper, qwen-asr, bitsandbytes (4-bit), jiwer, soxr.
   Recommendation: approve; only used inside the `asr-bench` container.

## Decisions
1. Approved by user 2026-11-14.
2. The user types the 30 reference texts personally (admin account).
3. Clips: 30 × 60 s.
4. Audar-ASR is included in the test; its licence is checked before any production use.
5. New tools approved (bench container only). WER/CER are computed with our own small edit-distance code (no `jiwer` needed).
