# Tasks: Hardening

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–10 in spec.md)
- [x] 2. Tech stack (postgresql-client-16, pip-tools) before adopting — `product/tech-stack.md` rows "Backup" and
         "Locked Python deps" (commit d28db6c)
- [x] 3. Tests first: `tests/test_agent_role.py` (route walk, deny by default), `test_backup.py` (rotation, manifest,
         tmp/rename, snapshot counts, verify refusals/scratch drops), `test_backup_pg.py` (real pg_dump/pg_restore,
         Docker only), `test_retention.py`, `test_health.py` (fakes + endpoint), `test_pbx_tls.py`
- [x] 4. Agent role: `visible_scope(user)` → `Scope` is a **required** field of `CallFilter` (list, summary, search share
         it), `visible_call` for detail + recording (404 for others' calls, no audit row), other agents' extension /
         voicemail legs removed from detail, server overrides `agent=`, `/api/agents` = own entry, `/api/kpi` forced to
         own ext (team ignored, 403 if the ext left teams.yaml), reports/goldset/health 403. `ROLES` + `STAFF_ROLES`,
         `create-user --role agent` needs `--ext` in teams.yaml, `set-ext` bumps `token_version`. Migration
         **0006** (backup_run, job_run, retention_run, retention_log; down_revision 0005; additive). Web: KPI page
         hides team/agent filters, PDF export and archive for agents; KPI link shown to agents.
- [x] 5. `backup` / `backup verify`: REPEATABLE READ + `pg_export_snapshot()` counts, `pg_dump -Fc --snapshot`,
         client major = server major check, free-disk check, `.tmp` → rename, sidecar `<dump>.json` (counts + alembic
         version), files copy (recordings status ok + report archive) size+mtime ±2 s, sha256 manifest, rotation 14
         daily + 8 weekly (own names only). Verify: hard-coded `callsight_restore_check`, refuses that name as live
         DB, `DROP … WITH (FORCE)` before + after, `pg_restore --no-owner --no-acl --exit-on-error`, compares counts +
         alembic version. Celery `backup_nightly` 01:30 on queue `maint` with a Redis lock (reconcile waits/retries
         while it is held); compose: worker `-Q ingest,reports,maint` + `${BACKUP_HOST_DIR}:/backup`, api `/backup:ro`.
         **Run on this PC against the live DB** (throwaway container from the test image, host network, audio/reports
         mounted read-only, `--no-record` = no write to the live DB, target `D:/callsight-backup-test`):
         `backup ok: callsight-20261114-141008.dump (192464 bytes, 14 tables); 418 new files copied (745976357 bytes)`
         → `restore drill OK (callsight-20261114-141008.dump): 14 tables, row counts equal` (call 911, call_leg 1676,
         alembic 0005); scratch DB gone afterwards (checked `pg_database`). Real round trip also in
         `test_backup_pg.py` (Docker: passed).
- [~] 6. Health page (admin) + banner + GPU heartbeat hook: `callsight/health.py` (disks system/audio/backup,
         `pg_database_size`, Redis LLEN per queue, job_run last success per job, Celery ping, backup age > 26 h red,
         same-disk warning until `BACKUP_OFFSITE_CONFIRMED=true`, last 7 reconciles, GPU from the Redis heartbeat with
         TTL 90 s, grey when absent), `/api/health` admin only, `HealthPage.tsx` + red admin banner (polls every 2 min).
         Heartbeat: `worker_ready` signal starts a thread only where `torch` is installed (= the asr worker).
         Not done: screenshot with real values (needs the stack rebuilt with migration 0006 — orchestrator/user).
- [x] 7. `YEASTAR_CA_FILE` (SSLContext, CERT_REQUIRED, missing file = error), `YEASTAR_TLS_CHECK_HOSTNAME`,
         `YEASTAR_CERT_SHA256` pin checked before every login (before the secret is sent), warning when verification
         is off, `pbx-cert --fetch` (prints fingerprint only) / `--accept <sha256>` (saves PEM only on match).
         **Not run** (no PBX contact tonight).
- [x] 8. Retention code (off, dry-run): `config/retention.yaml` (`days: null`), `RETENTION_ENABLED` + days ≥ 30, first
         run per `days` value always dry, deletes only DB-row paths inside AUDIO_DIR + copies in BACKUP_DIR/files/audio,
         `transcript` / `call_analysis` rows when those tables exist, call → `recording_status='deleted'` (reconcile
         no longer counts/re-fetches it), retention_log per call, audit rows > 730 days deleted. Beat 04:30 on `maint`.
         Tested on temp dirs only.
- [x] 9. App lock file + `--require-hashes`: `requirements.in` → `requirements.lock` (57 pkgs) + `requirements-dev.lock`
         (pytest, pypdf) via pip-tools 7.5.1 in `python:3.12-slim` linux/amd64 (`tools/lock-deps.sh`), constrained to
         the versions in the current image (`pip freeze` identical before/after). Dockerfile: `--require-hashes
         --no-deps` + `pip check`, `INSTALL_DEV=true` for test images; base images pinned by digest. Full suite in the
         rebuilt image: **303 passed**. GPU images: not hashed (stretch, [~]).
- [x] 10. `docs/scale-out.md`: keep .env safe, backups, restore procedure, more workers / 2nd GPU box, agents/teams,
         PBX certificate, updating packages (short version).
- [ ] 11. ⚠ NEEDS USER: encrypted 2nd disk/NAS for BACKUP_DIR (then `BACKUP_OFFSITE_CONFIRMED=true`); create agent
         accounts (`create-user NAME --role agent --ext …`); retention days + `RETENTION_ENABLED`; PBX fingerprint
         (`pbx-cert --fetch` on the LAN, compare, `--accept`)
- [ ] 12. Verify; review with 3 subagents; fix; spec → done/[~]; roadmap; merge (--no-ff)

## Test results (2026-11-14)
- PC: `295 passed, 8 skipped` (skips: WeasyPrint/PDF + pg_dump-only tests).
- Docker (`callsight-s7-test`, built with `--require-hashes`, `INSTALL_DEV=true`): `303 passed`.

## Assumptions / decisions made while building
- Migration is **0006** (down_revision 0005 = transcription branch), not "0008" as in task 4's text. On this branch
  alone `alembic upgrade` cannot run (0005 missing) — it works after the transcription merge. Tests use `create_all`.
- Agents see only calls where they are `agent_ext` (decision 1), not `missed_agent_ext` rings. A user with role
  agent but no ext, or an unknown role, sees nothing (deny by default).
- Agent KPI view = `kpi.report(agent=own ext)`: no team tiles, no other agents, top repeaters only theirs (masked).
- Dump sidecar `.json` holds the counts, so `verify` needs no live-DB row (works for "backup before migration" on an
  older schema and in disaster recovery). The `backup_run` row is written only when the table exists (`--no-record`
  forces no DB writes). The health page uses `backup_run` (after 0006).
- Free-disk check for the restore drill is on BACKUP_DIR (≥ 4 × dump size): the DB volume is on the same PC here.
- Health disk names: "system" = `/` inside the container (Docker's disk, C: on this PC), "audio" (D:), "backup".
  Job thresholds: ingest 10 min, recordings 20 min, reconcile/backup 26 h. Answers cached 15 s (worker ping 1 s).
- Lock: Redis `SET NX EX` (6 h TTL). The asr task (transcription branch) should skip/wait while `maint_busy()` —
  wire at merge (`from callsight.locks import maint_busy`). The GPU heartbeat already starts from `callsight.jobs`
  (`worker_ready`), which the asr worker imports.
- Gold-set clips are not touched by retention (ASR benchmark; user decides).
- `job_run` tracks ingest, recordings, reconcile, sync_agents, backup, retention (decorator `tracked`).
- Pre-existing Linux-only bug fixed: recording `.part` cleanup raised `NotADirectoryError` instead of `PbxError`
  (failed `test_disk_error_becomes_pbx_error` in Docker, also on the KPI image).
- PGDG keeps only the newest 16.x: the exact pin `16.16-1.pgdg13+1` must be bumped when the build can't find it.
- Not done (stretch): load test with 60,000 seeded calls (scratch DB), GPU-image hashes, real-values screenshot.
