# Roadmap

> Status: `[x]` done · `[~]` in progress · `[ ]` not started

**Current position (2026-11-15, night) — START HERE in a new session:**
- AWS main server (Frankfurt) live: https://calls.mohanadyehia.com, fixed address 63.184.90.120, accounts `admin` and
  `supervisor` (ext 114). Pushing to `main` on GitHub (private repo `HondaAbuElNaga/callsight`) updates it automatically.
- PBX link works through `sstli.ras.yeastar.com`: calls every 2 minutes, recordings download. Employee list loaded
  (104 خدمة عملاء المعهد, 116 متابعه المبيعات, 119 خدمة العملاء محمود هشام); refreshes nightly at 03:00.
- Live board ON on AWS (`LIVE_ENABLED=true`, "subscribed to PBX call events").
- Phase 7 (agents + supervisor) is live, plus the KPI / agent changes of 2026-11-15 night (see Phase 7 list).
- Server commands without SSH: GitHub → Actions → **Server task** (`.github/workflows/server-task.yml`: make-supervisor,
  new passwords, live on/off/log, sync-agents). Starting two at once cancels the waiting one. A password printed in a
  run log: delete that run after copying it. By hand: AWS console → EC2 Instance Connect (first add an SSH rule from
  `3.120.181.40/29` to security group callsight-main, delete it afterwards).
- **Next:** (1) GPU server (user step A4c in [user-steps](../specs/2026-11-15-aws-deploy/user-steps.md), quota approved)
  → transcription + analysis on AWS, then `ASR_START_DATE`; (2) create agent accounts on the users page, set the
  follow-up start date; (3) stop the PC's old services; (4) the rest of the order list in `docs/overnight-report.md`.
- Known small items: live board login waits a full 10 min when its wait ends exactly at the lock expiry
  (`callsight/live/service.py` LoginGate); `/api/calls` still answers agents (own calls only, page hidden); the KPI
  download log keeps only the start date of a chosen range.
- Step-by-step state: [tasks](../specs/2026-11-15-aws-deploy/tasks.md), guide: [docs/aws.md](../docs/aws.md).

**Position before the AWS move (2026-11-14):** All Phase 1 items **built**: call ingest ([spec](../specs/2026-11-12-call-ingest/spec.md)) and
web call list ([spec](../specs/2026-11-13-web-call-list/spec.md)), Arabic page on http://127.0.0.1:8080 (this PC only; accounts admin + supervisor 114).
**Phase 1 "Done when" still open:** 7 consecutive days of nightly reconcile = OK. Needs the PC on the office LAN as 192.168.50.189
(on 2026-11-13/14 it was on another network, 192.168.100.x, so ingest paused; it catches up automatically).
**Overnight run 2026-11-14:** 7 specs built, reviewed and merged; live DB at migration 0008: ASR benchmark, KPI + PDFs,
transcription, hardening, transcript view, live board (off), call analysis (automatic runs off). Details and the list of
steps that need the user: [docs/overnight-report.md](../docs/overnight-report.md).
**Next:** the user's steps (type 30 gold-set texts → pick the ASR model; office LAN; `ASR_START_DATE`; rate calls).
**Phase 0 leftovers (parallel):** live-events test, silent listen (paused), server purchase, legal recording announcement.

## Phase 0: PBX discovery & server (week 1)

**Goal:** Know exactly how to get calls out of the existing PBX, and have the server ready.
**Done when:** a written `docs/pbx-discovery.md` answers every question below, and one
real test call's CDR row + recording file has been pulled to the server by hand.

