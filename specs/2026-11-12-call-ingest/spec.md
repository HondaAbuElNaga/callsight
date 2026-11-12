# Spec: Call ingest (CDR → database, call status, recordings)

**Roadmap item:** Phase 1, items 1–4 ("PbxAdapter", "Call data model", "Unanswered-call classification",
"Recording fetch + nightly reconciliation") + Phase 0 item "Repo skeleton". The web UI (item 5) is the next spec.
**Status:** done (2026-11-12)

## Goal
Every call on the Yeastar PBX lands in our own PostgreSQL database within a few minutes of hang-up,
grouped as **one call** (not one row per leg), with the agent who answered, ring/wait/talk time and a
correct status (answered / no-answer / abandoned / busy / voicemail), and its recording downloaded.
This is the data every later phase (transcripts, scores, KPIs) is built on (mission: "Don't know how
many calls were missed", "Don't know how long calls take").

## Facts from the real PBX (checked 2026-11-12, 1,000 latest CDR rows)
- One call = **several CDR rows with the same `uid`** (legs). Most common inbound shapes:
  | Shape (legs in id order) | Count / 646 calls | Meaning |
  |---|---|---|
  | IVR ANSWERED → agent ext ANSWERED → Queue ANSWERED | 110 | answered by agent via queue |
  | IVR ANSWERED → Queue NO ANSWER | 53 | caller hung up while waiting in queue (abandoned) |
  | IVR ANSWERED only | 33 | caller hung up in the IVR menu |
  | ext NO ANSWER → Voicemail | 8 | direct call to an extension, went to voicemail |
  | ext ANSWERED / ext NO ANSWER | 10 | direct call to an extension (DID) |
- Outbound (314) and internal (107) calls are one or two rows.
- Queues (from `queue/list`): **6401 خدمة العملاء** (agents **104, 119**, ring all) and **6400 المبيعات** (19 agents, ring all).
- Queue wait: the agent leg's `ring_duration` is the time the caller waited in the queue (e.g. 1,236 s wait, 86 s talk).
- The PBX writes **no hold time** in the CDR, and for ring-all queues **no row for agents who rang but didn't pick up**.
- `cdr/detail` does not exist on this PBX (errcode 10001). `recording/download?file=<record_file>` works.
- CDR ids are increasing integers (`cdr/list` sorted by id); total ≈ 560 calls/day for the whole PBX.

## Scope
- In:
  - Docker Compose dev stack on this PC (until the server arrives): PostgreSQL 16, Redis, `worker` + `beat` (Celery).
  - `PbxAdapter` interface + `YeastarAdapter` (CDR fetch since id, recording download, queue/extension list). Reuses the probe's client code.
  - Database schema with migrations: `agent`, `queue`, `call`, `call_leg`, `ingest_state`, `reconcile_run`.
  - Ingest job every 2 min: new CDR rows → legs → grouped into calls → classified.
  - Recording job: download the recording of every answered call to the audio folder, path stored on the call.
  - Nightly reconciliation job (02:00): compares yesterday's PBX CDR rows/recordings with ours, re-fetches anything missing, stores a report.
  - Agent/queue sync from the PBX (`queue/list`, `extension/list`) once a day; team extensions set in a small config file.
  - **Only team calls are saved** (decision 2): a call is kept if any of its legs is from/to a team extension
    (**104, 119, 116**) or reaches a team queue (**6401**). All other PBX calls are skipped.
  - CLI: `python -m callsight ingest|backfill --days N|reconcile --date D|sync-agents|stats --date D`.
- Out:
  - Web UI (next spec, Phase 1 item 5).
  - Live WebSocket events and "agent rang but didn't pick up" for queue calls (needs event 30008 → Phase 5 live stream spec).
  - Hold time (not in CDR; stored as empty for now, see open question 3).
  - Transcription and scores (Phase 2/3). Any write to the PBX.

## Requirements
1. **Adapter.** `PbxAdapter` (abstract) with `fetch_cdr_since(last_id) -> list[CdrRow]`, `fetch_cdr_between(start, end)`,
   `download_recording(file, dest) -> Path`, `list_queues()`, `list_extensions()`. `YeastarAdapter` implements it with
   token refresh (token lives 30 min), retries with back-off on network errors, and **stops after 3 failed logins**
   (Yeastar auto-blocks the IP on repeated bad auth).
2. **Legs.** Every CDR row is stored once in `call_leg` (unique on PBX CDR `id`), with the raw JSON kept for audits.
   Leg kind is derived from `call_to`: `ivr`, `queue`, `voicemail`, `extension`, `external`.
3. **Calls.** Legs are grouped by `uid` into one `call` (unique on `uid`). A late leg updates the existing call and the call
   is re-classified. Stored per call: direction (inbound/outbound/internal), customer number, DID, start time (from Unix
   `timestamp`, stored UTC, shown Asia/Riyadh), entry point (ivr / queue / direct), queue, team, answering agent,
   `ivr_seconds`, `wait_seconds` (queue wait or ring time), `talk_seconds`, `hold_seconds` (NULL for now), `total_seconds`,
   `status`, `recording_file`, `recording_path`, `recording_status`.
4. **Status rules** (one status per call, first match wins):
   | Status | Rule |
   |---|---|
   | `answered` | an `extension` or `external` leg is ANSWERED (IVR and queue "ANSWERED" never count) |
   | `voicemail` | a `voicemail` leg exists |
   | `abandoned_queue` | reached a queue, no agent answered |
   | `abandoned_ivr` | inbound, only IVR legs (hung up in the menu) |
   | `busy` | last ring leg is BUSY |
   | `no_answer` | anything else that rang and wasn't answered (incl. FAILED) |
   For `no_answer` / `voicemail` on a direct call, the rung extension is stored as `missed_agent`.
5. **Agents & teams.** Agents/queues are synced from the PBX; `config/teams.yaml` lists the team extensions with their team
   (104, 119 = customer service; 116 = after-sales) and team queues (6401). Only calls touching these are stored
   (decided per `uid` over all its legs). Calls that end in the IVR menu (no queue, no agent) can't be linked to the team and are skipped.
6. **Recordings.** For every call with a `record_file`, the file is downloaded once to `AUDIO_DIR/YYYY/MM/DD/<file>`,
   checked to be a valid WAV, and `recording_status` set to `ok` / `missing` / `error` (retried up to 3 times).
7. **Reconciliation.** For a given day: PBX CDR ids in that day vs our `call_leg` ids, and answered customer calls
   (inbound/outbound) without a downloaded recording. Internal calls are not recorded by the PBX, so they are not expected. Missing rows are fetched and ingested; the run is saved in `reconcile_run` (counts, missing before, missing after).
8. **Config & secrets** only from `.env` (existing Yeastar keys + `DATABASE_URL`, `REDIS_URL`, `AUDIO_DIR`). Phone numbers
   are never written to logs in full (reuse `mask_phone`).
9. **Idempotent.** Running ingest, backfill or reconcile twice never creates duplicates or changes results.

## Acceptance criteria
- [x] `docker compose up -d` starts postgres, redis, worker, beat; `docker compose ps` shows all 4 healthy/running → all 4 up
- [x] `pytest` passes, with tests for every status rule built from the real masked call shapes above (≥ 1 test per shape) → 86 passed, 18 shapes
- [x] `python -m callsight backfill --days 7` loads 7 days; `stats` shows calls per status per day; running it again adds 0 rows → 894 calls, re-run new_legs=0
- [x] For one full day, team calls (`uid`) and their legs in our DB = team calls in PBX `cdr/list` for the same day (100%, by `reconcile`) → 2026-11-11: 181/181 calls, 313 legs
- [x] 20 random answered calls checked by hand against the PBX Call Logs page: agent, talk time and status all correct
      → user spot-checked 2 of the 20 (incl. #2, 141 s, ext 119) against CDR + recording file, accepted the rest as matching
- [x] The 3 example shapes "abandoned in queue", "hung up in IVR" and "voicemail" are classified as such by test; 1 real call each for
      "answered" and "abandoned in queue" (an IVR-only call can't be linked to the team, so it is not stored: requirement 5)
      → tests pass; real calls 19:19:59 answered (119) and 19:23:43 + 19:30:02 abandoned_queue
- [x] With the stack running, a new test call appears in the DB within 5 min of hang-up with its recording downloaded (`recording_status=ok`) → < 2 min
- [x] `reconcile --date <yesterday>` reports 0 missing legs and 0 missing recordings for answered calls → 2026-11-11 OK (91 recordings expected, 0 missing)

## Technical approach
- Package `callsight/` (new): `pbx/base.py` (PbxAdapter), `pbx/yeastar.py`, `db/models.py` (SQLAlchemy 2), `db/migrations/`
  (Alembic), `ingest/classify.py` (pure functions: legs → call fields; most tests live here), `ingest/service.py` (the logic), `jobs.py`
  (Celery tasks: `ingest_new` → triggers `fetch_recordings`, `reconcile_yesterday`, `sync_agents`), `cli.py`, `config.py`.
- `tools/pbx_probe.py` stays a standalone discovery tool (own client and tests); the app uses `pbx/yeastar.py`.
- Ingest cursor: `ingest_state.last_cdr_id`; each run pages `cdr/list` (id desc) until it is 300 ids **below** that id (overlap),
  so a call whose legs were split across two runs is still seen whole (team filter is per `uid`); upserts make the overlap harmless. A call is
  re-classified whenever a leg arrives; nightly reconcile catches late/missed legs.
- Classification is a pure function of the legs, so it is fully unit-tested with fixtures in `tests/fixtures/` taken from
  real masked rows (`docs/samples/`).
- Docker: `python:3.12-slim` image for the app; audio folder and Postgres data are Docker volumes mapped to `D:\callsight-data`
  on this PC (later the server's 4 TB disk).
- Risks: (a) the PBX keeps only limited CDR history; backfill only as far as it goes. (b) PC must be on and on the office LAN
  for the stack to run until the server arrives; reconcile fills gaps when it comes back. (c) A queue call transferred
  between agents gives extra legs; first version credits the **first** agent who answered (open question 4).
  (d) Disk: ≈ 16 KB/s audio → ~30 GB/month for the whole PBX.

## Open questions
1. **Which extensions are the team?** From the PBX: customer-service queue 6401 = **104** (خدمة عملاء المعهد) and **119**
   (محمود هشام); after-sales = **116** (متابعه المبيعات)? supervisor = **503** (د. حسام مشرف) or **114**?
   Recommendation: confirm these 4; everything else is still ingested but not in team KPIs.
2. ~~Ingest all PBX calls or only the team's?~~ → decided: team only (decision 2).
3. **Hold time:** the CDR doesn't have it. Recommendation: AHT = talk time for now (mission allows it); get hold time later
   from live events in Phase 5 if needed.
4. **Transferred calls:** credit the first agent who answered, or split talk time between agents? Recommendation: first
   agent for now, keep all legs so we can change the rule later.
5. **New tools (tech stack update):** SQLAlchemy 2 + Alembic (DB schema & migrations) and psycopg 3 (Postgres driver),
   PyYAML (teams config). Recommendation: approve; they are the standard choices with FastAPI/Postgres.
6. **Git:** Steps 7–10 (branch, review, commit) need git. Recommendation: `git init` locally now (no GitHub push unless you want it).

## Decisions
1. Approved by user 2026-11-12.
2. **Save only the team's calls**: extensions **104, 119** (customer service, queue 6401) and **116** (after-sales). User's choice;
   adding an agent later = add to `config/teams.yaml` + run `backfill` (only as far back as the PBX keeps CDR).
3. Supervisor extension (503 or 114) not decided yet; not needed in this spec.
4. Open questions 3–5 take the recommendation: AHT = talk time for now; transferred call → first answering agent; new tools approved.
5. Git: local repository (no GitHub), created 2026-11-12.
