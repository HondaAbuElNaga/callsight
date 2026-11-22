# Roadmap

> Status: `[x]` done · `[~]` in progress · `[ ]` not started

**Current position (2026-11-18) — START HERE in a new session:**

- **2026-11-22 supervisor close of a forwarded problem** (`docs/followup-form-and-forward.md`, «Supervisor close»): staff pick who in the chain solved it (KPI point, on time only within his 24 h), the ticket closes in the ERP by the worker. Migration 0029. LIVE 2026-11-22 (e1480af).
- **2026-11-22 shared calls tab** (`specs/2026-11-22-shares-tab/spec.md`): agent tab «ملاحظات المشرف» + archive, close after reply, 24 h reply KPI. LIVE 2026-11-22 (migration 0026). Review fixes LIVE the same day (migration 0027): each supervisor/admin has their own archive, a reply needs 10+ characters, an edit after the 24 h counts late, faster list.
- **The plan to the end of the project:** [finish-plan.md](finish-plan.md), steps 0–10, every spec approved
  2026-11-18 ("approve all": each open question = the recommendation). Build one at a time, 3-agent review, push to
  `main` only on the user's "ok push" (push = live deploy).
- **Step 0 live 2026-11-18:** priority, "لا تحتاج متابعة", "تحتاج حل", the forward chain to the customer-service
  system (any of 473 employees → branch supervisor → 3 managers), questions/answers, forwards KPI, training lookup.
  Keys on the server; the 7 supervisor exts linked. Left: one real test ticket from an agent (task D5).
- **Step 1 (cleanup)** on branch `spec/2026-11-17-cleanup`.
- **Running on AWS (Frankfurt):** https://calls.mohanadyehia.com (main server 63.184.90.120, accounts `admin`,
  `supervisor`); PBX link through `sstli.ras.yeastar.com` (calls every 2 min, recordings); live board on, team only
  (104, 116, 119, queue 6401); GPU server (g4dn.xlarge) wakes every 30 min: Whisper large-v3 transcripts + call
  analysis (`NLP_ENABLED=true`), `ASR_START_DATE=2026-11-15`; rating set of 500 calls ready to rate.
- **Server commands without SSH:** GitHub → Actions → **Server task** (`server-task.yml`: accounts, passwords, live
  on/off/log, sync-agents, rating-*, link-keys, forward-sync) and **GPU setup** (`gpu-setup.yml`: main-status,
  gpu-update, nlp-on, ...). After a deploy that changes code the GPU runs: run `gpu-update`.
- **Waiting for the user:** test forward (D5); rate the 500 calls (step 5); list of agents (step 2); company / HR
  answers (step 8).
- Still on the PC: the old services (switched off in step 2).

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
- [x] API access ✅ (`tools/pbx_probe.py token/cdr/recording` work); live WebSocket events ✅ (live board on AWS since 2026-11-15); extension mapping ✅ (`config/teams.yaml`: 104, 116, 119, supervisor 114); inbound calls go through IVR 6200 (idea: user)
- [~] Silent listen: Yeastar feature code `*51`+ext / API `call/listen`. **Paused by user 2026-11-12, resume later.** State:
  user has Feature Code access; Listen permission = monitor ext **114** (= supervisor, confirmed by user 2026-11-13); phone 114 "Key As Send" was `*` (ate the star) → told to set `#`;
  `*51119` still declined while 119 talking. Next: check **Extension 119 → Features → Call Monitor** setting, then run
  `tools/listen_test.py 119 114 300` during a 103↔119 call (API `call/listen` returns the exact error code). WebSocket subscribe already works. (idea: user)
