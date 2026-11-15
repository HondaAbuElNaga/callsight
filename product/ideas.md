# Enhancement Ideas

Researched from similar GitHub projects. Status: proposed · approved · rejected.
Approved ideas are copied into the roadmap.

## Search log
| Date | Method | Keywords | Repos read |
|---|---|---|---|
| 2026-11-11 | gh CLI | "call center analytics", "speech analytics", "arabic speech recognition", "arabic sentiment", "saudi dialect", "whisper diarization", "asterisk ari", "faster-whisper", "whisperx", "camel-tools", "call center", topic:asterisk | transcriptionstream/transcriptionstream, MahmoudAshraf97/whisper-diarization, Azure-Samples/tayra, DinoTheDinosaur/speech_analytics, speaches-ai/speaches, echonova-ai-solutions/arabic-asr-benchmark, asterisk/asterisk-websocket-examples, m-bain/whisperX, CAMeL-Lab/camel_tools |

Gaps: no maintained open-source "Asterisk queue KPI" or "Arabic call-center analytics" project found; KPI logic will be our own.

## User requirements (core scope)
| ID | Idea | What it adds | Source | Effort | Status |
|---|---|---|---|---|---|
| USR-1 | Call ingest + duration | Every PBX call with ring/talk/hold time | user | M | approved |
| USR-2 | Unanswered call tracking | Missed / abandoned / busy calls | user | S | approved |
| USR-3 | Transcript extraction | Saudi Arabic text of every call | user | M | approved |
| USR-4 | Satisfaction score 1–10 | Fine-tuned Arabic sentiment model | user | L | approved |
| USR-5 | Live call board + silent listen | Supervisor spies on live calls | user | M | approved |
| USR-6 | Weekly/monthly KPI PDF per agent | Calls, unanswered, AHT, avg satisfaction, 72h repeats | user | M | approved |

## Speech-to-text (ASR)
| ID | Idea | What it adds | Source | Effort | Status |
|---|---|---|---|---|---|
| ASR-1 | Benchmark ASR on our own calls | Pick Jais-ASR / Audar / Whisper by real WER on 30 Saudi calls, using their Arabic normalizer + scoring code | [echonova-ai-solutions/arabic-asr-benchmark](https://github.com/echonova-ai-solutions/arabic-asr-benchmark) | S | approved |
| ASR-2 | VAD before transcription | Skips silence/hold music; fewer hallucinated words, faster | [m-bain/whisperX](https://github.com/m-bain/whisperX) | S | rejected |
| ASR-3 | Word-level timestamps | Click a word in the transcript → audio jumps there | [m-bain/whisperX](https://github.com/m-bain/whisperX) | M | rejected |
| ASR-4 | OpenAI-compatible local STT server | Swap ASR models without code changes; auto load/unload models on GPU | [speaches-ai/speaches](https://github.com/speaches-ai/speaches) | M | approved |
| ASR-5 | Noise suppression | Cleaner audio on bad mobile lines before ASR | [DinoTheDinosaur/speech_analytics](https://github.com/DinoTheDinosaur/speech_analytics) | M | rejected |

## Call analysis
| ID | Idea | What it adds | Source | Effort | Status |
|---|---|---|---|---|---|
| AN-1 | LLM call summary | 3-line summary, topic, action items per call (local Ollama) | [transcriptionstream/transcriptionstream](https://github.com/transcriptionstream/transcriptionstream) | S | approved |
| AN-2 | Full-text transcript search | Search all calls for a word (product name, complaint) | [transcriptionstream/transcriptionstream](https://github.com/transcriptionstream/transcriptionstream) | S | rejected |
| AN-3 | Agent script checklist | Did the agent greet, verify, offer help, close politely? Score per call | [Azure-Samples/tayra](https://github.com/Azure-Samples/tayra), [DinoTheDinosaur/speech_analytics](https://github.com/DinoTheDinosaur/speech_analytics) | M | rejected |
| AN-4 | Bad words / threat detection | Flag insults or escalation (customer or agent) | [DinoTheDinosaur/speech_analytics](https://github.com/DinoTheDinosaur/speech_analytics) | S | approved |
| AN-5 | Silence & interruption metrics | Long silences, agent talking over customer, talk ratio | [DinoTheDinosaur/speech_analytics](https://github.com/DinoTheDinosaur/speech_analytics) | S | approved |
| AN-6 | Configurable evaluation rules UI | Manager edits rules/keywords without code | [Azure-Samples/tayra](https://github.com/Azure-Samples/tayra) | M | approved |

## Live & platform
| ID | Idea | What it adds | Source | Effort | Status |
|---|---|---|---|---|---|
| LIVE-1 | Live transcript of active calls | Stream call audio via Asterisk ARI external media WebSocket → text appears live | [asterisk/asterisk-websocket-examples](https://github.com/asterisk/asterisk-websocket-examples) | L | rejected |
| PLAT-1 | Drop-folder ingestion | Any audio dropped in a folder gets processed — fallback if the PBX API is limited, also for old recordings | [transcriptionstream/transcriptionstream](https://github.com/transcriptionstream/transcriptionstream) | S | rejected |

## From the user, 2026-11-15 (discussion only; the user said: do not write specs yet)
- **Admin page:** create supervisor / agent users; delete data. Claude's advice: user management first; delete =
  one call with its recording, text and analysis, with confirmation and a log (who, when, which call), not free
  editing of tables.
- **Supervisor shares a call with a note:** the agent sees the call, its insights and the supervisor's comment.
  Advice: show the summary and the comment; hide the 1-10 estimate from agents until the model is validated.
- **Agent follow-up form after every answered call:** what happened, problem solved or not, expected time, who
  must solve it; if not solved, the agent picks a callback time. Advice: very short form, responsible person from a
  list, one click "no follow-up needed" for wrong numbers; the AI summary arrives up to ~35 min later on AWS.
- **Missed callback lowers the agent's KPI.** Advice: the system checks the callback itself from outgoing calls to
  that number; own KPI "callbacks on time %" (+ "forms filled %"); an unanswered attempt counts; grace time;
  repeat call after "solved" is flagged; two weeks show-only; HR/legal OK before it judges staff.
- Suggested order: users page, follow-up form + callback check, supervisor notes, the KPIs, delete with log.
- **Open questions to the user:** (1) agents see the summary only, or also the score? (2) callback by any team
  member or only the same agent? (3) form for every answered call or only calls longer than ~20 s?
