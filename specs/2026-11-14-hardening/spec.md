# Spec: Hardening — agent role, backups + retention, disk/health monitoring, PBX certificate, locked dependencies

**Roadmap items:** Phase 6: "Roles & permissions", "Backups (DB + audio), retention policy, disk alerts",
"Trust the PBX's own self-signed certificate (`YEASTAR_CA_FILE`)", "Pin exact dependency versions with hashes",
"Monitoring: queue lengths, worker health, GPU usage", "Scale-out guide". ("Rules editor UI" → see Open question 4.)
**Status:** draft

## Goal
The system runs unattended and safely: agents see only their own calls, data is backed up and old personal data is
removed on schedule (PDPL), problems (full disk, stopped worker, growing queue) are visible, and the build is
reproducible. Phase 6 **Done when:** restore-from-backup drill succeeds; load test of 2,000 calls/day without backlog.

## Facts (checked 2026-11-14)
- Roles today: `admin`, `supervisor` (`app_user.role`, `app_user.ext`). Agents: 104, 119 (customer service),
  116 (after-sales). The user→extension mapping for agents is ⚠ waiting for the user (who gets an account).
- Data: Postgres (calls, legs, users, audits, gold set, then transcripts/analysis/reports), audio in
  `D:\callsight-data\audio` (WAV, ~1 MB/min), reports in a reports dir. No backup exists today.
- PBX TLS: `YEASTAR_VERIFY_SSL=false` today (self-signed certificate).
- Python deps: `requirements.txt` with `>=` ranges; bench images now exact-pinned.
- The production server is not bought yet; this PC is the only machine.

## Scope
- In:
  1. **Agent role:** `create-user NAME --role agent --ext 104`; agents see only calls where they are `agent_ext`
     or `missed_agent_ext` (list, summary, detail, recording, transcript, analysis), only their own KPIs; no gold
     set, rating, reports archive or admin pages. Enforced in the API (one helper), tested on every endpoint.
     Creating the real agent accounts = ⚠ NEEDS USER.
  2. **Backups:** `python -m callsight backup` (+ nightly beat job 01:30): `pg_dump -Fc` into `BACKUP_DIR/db/`,
     incremental copy of new audio + reports into `BACKUP_DIR/files/` (copy only new/changed files, never delete in
     the backup on its own), keep 14 daily + 8 weekly DB dumps; writes a `backup_run` row; `backup verify`
     restores the newest dump into a scratch DB `callsight_restore_check` and compares row counts (= the restore
     drill, automated). `BACKUP_DIR` default `D:\callsight-backup` on this PC ⚠ NEEDS USER: a second disk/NAS is
     the real target (same disk ≠ safe).
  3. **Retention (PDPL):** `config/retention.yaml`: recordings + transcripts + analysis older than N days
     (default **off**, user sets N) are deleted by a nightly job with a dry-run report first; audit rows kept 2 years.
     Turning it on = ⚠ NEEDS USER (business/legal decision).
  4. **Health page** (admin): disk free on audio/backup/DB volumes (warn < 15 %), queue lengths (ingest/asr/nlp),
     last success time of each scheduled job (ingest, recordings, reconcile, backup), worker ping, GPU memory (from the
     asr worker heartbeat), last 7 reconcile results (Phase 1 Done-when tracker). Red banner in the app for admins
     when something is wrong. No external alerting (no mail server) — later idea.
  5. **PBX certificate:** `YEASTAR_CA_FILE=/app/config/pbx-ca.pem` → httpx verifies against it; a helper
     `python -m callsight pbx-cert --fetch` saves the PBX's presented certificate for the user to confirm (prints
     its SHA-256 fingerprint) ⚠ NEEDS USER (on the office LAN; compare fingerprint with the PBX web page).
     Not run tonight (no PBX contact).
  6. **Locked dependencies:** `requirements.in` (ranges) → `requirements.lock` (exact + hashes, via pip-tools
     run in Docker), Dockerfile installs with `--require-hashes`.
  7. **Scale-out guide** `docs/scale-out.md`: moving to the server, extra workers per queue, second GPU box,
     adding agents/queues in `teams.yaml`, restore procedure.
- Out:
  - Load test of 2,000 calls/day on the real server (⚠ server not bought); here only a synthetic DB load test of
    the API/KPI queries with 60,000 seeded calls.
  - Rules editor UI (Open question 4).
  - External monitoring (Prometheus/Grafana), e-mail/SMS alerts.

## Acceptance criteria
- [ ] pytest: agent role on every endpoint (own calls only, 403/404 for others, no admin pages), backup rotation
      logic, retention dry-run selection, health checks with fakes, CA-file client config
- [ ] `backup` + `backup verify` run on this PC: dump restored into scratch DB, counts equal (restore drill ✅)
- [ ] Health page shows real values (screenshot)
- [ ] Image builds with `--require-hashes`
- [ ] Load test: KPI + call list API on 60,000 seeded calls within limits (list < 1 s, month KPI < 2 s)

## Open questions (decided by 2 judges on the user's behalf)
1. **Agents' visibility:** own calls only (recommended, roadmap) — including recordings/transcripts of own calls?
   Recommended: yes (they learn from their own calls), but **not** the LLM estimate or supervisor ratings.
2. **Backup tool:** our own `pg_dump` + Python copy (recommended: no new tool) vs restic/borg (dedup + encryption,
   new tool).
3. **Retention default:** off until the user decides (recommended) vs e.g. 365 days.
4. **Rules editor UI** (bad words / escalation phrases / KPI targets): build a simple admin page now that edits
   `rules.yaml` (needs S5 first) vs keep YAML-only for now (recommended if S5 isn't merged yet).