- [x] Connect over LAN, not the internet remote-access address (`sstli.ras.yeastar.com` gave 70087 IP FORBIDDEN); IP Restriction allows PC `192.168.50.189` (idea: user)
- [x] Remove old public-IP rule `66.212.118.128` from API IP Restriction (user) → done 2026-11-12, API login still OK
- [ ] Legal recording announcement for customers (Saudi PDPL consent); recording already on
- [~] ~~Buy & install server~~ → changed by the user 2026-11-15: **run on AWS** (Frankfurt, changed from Bahrain the same day: that region did not answer; small server always on + GPU server woken every 30 min, ≈ $110/month) — spec approved 2026-11-15; repository part built (work-left command, health item, GPU on/off service, compose files, CloudFormation file, `docs/aws.md`), main server live 2026-11-15, GPU server + 30-min schedule live 2026-11-16; AWS tasks 15–18 open (7-day cost/latency check) ([spec](../specs/2026-11-15-aws-deploy/spec.md)) (idea: user)
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

- [x] Collect & hand-correct 30 representative calls (gold set: 30 × 60 s clips cut ✅; 30 texts typed and scored 2026-11-14) ([spec](../specs/2026-11-14-asr-benchmark/spec.md)) (idea: user)
- [x] Benchmark Jais-ASR / Audar-ASR / Whisper-large-v3 on gold set, pick the winner (scored on 30 typed clips: Audar 30.1%, Whisper 40.3%, Jais 47.1% WER; **user chose Whisper-large-v3** (MIT licence), confirmed 2026-11-15) (source: [echonova-ai-solutions/arabic-asr-benchmark](https://github.com/echonova-ai-solutions/arabic-asr-benchmark))
- [x] STT model chosen by config (`ASR_MODEL`), served by our own `asr` worker, not a separate server (changed 2026-11-14, decision R2; speaches can't run Audar/Jais) ([spec](../specs/2026-11-14-transcription/spec.md)) (source: [speaches-ai/speaches](https://github.com/speaches-ai/speaches))
- [x] `asr` worker queue: mono recording → 30 s windows → transcribe → store segments with timestamps; speaker split via diarization later (recordings are mono, not stereo) ([spec](../specs/2026-11-14-transcription/spec.md)) (idea: user) ([spec](../specs/2026-11-14-transcription/spec.md))
- [x] Transcript view next to the audio player; export transcript as .txt (idea: user) ([spec](../specs/2026-11-14-transcript-view/spec.md))
- [x] Agent / customer split (pyannote 3.1 diarization + naming rules g2): first-speaker check 27/30 on the gold calls, 2026-11-22 ([spec](../specs/2026-11-17-diarization/spec.md)) (idea: user)
- [-] ~~Later idea: redaction of spoken ID/card/phone numbers~~ — cancelled by the user 2026-11-19 ("not important"); spec kept: [2026-11-17-redaction](../specs/2026-11-17-redaction/spec.md)

## Phase 3: Satisfaction score 1–10 (fine-tuned model)

**Goal:** Every analysed call gets a trustworthy 1–10 customer-satisfaction score.
**Done when:** on a 100-call human-rated test set, score is within ±2 of the human on ≥ 80% of calls.

- [~] Labelling tool: supervisor rates calls 1–10 (pre-filled by local LLM to go faster; first 100 = frozen blind test set without pre-fill) — built + tested (synthetic data), live on AWS with the 500-call rating set (2026-11-16); no real call rated yet ⚠ NEEDS USER ([spec](../specs/2026-11-14-call-analysis/spec.md)) (idea: user)
- [ ] Label ≥ 500 calls = 400 training + the 100 blind test calls (LLM pre-label + human correction) ⚠ NEEDS USER: rate on the page "تقييم المكالمات", 100 blind first (idea: user)
- [~] Fine-tune CAMeLBERT-DA (Apache-2.0; MARBERT excluded: research-only licence) as 1–10 regressor; compare with LLM-only baseline on the blind set — training code `callsight/analysis/train.py` + Docker self-test done; real run waits for ≥ 500 labels; customer-side text needs diarization ([spec](../specs/2026-11-14-call-analysis/spec.md)) (idea: user)
- [~] `nlp` worker: score each call; store score + model version; re-score job when model updates — built for the LLM first estimate (model + prompt version stored, `analyze --rescore`); the tuned model's score comes after training ([spec](../specs/2026-11-14-call-analysis/spec.md)) (idea: user)
- [~] LLM call summary: summary, topic, action items — local Ollama `qwen2.5:7b`, JSON schema + validation; automatic on AWS since 2026-11-16 (`NLP_ENABLED=true`, main queues, GPU runs) ([spec](../specs/2026-11-14-call-analysis/spec.md)) (source: [transcriptionstream/transcriptionstream](https://github.com/transcriptionstream/transcriptionstream))
- [~] Bad words / escalation flags — matching built (whole words, normalised; lists edited on the page "القواعد" since the rules editor, first filled from `config/rules.yaml`); user checks the starter lists on the page ⚠ NEEDS USER ([spec](../specs/2026-11-14-call-analysis/spec.md)) (source: [DinoTheDinosaur/speech_analytics](https://github.com/DinoTheDinosaur/speech_analytics))
- [~] Silence, interruption & talk-ratio metrics from channel timings — silence metrics built (RMS, no diarization); talk ratio + interruptions built from the pyannote turns (diarization live 2026-11-22) ([spec](../specs/2026-11-14-call-analysis/spec.md)) (source: [DinoTheDinosaur/speech_analytics](https://github.com/DinoTheDinosaur/speech_analytics))

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

- [~] Live event stream from PBX adapter → WebSocket → live board (who's on call, caller, duration, queue waiting) (idea: user) — built; **on AWS since 2026-11-15, team-only since 2026-11-16** (Call Status Monitor on trunk SSTLI); the user saw a real test call appear on the board; delay not timed, real 30011 payload not saved yet ([spec](../specs/2026-11-14-live-board/spec.md))
- [~] "Listen" button: PBX originates spy call (ChanSpy `q` / vendor monitor) to supervisor extension (idea: user) — paused by user; 403 stub only, legal steps ⚠ NEEDS USER ([spec](../specs/2026-11-14-live-board/spec.md))
- [x] Access control + audit log of who listened to which call (idea: user) — staff only, append-only listen_audit ([spec](../specs/2026-11-14-live-board/spec.md))

## Phase 6: Hardening & scale

**Goal:** Runs unattended, safe, and grows with the team.
**Done when:** restore-from-backup drill succeeds; load test of 2,000 calls/day processes without backlog.

- [x] Roles & permissions (admin / supervisor / agent sees own calls only) ([spec](../specs/2026-11-14-hardening/spec.md))
- [~] Rules editor UI: admin (only) edits word lists + topics on the page "القواعد" (history, test a sentence, re-check 7 days) — built + reviewed 2026-11-18, waits for "ok push"; thresholds / KPI targets stay in code (spec scope) ([spec](../specs/2026-11-17-rules-editor/spec.md)) (source: [Azure-Samples/tayra](https://github.com/Azure-Samples/tayra))
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
- [x] Follow-up stages per call + agent page fixes ([spec](../specs/2026-11-15-followup-stages/spec.md), branch
  `spec/2026-11-15-followup-stages`, 3-agent review, 664 tests): مطلوب → حُلّت من أول مكالمة / بانتظار معاودة →
  تم الاتصال – أضف ملاحظة → حُلّت بعد المتابعة or the next round; stage KPIs (first-call solve %, solved after
  follow-up, open, average rounds, notes on time %); agent page: needs-action view (62 days), counters, urgent
  rows, refresh 60 s, copy/call number, auto-seen notes, form first. Live 2026-11-15 (PR #2) (idea: user)
- [x] Priority + no-follow-up visible + "تحتاج حل" + forward chain — **live 2026-11-18** ([spec](../specs/2026-11-16-priority-no-followup/spec.md)):
  parts 1–3 built (required priority عالية/متوسطة/منخفضة in the first form, 1 day to solve when عالية, priority
  filter + tag; "لا تحتاج متابعة" counter/filter + KPI/PDF columns; "تحتاج حل" side section whatever the dates;
  supervisor one-line view), 3-agent review per part; parts 1–4 live 2026-11-18 (forward chain, questions/answers, forwards KPI,
  training lookup); the first real test ticket (D5) is left (idea: user)
