# Tech Stack

> Change this file **before** adopting a new tool.
> Status: ✅ in use · 🔜 planned · ❓ depends on Phase 0 (PBX discovery)

Principle: **100% local**, Docker Compose on one server now; every heavy step is a
queue worker, so scaling = more workers / a second GPU box, not a rewrite.

## Telephony integration
| Area | Choice | Status | Why |
|---|---|---|---|
| PBX | **Yeastar P-Series** at LAN `192.168.50.151:8088` (plan/firmware TBC), Yealink IP phones | ✅ | API works over LAN (2026-11-12); has REST API, WebSocket events, `*51` Listen |
| Yeastar integration | Yeastar P-Series OpenAPI: `cdr/*`, `recording/*`, `call/listen`, WebSocket events 30011/30012/30008; `httpx` + `websockets` | 🔜 | Needs Enterprise/Ultimate plan; this is the first `PbxAdapter` |
| PBX adapter layer | `PbxAdapter` interface: `fetch_cdr()`, `fetch_recording()`, `live_events()`, `spy(call, supervisor_ext)` | 🔜 | One code path for the app; only the adapter changes per PBX brand |
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
| Sentiment / satisfaction model | Fine-tuned Arabic BERT (MARBERT / CAMeLBERT-DA) → 1–10 regression on the customer side of the transcript | 🔜 | "Tuned" model: start from a dialect-aware model, fine-tune on our labelled calls |
| Bootstrap labels + summaries | Local LLM via Ollama (Arabic-capable, e.g. ALLaM / Qwen class) | 🔜 | Pre-label calls 1–10 for a human to correct, then train the BERT model; later summaries |
| Training | Hugging Face Transformers + PyTorch | 🔜 | Fine-tuning and inference |
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
| Backup | Nightly `pg_dump` + audio rsync to a second disk/NAS | 🔜 | Recordings are business records |
| Tests | pytest, Playwright for UI | 🔜 | |
