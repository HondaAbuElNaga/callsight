# Tech Stack

> Change this file **before** adopting a new tool.
> Status: ✅ in use · 🔜 planned · ❓ depends on Phase 0 (PBX discovery)

Principle: **100% local**, Docker Compose on one server now; every heavy step is a
queue worker, so scaling = more workers / a second GPU box, not a rewrite.

## Telephony integration
| Area | Choice | Status | Why |
|---|---|---|---|
| PBX | **Yeastar P-Series** at LAN `192.168.50.151:8088` (plan/firmware TBC), Yealink IP phones | ✅ | API works over LAN (2026-11-12); has REST API, WebSocket events, `*51` Listen |
| Yeastar integration | Yeastar P-Series OpenAPI: `cdr/*`, `recording/*`, WebSocket events 30011/30012 (30008 agent status cut until real payloads are captured); `call/listen` not wired (adapter `listen()` refuses); `httpx` + `websockets` | ✅ / 🔜 | Needs Enterprise/Ultimate plan. Redis key `pbx:auth_blocked` stops every PBX login (worker, CLI, live) after a refused login or errcode 60002 until `pbx-unblock`. Token budget: worker 1 + CLI 1 + live 1 = 3 of 8 |
| PBX adapter layer | `PbxAdapter` interface: `fetch_cdr()`, `fetch_recording()`, `listen()` (refuses while Listen is off); live events run in the separate `live` service (`callsight/live/`, compose profile `live`, off by default) | ✅ | One code path for the app; only the adapter changes per PBX brand |
| Asterisk-based PBX (FreePBX/Issabel/MikoPBX…) | AMI (events, originate ChanSpy) + CDR MySQL + MixMonitor recordings | ❓ | Most common; ChanSpy with `q` option = silent listen |
| 3CX / Yeastar / Grandstream | Their REST/Call-Control API + recording export | ❓ | Fallback if not Asterisk |
| Recording format | **Mono WAV, 8 kHz, 16-bit** (confirmed 2026-11-12) | ✅ | Both voices mixed, so agent/customer separation needs diarization |
| Speaker diarization | pyannote-audio (as used in whisper-diarization / whisperX), local | 🔜 | Recordings are mono; sentiment must run on the customer's speech only |

## Backend
| Area | Choice | Status | Why |
|---|---|---|---|
| Language | Python 3.12 | 🔜 | Same language for API, ML and telephony |
| API | FastAPI + WebSockets | 🔜 | Async, live board push, auto OpenAPI docs |
| Database | PostgreSQL 16 | 🔜 | Calls, transcripts, scores, KPIs; full-text search; scales far beyond 200 calls/day |
| ORM / migrations | SQLAlchemy 2 + Alembic, driver psycopg 3 | 🔜 | Added 2026-11-12 (call-ingest spec): typed models, versioned schema changes; standard with FastAPI + Postgres |
| Transcript search | `pg_trgm` (Postgres contrib extension): trigram GIN index on `transcript.search_text` | ✅ | Added 2026-11-14 (transcript-view spec): fast `LIKE '%word%'` on Arabic text; trusted extension on PostgreSQL 16, so the DB owner creates it in migration 0007 (no superuser) |
| Config files | PyYAML (`config/teams.yaml`) | 🔜 | Added 2026-11-12: team extensions/queues editable without code |
| Dev runtime | Docker Desktop on the analytics PC (192.168.50.189) until the server arrives | ✅ | Same Compose file moves to the server unchanged |
| Queue / workers | Redis + Celery (separate queues: `ingest`, `asr`, `nlp`, `reports`) | 🔜 | Each stage scales independently; GPU queue isolated |
| Audio storage | Local disk volume (path in DB), optional MinIO later | 🔜 | Simple; MinIO only if storage moves to another box |
| Scheduler | Celery Beat | 🔜 | CDR polling, nightly reconcile, auto weekly/monthly reports |

