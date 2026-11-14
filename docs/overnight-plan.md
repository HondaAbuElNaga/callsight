# Overnight plan (2026-11-14) — Claude's recommendations, to be judged

Context: user asked Claude to finish all remaining roadmap phases (2–6) alone overnight. Rules from the user:
- every recommendation Claude makes is first checked by **2 judge agents**, who pick the best option;
- every finished spec is reviewed by **3 subagents** (bugs, security, accuracy) before merge;
- anything that needs the user is marked **`⚠ NEEDS USER`** in that spec's tasks.md;
- at the end, a Markdown report explains what was recommended and what was done.

Hard limits on this PC tonight:
- PC is probably NOT on the office LAN → no Yeastar API (live events, listen, new CDRs cannot be tested live).
  Yeastar allows max 8 tokens; never loop logins. Don't retry failed auth.
- GPU: RTX 5050 Laptop 8 GB; RAM 16 GB (Docker ~7.5 GB). One GPU model at a time.
- Gold-set reference texts (30 clips) are typed by the user → WER cannot be computed tonight.
- No satisfaction labels exist → the 1–10 model cannot be fine-tuned tonight.
- Local git only, no remote: "push" = merge the spec branch into `main`.

## R1. Order and grouping of the remaining work (one spec each)

| # | Spec | Roadmap items covered | Blocked parts (⚠ NEEDS USER) |
|---|---|---|---|
| S1 | Finish `2026-11-14-asr-benchmark`: adapters smoke-tested (Whisper ✅, Audar ✅, Jais 4-bit running), add `python -m callsight goldset score` that computes WER/CER from the DB texts + jsonl and **writes `docs/asr-benchmark.md` automatically** | Phase 2 items 1–2 | user types 30 texts, then runs bench + score (one command each) |
| S2 | Transcription pipeline: `asr` Celery queue, GPU worker container, model chosen by config (default **Whisper large-v3** until the benchmark picks a winner), 30 s chunking, segments with timestamps stored in DB, new answered calls queued automatically, backfill command | Phase 2 items 3–4 | speaker split (pyannote is a gated HF model: user must accept its licence + give an HF token) |
| S3 | Transcript view next to the player + .txt export + search in transcripts | Phase 2 item 5 | — |
| S4 | KPI engine (SQL views) + KPI dashboard (ECharts, week/month, agent/team, trend) + PDF export (WeasyPrint, Arabic RTL) + scheduled weekly/monthly reports archive | Phase 4 all | check numbers against a hand calculation for one real week |
| S5 | Call analysis: local LLM via Ollama (summary, topic, action items, 1–10 pre-score), bad-word/escalation flags from a YAML list, silence/talk-ratio metrics; labelling page (supervisor rates 1–10, LLM pre-filled); fine-tune script for MARBERT regression (code + test on tiny synthetic data) | Phase 3 all | label ≥ 500 calls; then run fine-tune; pick Ollama model download |
| S6 | Live board (Yeastar WebSocket → our API WebSocket) + Listen button (`call/listen` to supervisor ext 114) + listen audit log | Phase 5 all | test on office LAN; listen feature still paused by the user (built behind a config switch, OFF) |
| S7 | Hardening: agent role (own calls only), nightly backups (pg_dump + audio copy) with retention, disk-space alert, `YEASTAR_CA_FILE`, pinned dependency versions, health/monitoring page, scale-out guide; rules editor UI for bad words / KPI targets | Phase 6 all | backup target disk/NAS path; real restore drill on the server |

Recommended build order: **S1 → S4 → S2 → S3 → S5 → S7 → S6**.
Why: S4 (reports) only needs data we already have and is the most visible value; S2/S3 give transcripts that S5 needs;
S7 before S6 because S6 cannot be tested tonight anyway and is paused by the user.
Alternative considered: strict roadmap order (S1, S2, S3, S5, S4, S6, S7) — slower to give value, S5 (LLM) and S2 (ASR)
compete for the 8 GB GPU.

## R2. Speech-to-text serving (roadmap said "speaches" server)

- **Option A (recommended):** no separate STT server. A Celery `asr` worker in a GPU container imports our existing
  `callsight.asr.bench` adapters (one model in memory, chosen by `ASR_MODEL` in `.env`). Reason: speaches only serves
  faster-whisper models; Audar (qwen-asr) and Jais (transformers 5) are not supported, so a custom backend would be
  needed anyway; one less service; our adapters are already tested.
- Option B: speaches server + custom backend for non-Whisper models.
- Option C: speaches for Whisper only, our adapter for others (two code paths).

## R3. Speaker separation (mono recordings)

- **Option A (recommended):** build segments with `speaker = null` now; add pyannote-audio diarization behind
  `DIARIZATION=on` that only runs when an HF token is present (gated model) ⚠ NEEDS USER. Sentiment/LLM uses the whole
  text until then; the UI shows one stream.