- [x] Identify the PBX: **Yeastar P-Series**, LAN `192.168.50.151:8088` (model/firmware/plan still unknown: no Dashboard access; not blocking) ([spec](../specs/2026-11-12-pbx-discovery/spec.md)) (idea: user)
- [x] Find CDR + recording access: via Yeastar API (`cdr/list`, `recording/download`); recording is on; format **mono WAV 8 kHz** → diarization needed in Phase 2 (idea: user)
- [~] API access ✅ (`tools/pbx_probe.py token/cdr/recording` work); live WebSocket events ⏳ not tested; agent/supervisor extension mapping ⏳ waiting for user; inbound calls go through IVR 6200 (idea: user)
- [~] Silent listen: Yeastar feature code `*51`+ext / API `call/listen`. **Paused by user 2026-11-12, resume later.** State:
  user has Feature Code access; Listen permission = monitor ext **114** (= supervisor, confirmed by user 2026-11-13); phone 114 "Key As Send" was `*` (ate the star) → told to set `#`;
  `*51119` still declined while 119 talking. Next: check **Extension 119 → Features → Call Monitor** setting, then run
  `tools/listen_test.py 119 114 300` during a 103↔119 call (API `call/listen` returns the exact error code). WebSocket subscribe already works. (idea: user)
- [x] Connect over LAN, not the internet remote-access address (`sstli.ras.yeastar.com` gave 70087 IP FORBIDDEN); IP Restriction allows PC `192.168.50.189` (idea: user)
- [x] Remove old public-IP rule `66.212.118.128` from API IP Restriction (user) → done 2026-11-12, API login still OK
- [ ] Legal recording announcement for customers (Saudi PDPL consent); recording already on
- [ ] ~~Buy & install server~~ → changed by the user 2026-11-15: **run on AWS** (Frankfurt, changed from Bahrain the same day: that region did not answer; small server always on + GPU server woken every 30 min, ≈ $110/month) — spec approved 2026-11-15; repository part built (work-left command, health item, GPU on/off service, compose files, CloudFormation file, `docs/aws.md`), nothing created on AWS yet ([spec](../specs/2026-11-15-aws-deploy/spec.md)) (idea: user)
- [x] Repo skeleton: Docker Compose (Postgres, Redis, worker, beat), `.env` config (done in call-ingest spec; API container comes with the web UI; CI later)

## Phase 1: Call ingest, duration & unanswered calls (MVP data)

**Goal:** Every call is in our database with correct timings and status.
**Done when:** for 7 consecutive days, our call counts match the PBX's own report 100%,
and each call has ring/talk/hold time + status (answered / no-answer / abandoned / busy).

- [x] `PbxAdapter` interface + adapter for our PBX brand (CDR poll; live events → Phase 5) ([spec](../specs/2026-11-12-call-ingest/spec.md)) (idea: user)
- [x] Call data model: call, call legs, agent, caller number, timings, status, recording path (hold time not in CDR → later) (idea: user)
- [x] Unanswered-call classification (no-answer, abandoned in queue, busy, voicemail; "rang agent not picked" for queue calls needs live events → Phase 5) (idea: user)
- [x] Recording fetch + link to call; nightly reconciliation job (finds missing calls/recordings) (idea: user)
- [x] Basic web UI: login, call list with filters (date, agent, status), play recording ([spec](../specs/2026-11-13-web-call-list/spec.md)) (idea: user)

## Phase 2: Transcription (Saudi Arabic)

**Goal:** Readable text for every answered call, agent and customer separated.
**Done when:** chosen model WER ≤ 25% on 30 hand-corrected own calls; every new answered
call has a transcript within 15 min of hang-up.