## AI / ML (all on the local GPU)
| Area | Choice | Status | Why |
|---|---|---|---|
| Speech-to-text | Candidates: **IFM Jais-ASR**, **Audar-ASR-V1-Turbo**, Whisper large-v3 (via Hugging Face transformers) — pick by benchmark on our own calls | 🔜 | Public benchmark (echonova arabic-asr-benchmark): Saudi WER Jais-ASR 20.9%, Audar 23.8%, Whisper-large-v3 31.1% |
| STT serving | **Own Celery `asr` worker** in a GPU container (no speaches): loads the model named by `ASR_MODEL` once per process (adapters in `callsight/asr/models.py`), frees the GPU after `ASR_IDLE_UNLOAD_S` idle. Image per model family: `asr-worker` (Whisper/Audar, transformers 4.57.6) and `asr-worker-jais` (transformers 5, server only). Internal Docker network (no internet), `HF_HUB_OFFLINE=1` | 🔜 | Changed 2026-11-14 (transcription spec, overnight decision R2): speaches only serves faster-whisper models; Audar/Jais need our own adapters anyway, one less service |
| Arabic text tools | CAMeL Tools (normalisation, dialect ID) | 🔜 | Standard Arabic NLP toolkit, works on Windows/Linux |
| Sentiment / satisfaction model | Fine-tuned **`CAMeL-Lab/bert-base-arabic-camelbert-da`** (Apache-2.0, revision `231698eab9ebf0ae7b518a64277b81b2fe829f2d` pinned, name in config) → 1–10 regression (`callsight/analysis/train.py`, runs in the asr-worker image, `HF_HUB_OFFLINE=1`); input 512 tokens, head+tail truncation; whole-call text until diarization exists | 🔜 | Changed 2026-11-14 (call-analysis spec, decision 1): MARBERT/MARBERTv2 excluded, their licence is research-only (commercial use needs the authors' written permission). Training run waits for ≥ 500 labels |
| Bootstrap labels + summaries | **Ollama** `ollama/ollama:0.40.13` (image pinned by digest in docker-compose.yml, MIT), compose profile `llm`, GPU, on the `internal: true` network only (no internet), `keep_alive=0`, `OLLAMA_DEBUG` off. Models pulled by a separate one-off container with no call data mounted: **`qwen2.5:7b`** (Apache-2.0, ~4.7 GB Q4, default) and `qwen3:8b` (Apache-2.0, ~5.2 GB, `think:false`) for the comparison; model in `LLM_MODEL`. Output = JSON schema in Ollama `format` + pydantic validation (pydantic is already installed with FastAPI, no new package) | ✅ | Adopted 2026-11-14 (call-analysis spec, decisions 3, 6, 7): summary, topic, action items and a first, unvalidated satisfaction estimate ("تقدير أولي"), never used in KPIs/PDFs |
| Training | Hugging Face Transformers + PyTorch (the exact-pinned asr-worker image: transformers 4.57.6, torch 2.8.0) | 🔜 | Fine-tuning and inference; no new image for training |
| ASR benchmark containers | Image 1 (Whisper + Audar): PyTorch 2.8.0 CUDA 12.8 base, transformers 4.57.6, qwen-asr 0.0.6, accelerate, soundfile, soxr. Image 2 (Jais): torch 2.11.0+cu128 (PyTorch index only), transformers 5.16.1, compressed-tensors 0.20.1 (official 4-bit weights). All exact-pinned; model revisions pinned; runs with no network (`network_mode: none`) | ✅ | Added 2026-11-14 (asr-benchmark spec); changed after review: faster-whisper and bitsandbytes were planned but are not used |

## Frontend & reports
| Area | Choice | Status | Why |
|---|---|---|---|
| Web UI | React + Vite + TypeScript, RTL Arabic UI | 🔜 | Live board, call list, transcript player, report button |
| Charts | Apache ECharts `echarts@6.1.0` (exact pin, npm) | ✅ | Adopted 2026-11-14 (kpi-reports spec). No native RTL: axes mirrored by hand, Arabic font set explicitly |
| PDF reports | WeasyPrint `weasyprint==70.0` (HTML → PDF) + apt `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0` in the app image; bundled IBM Plex Sans Arabic TTF (OFL, `callsight/reports/fonts/`, from the IBM plex GitHub release `@ibm/plex-sans-arabic@1.1.0`); charts are SVG bars drawn in Python | ✅ | Adopted 2026-11-14 (kpi-reports spec): proper Arabic shaping and RTL, no headless browser |
| PDF test helper | `pypdf==6.21.0` (test only: extracts the Arabic text back from the PDF) | ✅ | Added 2026-11-14 (kpi-reports spec) |
| Auth | Local users + roles (admin, supervisor, agent) with JWT | 🔜 | Agents see only their own calls |
| Auth libraries | argon2-cffi (password hashing), PyJWT (signed login token in an HttpOnly cookie) | 🔜 | Added 2026-11-13 (web call list spec) |
| Web server | uvicorn (runs the FastAPI app) | 🔜 | Added 2026-11-13; standard FastAPI server |
| Frontend build | Node.js 22 inside the Docker build stage only (no Node on the PC) | ✅ | Added 2026-11-13; builds the React/Vite app |
| Arabic font | IBM Plex Sans Arabic, bundled via `@fontsource/ibm-plex-sans-arabic` (served locally, nothing from the internet) | ✅ | Added 2026-11-13 (review finding: was missing here) |

## Infrastructure
| Area | Choice | Status | Why |
|---|---|---|---|
| OS | Ubuntu Server 24.04 LTS | 🔜 | Best NVIDIA/Docker support |
| Deploy | Docker Compose (later: same images on k3s if needed) | 🔜 | One command install; same images scale out |
| Server (to buy) | 1× NVIDIA GPU 16–24 GB VRAM (RTX 4060 Ti 16GB minimum, RTX 4090/5090 24GB+ comfortable), 8+ core CPU, 64 GB RAM, 1 TB NVMe (system/DB) + 4 TB HDD (audio) | 🔜 | 200 calls/day ≈ 10 h audio/day; one GPU transcribes that in well under an hour |
| Backup | Our own `python -m callsight backup`: `pg_dump -Fc` (REPEATABLE READ snapshot, counts saved) + Python incremental copy of recordings/reports (sha256 manifest) into `BACKUP_DIR`; `backup verify` restores into the scratch DB `callsight_restore_check`. Needs **`postgresql-client-16`** (pg_dump/pg_restore, exact pin `16.16-1.pgdg13+1`; bump it when PGDG drops that build) from the PostgreSQL apt repo (PGDG, `apt.postgresql.org`) in the app image: Debian's own client is another major version, which cannot dump our v16 server | 🔜 | Adopted 2026-11-14 (hardening spec, decision 2): no new backup tool; restic/borg (dedup + encryption) = later idea. Target should be an encrypted second disk/NAS |
| Locked Python deps | **pip-tools** (`pip-compile --generate-hashes --allow-unsafe`), run only inside a `python:3.12-slim` linux/amd64 container (not installed on the PC or in the image): `requirements.in` (ranges) → `requirements.lock` (exact + hashes); the app image installs with `pip install --require-hashes`. Test-only tools in `requirements-dev.in` / `requirements-dev.lock` | 🔜 | Adopted 2026-11-14 (hardening spec, decision 7): reproducible builds, changed wheels are rejected |
| Tests | pytest, Playwright for UI | 🔜 | |