- Option B: heuristic split (first speaker = agent on inbound) — unreliable on mixed mono audio.
- Option C: skip diarization entirely.

## R4. Satisfaction score before labels exist

- **Option A (recommended):** LLM-only baseline score (Ollama, Arabic-capable model ≤ 8 GB, e.g. Qwen2.5-7B-Instruct
  Q4) stored with `model_version = "llm-baseline"`; labelling page lets the supervisor correct it; fine-tune script
  ready for when ≥ 500 labels exist. GPU is shared: `nlp` and `asr` queues run one at a time on this PC.
- Option B: wait for labels, no score until then.

## R5. PDF reports

- **Option A (recommended):** WeasyPrint in the API image (already in tech stack) with the bundled IBM Plex Sans
  Arabic font; charts as server-rendered SVG (simple bars made in Python), so no headless browser is needed.
- Option B: headless Chromium (Playwright) printing the dashboard page — heavier image (~400 MB), new tool.

## R6. Approvals while the user sleeps

- **Option A (recommended):** each spec's open questions are decided by the 2 judges; the spec is marked
  `approved (by judges on behalf of user, 2026-11-14 — user may revise)`; every decision is listed in the final report
  so the user can overturn it in the morning. Nothing irreversible (no PBX settings changes, no deletes, no external
  sharing, no paid services).
- Option B: write specs as `draft` only and build nothing.

## R7. Testing without the office LAN

- **Option A (recommended):** unit/integration tests with recorded fixtures (existing `tests/fixtures/cdr_shapes.json`
  style) for Yeastar WebSocket + listen; no live PBX calls tonight; live checks listed as ⚠ NEEDS USER.

---

## Judges' decisions (2 independent judge agents, 2026-11-14) — binding for tonight

| # | Judge 1 | Judge 2 | Final decision |
|---|---|---|---|
| R1 order | MODIFY | MODIFY | Order S1 → S4 → S2 → S3 → S5 → S7 → S6. **Must:** S1, S4, S2, S3. **Should:** S5. **Stretch:** S7, S6. Stop cleanly at a spec boundary; only fully built + reviewed specs are merged, unfinished ones stay on their branch. S4 shows "avg satisfaction" as "n/a" (not 0) until S5; S4 numbers flagged "not trusted until Phase 1 Done-when (7 OK reconciles)". S6: live board OFF by default, Listen only a stub (user paused it). |
| R2 STT serving | MODIFY A | MODIFY A | No speaches. `asr` worker images per model family (same Dockerfile, `REQS` build arg): one for Whisper/Audar (transformers 4.57), one for Jais (transformers 5). `ASR_MODEL` must match the compose profile; only one runs. Update tech-stack.md + roadmap first. Pin the Jais model revision; `HF_HUB_OFFLINE=1` after download. Audar never default (licence). |
| R3 diarization | ACCEPT A | MODIFY A | Nullable `speaker` + `diarization_version` on segments. pyannote open pipeline only (never the pyannoteAI cloud key). Accepting the gated licence + HF token = ⚠ NEEDS USER. Roadmap text "split stereo channels" corrected (recordings are mono). |
| R4 score | MODIFY A | MODIFY A | Ollama model in config, default `qwen2.5:7b` (Apache-2.0); quick Arabic test vs `qwen3:8b`, keep the better. LLM score labelled "تقدير أولي" (unvalidated), kept OUT of KPI avg satisfaction + PDFs. Labelling: a blind 100-call test set without pre-fill. Ollama bound to the internal Docker network, `keep_alive=0` so ASR gets the GPU. Model download (~5 GB) allowed. |
| R5 PDF | ACCEPT A | ACCEPT A | WeasyPrint + Pango libs in the API image + bundled IBM Plex Sans Arabic TTF (OFL); test extracts Arabic text from the PDF; check < 30 s. |
| R6 approvals | MODIFY A | MODIFY A | Judges approve specs on the user's behalf (user may revise). Tag `main` as `pre-overnight-2026-11-14`; `--no-ff` merge per spec. `pg_dump` before every migration; additive migrations only. Never tick a roadmap item whose "Done when" is unverified (use `[~]`). Never: accept licences, create accounts/tokens, touch PBX settings, paid/cloud APIs, delete audio. |
| R7 testing | ACCEPT A | MODIFY A | **No Yeastar login at all tonight** (8-token limit). Tests mock the network; WebSocket fixtures labelled "synthetic (from Yeastar docs)". New PBX services OFF by default, no auth retry, back off when unreachable. Live check on office LAN = ⚠ NEEDS USER. |

Extra items from the judges: disk-space check before image builds; Docker RAM ~7.5 GB → concurrency 1, one model in memory; ASR backfill capped + resumable (no full overnight backfill); log queue latency for "transcript within 15 min" (⚠ verify on LAN); transcript search with Arabic normalisation + `pg_trgm`; PDPL: transcripts/summaries in backup + retention; Phase 0 leftovers (legal announcement, server purchase) listed for the user; final report lists every model download + licence.
