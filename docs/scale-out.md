# Scale-out and recovery guide (short version)

Written 2026-11-14 (hardening spec). Everything runs with Docker Compose; the same files move to the server.

## 1. Keep `.env` safe — it is NOT in the backup

`.env` holds the PBX API secret, the database password and `APP_SECRET`. The backup deliberately leaves it out.
Keep one copy somewhere safe and separate (password manager or an encrypted USB stick). Without it a restore still
works, but you must create new secrets: a new `APP_SECRET` logs everyone out; a new PBX API secret is made in the
Yeastar portal.

## 2. Backups

- Nightly at 01:30 (Riyadh) the worker runs `python -m callsight backup` (queue `maint`):
  `BACKUP_HOST_DIR/db/callsight-YYYYMMDD-HHMMSS.dump` (+ `.json` with the row counts) and copies of new recordings
  and reports in `BACKUP_HOST_DIR/files/` with `files/manifest.sha256`. 14 daily + 8 weekly dumps are kept.
- By hand, and **before every migration / update**: `docker compose run --rm worker python -m callsight backup`
- Restore drill (safe, uses the scratch DB `callsight_restore_check`, deleted afterwards):
  `docker compose run --rm worker python -m callsight backup verify`
- ⚠ The backup is **not encrypted** and, by default, on the **same disk** as the audio: it protects against mistakes,
  not against disk loss or theft. Point `BACKUP_HOST_DIR` to an encrypted second disk / NAS, then set
  `BACKUP_OFFSITE_CONFIRMED=true` (the health page warning goes away).
- Retention: deleted calls stay inside old DB dumps until rotation removes them (about 8 weeks).

## 3. Restore on a new machine (or after losing the database)

1. Install Docker; copy the project folder, your saved `.env` and `config/` to the new machine.
2. Copy the backup folder to the new machine. Check the files:
   `cd <backup>/files && sha256sum -c manifest.sha256 --quiet` (Linux / Git Bash).
3. Copy `<backup>/files/audio/*` into `AUDIO_HOST_DIR` and `<backup>/files/reports/*` into `REPORTS_HOST_DIR`.
4. Set `BACKUP_HOST_DIR` in `.env` to the backup folder, then start only the database:
   `docker compose up -d postgres redis`
5. Restore the newest dump into the (empty) database:
   ```
   docker compose run --rm --no-deps -e PGPASSWORD=<POSTGRES_PASSWORD> worker \
     pg_restore --no-owner --no-acl --exit-on-error -h postgres -U callsight -d callsight \
     /backup/db/callsight-YYYYMMDD-HHMMSS.dump
   ```
6. `docker compose up -d --build` — the worker runs `alembic upgrade head` on start.
7. Check: log in, open "حالة النظام" (health page, admin), run `backup verify` once.

## 4. More capacity

- **More ingest/report work:** start a second worker: `docker compose up -d --scale worker=2` (each runs
  `-Q ingest,reports,maint`). Keep `maint` on one worker only if backups get slow.
- **Speech-to-text (asr queue):** one GPU worker per GPU; `ASR_MODEL` must match the compose profile; one model per
  GPU (8 GB cards: concurrency 1).
- **Second GPU box:** run only the asr worker there with the same image, `REDIS_URL` / `DATABASE_URL` pointing to the
  main server (private LAN only; open ports 6379/5432 to that one IP), audio mounted read-only from the main server
  (NFS/SMB). Nothing else changes: tasks are picked up from the shared Redis queue.
- **Server sizing:** see `product/tech-stack.md` (Server row). The health page shows queue lengths; a queue that
  keeps growing = add a worker for it.

## 5. New agents, teams and accounts

1. Add the extension (and queue) in `config/teams.yaml`; it is re-read automatically.
2. `docker compose run --rm worker python -m callsight backfill --days 7` to load its recent calls.
3. Web login for the agent (sees only their own answered/made calls and own KPIs):
   `docker compose run --rm worker python -m callsight create-user NAME --role agent --ext 104 --generate`
   Changing an agent's extension: `... set-ext NAME --ext 119` (ends their open logins).
   Known limit: an agent who only took the 2nd leg of a transferred call does not see that call.

## 6. PBX certificate (instead of `YEASTAR_VERIFY_SSL=false`)

On the office LAN: `docker compose run --rm worker python -m callsight pbx-cert --fetch` prints the SHA-256
fingerprint of the certificate the PBX presents. Compare it with the PBX web interface. Only when equal:
`... pbx-cert --accept <fingerprint>` saves `config/pbx-ca.pem`; then set `YEASTAR_CA_FILE=/app/config/pbx-ca.pem`
in `.env` (and `YEASTAR_TLS_CHECK_HOSTNAME=false` if the certificate does not name the PBX's IP) and restart.

## 7. Updating Python packages

Edit `requirements.in`, then re-lock with hashes inside Docker (never on the PC):
`docker run --rm --platform linux/amd64 -v "$(pwd -W):/src" -w /src python:3.12-slim sh tools/lock-deps.sh`
Rebuild with `--build-arg INSTALL_DEV=true` into a test tag, run the full test suite in it, then rebuild normally.
