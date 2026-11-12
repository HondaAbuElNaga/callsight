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
| Speech-to-text | Candidates: **IFM Jais-ASR**, **Audar-ASR-V1-Turbo**, Whisper large-v3 (via faster-whisper) — pick by benchmark on our own calls | 🔜 | Public benchmark (echonova arabic-asr-benchmark): Saudi WER Jais-ASR 20.9%, Audar 23.8%, Whisper-large-v3 31.1% |
| STT serving | speaches (OpenAI-compatible local STT server, model load/unload on GPU); custom HF backend if the winning model isn't supported | 🔜 | Idea ASR-4: swap ASR models by config, no code change |
| Arabic text tools | CAMeL Tools (normalisation, dialect ID) | 🔜 | Standard Arabic NLP toolkit, works on Windows/Linux |
| Sentiment / satisfaction model | Fine-tuned Arabic BERT (MARBERT / CAMeLBERT-DA) → 1–10 regression on the customer side of the transcript | 🔜 | "Tuned" model: start from a dialect-aware model, fine-tune on our labelled calls |
| Bootstrap labels + summaries | Local LLM via Ollama (Arabic-capable, e.g. ALLaM / Qwen class) | 🔜 | Pre-label calls 1–10 for a human to correct, then train the BERT model; later summaries |
| Training | Hugging Face Transformers + PyTorch | 🔜 | Fine-tuning and inference |

## Frontend & reports
| Area | Choice | Status | Why |
|---|---|---|---|
| Web UI | React + Vite + TypeScript, RTL Arabic UI | 🔜 | Live board, call list, transcript player, report button |
| Charts | Apache ECharts | 🔜 | Good RTL/Arabic support |
| PDF reports | WeasyPrint (HTML → PDF) with an Arabic font (e.g. Noto Naskh / IBM Plex Sans Arabic) | 🔜 | Proper Arabic shaping and RTL |
| Auth | Local users + roles (admin, supervisor, agent) with JWT | 🔜 | Agents see only their own calls |

## Infrastructure
| Area | Choice | Status | Why |
|---|---|---|---|
| OS | Ubuntu Server 24.04 LTS | 🔜 | Best NVIDIA/Docker support |
| Deploy | Docker Compose (later: same images on k3s if needed) | 🔜 | One command install; same images scale out |
| Server (to buy) | 1× NVIDIA GPU 16–24 GB VRAM (RTX 4060 Ti 16GB minimum, RTX 4090/5090 24GB+ comfortable), 8+ core CPU, 64 GB RAM, 1 TB NVMe (system/DB) + 4 TB HDD (audio) | 🔜 | 200 calls/day ≈ 10 h audio/day; one GPU transcribes that in well under an hour |
| Backup | Nightly `pg_dump` + audio rsync to a second disk/NAS | 🔜 | Recordings are business records |
| Tests | pytest, Playwright for UI | 🔜 | |