- [~] Collect & hand-correct 30 representative calls (gold set: 30 × 60 s clips cut ✅; ⚠ typing by user open) ([spec](../specs/2026-11-14-asr-benchmark/spec.md)) (idea: user)
- [x] Benchmark Jais-ASR / Audar-ASR / Whisper-large-v3 on gold set, pick the winner (scored on 30 typed clips: Audar 30.1%, Whisper 40.3%, Jais 47.1% WER; **user chose Whisper-large-v3** (MIT licence), confirmed 2026-11-15) (source: [echonova-ai-solutions/arabic-asr-benchmark](https://github.com/echonova-ai-solutions/arabic-asr-benchmark))
- [x] STT model chosen by config (`ASR_MODEL`), served by our own `asr` worker, not a separate server (changed 2026-11-14, decision R2; speaches can't run Audar/Jais) ([spec](../specs/2026-11-14-transcription/spec.md)) (source: [speaches-ai/speaches](https://github.com/speaches-ai/speaches))
- [x] `asr` worker queue: mono recording → 30 s windows → transcribe → store segments with timestamps; speaker split via diarization later (recordings are mono, not stereo) ([spec](../specs/2026-11-14-transcription/spec.md)) (idea: user) ([spec](../specs/2026-11-14-transcription/spec.md))
- [x] Transcript view next to the audio player; export transcript as .txt (idea: user) ([spec](../specs/2026-11-14-transcript-view/spec.md))
- [ ] Later idea: redaction of spoken ID/card/phone numbers in transcripts and the .txt export (PDPL) ([spec](../specs/2026-11-14-transcript-view/spec.md), decision 9)

## Phase 3: Satisfaction score 1–10 (fine-tuned model)

**Goal:** Every analysed call gets a trustworthy 1–10 customer-satisfaction score.
**Done when:** on a 100-call human-rated test set, score is within ±2 of the human on ≥ 80% of calls.

- [~] Labelling tool: supervisor rates calls 1–10 (pre-filled by local LLM to go faster; first 100 = frozen blind test set without pre-fill) — built + tested (synthetic data), deployed (live DB at 0008); no real call rated yet ⚠ NEEDS USER ([spec](../specs/2026-11-14-call-analysis/spec.md)) (idea: user)
- [ ] Label ≥ 500 calls = 400 training + the 100 blind test calls (LLM pre-label + human correction) ⚠ NEEDS USER: rate on the page "تقييم المكالمات", 100 blind first (idea: user)
- [~] Fine-tune CAMeLBERT-DA (Apache-2.0; MARBERT excluded: research-only licence) as 1–10 regressor; compare with LLM-only baseline on the blind set — training code `callsight/analysis/train.py` + Docker self-test done; real run waits for ≥ 500 labels; customer-side text needs diarization ([spec](../specs/2026-11-14-call-analysis/spec.md)) (idea: user)
- [~] `nlp` worker: score each call; store score + model version; re-score job when model updates — built for the LLM first estimate (model + prompt version stored, `analyze --rescore`); the tuned model's score comes after training ([spec](../specs/2026-11-14-call-analysis/spec.md)) (idea: user)
- [~] LLM call summary: summary, topic, action items — local Ollama `qwen2.5:7b`, JSON schema + validation; deployed; 4 real calls analysed through the nlp worker (1 more skipped: poor audio); automatic runs off (`NLP_ENABLED=false`); page not yet checked in a browser ⚠ NEEDS USER ([spec](../specs/2026-11-14-call-analysis/spec.md)) (source: [transcriptionstream/transcriptionstream](https://github.com/transcriptionstream/transcriptionstream))
- [~] Bad words / escalation flags — matching built (`config/rules.yaml`, whole words, normalised); real word lists ⚠ NEEDS USER ([spec](../specs/2026-11-14-call-analysis/spec.md)) (source: [DinoTheDinosaur/speech_analytics](https://github.com/DinoTheDinosaur/speech_analytics))
- [~] Silence, interruption & talk-ratio metrics from channel timings — silence metrics built (RMS, no diarization); talk ratio + interruptions need diarization (recordings are mono) ([spec](../specs/2026-11-14-call-analysis/spec.md)) (source: [DinoTheDinosaur/speech_analytics](https://github.com/DinoTheDinosaur/speech_analytics))

## Phase 4: KPI engine & reports ([spec](../specs/2026-11-14-kpi-reports/spec.md); built 2026-11-14, "Done when" hand check ⚠ open)

**Goal:** One click gives a correct weekly or monthly PDF per agent and for the team.
**Done when:** KPIs for a test week match a hand calculation exactly; PDF (Arabic RTL)
generates in < 30 s; weekly/monthly reports also auto-generate on schedule.

- [x] KPI engine: calls, unanswered, AHT, avg satisfaction, repeat calls in 72h — definitions in mission.md (idea: user)
- [x] KPI dashboard: filter by week/month, agent, team (CS vs after-sales), charts + trend vs previous period (idea: user)
- [x] "Export report" button → PDF per agent + team summary, week or month (idea: user)
- [x] Scheduled reports: Sunday morning weekly, 1st of month monthly, saved in archive (idea: user)

## Phase 5: Live calls & silent listen

**Goal:** Supervisor sees all active calls and can listen silently from their Yealink phone.
**Done when:** live board updates within 2 s of call state change; "Listen" button rings
the supervisor's phone and connects silently to the chosen call (customer + agent unaware).

- [~] Live event stream from PBX adapter → WebSocket → live board (who's on call, caller, duration, queue waiting) (idea: user) — built; **on since 2026-11-14** (Call Status Monitor on trunk SSTLI); the user saw a real test call appear on the board; delay not timed, real 30011 payload not saved yet ([spec](../specs/2026-11-14-live-board/spec.md))
- [~] "Listen" button: PBX originates spy call (ChanSpy `q` / vendor monitor) to supervisor extension (idea: user) — paused by user; 403 stub only, legal steps ⚠ NEEDS USER ([spec](../specs/2026-11-14-live-board/spec.md))
- [x] Access control + audit log of who listened to which call (idea: user) — staff only, append-only listen_audit ([spec](../specs/2026-11-14-live-board/spec.md))

## Phase 6: Hardening & scale

**Goal:** Runs unattended, safe, and grows with the team.
**Done when:** restore-from-backup drill succeeds; load test of 2,000 calls/day processes without backlog.

- [x] Roles & permissions (admin / supervisor / agent sees own calls only) ([spec](../specs/2026-11-14-hardening/spec.md))
- [ ] Rules editor UI: manager edits bad-word lists, thresholds, KPI targets without code (source: [Azure-Samples/tayra](https://github.com/Azure-Samples/tayra))
- [x] Backups (DB + audio), retention policy for recordings, disk alerts ([spec](../specs/2026-11-14-hardening/spec.md))
- [x] Trust the PBX's own self-signed certificate (`YEASTAR_CA_FILE`) instead of `YEASTAR_VERIFY_SSL=false` (review 2026-11-12) ([spec](../specs/2026-11-14-hardening/spec.md))
- [x] Pin exact dependency versions with hashes (pip-tools) (review 2026-11-12) ([spec](../specs/2026-11-14-hardening/spec.md))
- [x] Monitoring: queue lengths, worker health, GPU usage ([spec](../specs/2026-11-14-hardening/spec.md))
- [~] Scale-out guide: extra workers / second GPU box; add new agents/queues by config (short version: docs/scale-out.md) ([spec](../specs/2026-11-14-hardening/spec.md))

## Phase 7: Agent + supervisor work ([spec](../specs/2026-11-15-agent-supervisor/spec.md))

**Goal:** agents log in and close the loop on every real customer call; the supervisor sees kept / missed callbacks.
**Done when:** agents fill forms for 2 weeks on the live server, and the admin switches the follow-up KPIs on after
the HR/legal OK.

- [x] Users page (admin): create / disable supervisors and agents, new password, extension (idea: user)
- [x] Follow-up form after answered calls > 20 s, callback time; callback checked from outgoing calls (idea: user)
- [x] Follow-up KPIs: forms on time %, callbacks on time % (show-only until the admin sets the date) (idea: user)
- [x] Supervisor shares a call with a note; agent marks it seen; agents see the summary only (idea: user)
- [x] Delete one call's content (recording, texts, forms) with reason + admin log (idea: user)
- [x] Live: merged to `main` on 2026-11-15 (PR #1)
- [x] User's changes 2026-11-15 night (branch `feat/kpi-date-range`, 3-agent review, 644 tests): KPI page only من/إلى
  dates (any range ≤ 366 days, compared with the same number of days before; PDF follows); ما بعد البيع shows
  outgoing calls only (page + PDF); KPI page and `/api/kpi` for admin + supervisor only; agents: no employee filter,
  no calls list, المتابعات is their only page (a call opens from it); follow-ups column "رد العميل؟" from the PBX
  record (any answered team callback), never a button (idea: user)
- [ ] Create the agent accounts, set the follow-up start date (⚠ NEEDS USER)
