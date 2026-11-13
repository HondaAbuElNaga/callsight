# Spec: Web screen, call list (login, filters, play recording)

**Roadmap item:** Phase 1, "Basic web UI: login, call list with filters (date, agent, status), play recording"
**Status:** approved

## Goal
The supervisor (ext **114**) and the manager open a web page, log in, and see every team call with its status,
times and agent, filter it, and play the recording in the browser. This makes the ingested data usable day to day
(mission: "No visibility of call quality, missed calls or repeat callers") and is the base for the KPI dashboard (Phase 4).

## Scope
- In:
  - API (FastAPI) in a new `api` container: login/logout, call list with filters + paging, call detail (legs timeline),
    recording streaming (seek works), team agent list, summary counts for the current filter.
  - User accounts in the DB (created with a CLI command), passwords hashed, login cookie, lock after repeated wrong passwords.
  - Audit table: who played which recording and when (recordings are personal data, Saudi PDPL).
  - Web page (React + Vite + TypeScript), **Arabic, right-to-left**, times in Riyadh time:
    - Login page.
    - Calls page: filter bar (date from–to, agent, status, direction, number search), summary chips
      (total / answered / hung up in queue / no answer / busy / voicemail), table, play button with an inline audio player.
    - Call detail panel: path of the call (IVR → queue → agent), wait/talk times, recording.
  - Frontend is built inside Docker (no Node.js needed on this PC) and served by the API container.
- Out:
  - KPI dashboard, charts, PDF reports, CSV/Excel export (Phase 4).
  - Agent role that sees only own calls (Phase 6 "Roles & permissions"); now every account sees all team calls.
  - Live calls board and "Listen" button (Phase 5). Transcripts (Phase 2).
  - HTTPS certificate and access from other PCs (decision 2: this PC only).

## Requirements
1. **Accounts.** `python -m callsight create-user <username> --role admin|supervisor [--ext 114]` asks for the password
   twice (min 10 characters). Passwords stored as Argon2 hashes. `set-password` and `disable-user` commands too.
2. **Login.** `POST /api/login` sets an HttpOnly, SameSite=Strict cookie holding a signed token (8 h). After 5 wrong passwords
   for a username, that username is locked for 15 minutes. Every other `/api/*` endpoint (and recordings) returns 401 without a valid cookie.
3. **Call list.** `GET /api/calls` with filters `from`, `to` (Riyadh dates, inclusive), `agent` (ext; matches the answering
   or missed agent), `status`, `direction`, `q` (part of the customer number), paging (50 per page), newest first. Each row:
   time, direction, customer number, agent ext + name, status, IVR/wait/talk seconds, has-recording flag.
4. **Summary.** `GET /api/calls/summary` with the same filters returns counts per status. The page asks for it **without**
   the status filter, so every status box keeps its number when one status is chosen (user feedback 2026-11-14).
5. **Detail.** `GET /api/calls/{id}` adds the legs (kind, number, disposition, times, reason) in order.
6. **Recording.** `GET /api/calls/{id}/recording` streams the WAV with HTTP Range support (seeking in the player).
   The file path comes only from the DB (`recording_path`) and must resolve inside `AUDIO_DIR`. Each play writes one audit row.
7. **Agents.** `GET /api/agents` returns the team extensions with names (from the synced `agent` table) for the filter.
8. **Web page.** Arabic RTL; status names in Arabic (تم الرد، أغلق أثناء الانتظار في الطابور، لم يتم الرد، مشغول، بريد صوتي);
   durations as m:ss; default filter = today; filters apply as soon as they change (no "show" button; the number box
   applies 0.4 s after typing stops), user feedback 2026-11-14. Works in Chrome and Edge on a 1366×768 screen.
9. **Config.** New `.env` keys: `APP_SECRET` (random, signs the login token), `WEB_PORT`. No secret in code or the image.

## Acceptance criteria
- [x] `pytest` passes, including API tests: every filter, paging, summary = table counts, 401 without login, lockout after 5
      wrong passwords, recording Range request returns 206, path outside `AUDIO_DIR` refused, audit row written per play
      → 115 passed (29 API tests)
- [x] `docker compose up -d --build` starts the new `api` service; `http://<pc>:<WEB_PORT>` shows the Arabic login page
      → api healthy on 127.0.0.1:8080; not reachable via the PC's LAN address
- [x] Logged in as the supervisor: the calls list for 2026-11-05 … 2026-11-11 shows the same total and per-status counts as
      `python -m callsight stats` for those days → 767 calls; answered 352, abandoned_queue 233, no_answer 128, busy 52, voicemail 2 = DB
- [x] Filter by agent 119 + status "answered" for 2026-11-12 shows only 119's answered calls (count = DB query) → 28 = 28
- [x] The recording of the 2026-11-12 19:19:59 test call plays in the browser and can be dragged to the middle → Range 206; user tried the page
- [x] The call list API answers in < 500 ms for a 7-day filter (measured with 900+ calls) → ≈ 8 ms
- [x] User confirms the page is readable and useful (RTL, Arabic labels, times correct) → 2026-11-14 after 2 changes (instant filters, boxes keep counts)

## Technical approach
- `callsight/api/`: `main.py` (FastAPI app, mounts the built frontend at `/`), `auth.py` (Argon2 via `argon2-cffi`,
  signed token via `PyJWT`, cookie handling, lockout), `calls.py` (query building with SQLAlchemy; filters → one shared
  `where` used by list and summary), `recordings.py` (Starlette `FileResponse`, which supports Range).
- DB: migration 0002 adds `app_user` (username, password_hash, role, ext, active, failed_logins, locked_until) and
  `recording_play` (user_id, call_id, played_at).
- `web/`: Vite + React + TypeScript, no UI framework at first (plain CSS, RTL via `dir="rtl"`), Arabic font
  IBM Plex Sans Arabic (bundled locally, nothing loaded from the internet). Built in a Node stage of the Dockerfile.
- Compose: `api` service (uvicorn), published on `127.0.0.1:${WEB_PORT:-8080}` only (decision 2).
- Tests: API tests with FastAPI `TestClient` against the test DB (same pattern as `tests/test_service.py`).
- Risks: (a) recordings and phone numbers are personal data, so login is required for everything and every play is logged;
  (b) plain HTTP on the office network would expose passwords and audio to anyone on that network (see open question 1);
  (c) the PC must be on the office LAN for new calls; the page still works offline with the stored data.

## Open questions
1. **Who opens the page from where?** (a) only on this PC (`127.0.0.1`), or (b) from other office PCs, for example the
   supervisor's. Recommendation: **(a) this PC only for now**: no network exposure, no certificate needed. Move to (b) with
   HTTPS when the server arrives. If (b) is needed now, I add HTTPS with a local certificate (each browser shows a one-time warning).
2. **Accounts:** who gets one? Recommendation: `admin` (you) + `supervisor` (ext 114). Agents later (Phase 6).
3. **Customer numbers:** show the full number (needed to call back) or masked (e.g. 055****567)?
   Recommendation: full, because only admin/supervisor can log in.
4. **New tools:** `argon2-cffi` (password hashing), `PyJWT` (login token), `uvicorn` (web server), Node.js 22 (only
   inside the Docker build). Recommendation: approve; all standard with FastAPI/React (already in the tech stack).

## Decisions
1. Approved by user 2026-11-13.
2. **This PC only**: the web page is published on `127.0.0.1` (no office-network access, plain HTTP is fine). Other PCs + HTTPS when the server arrives.
3. Accounts: `admin` (user) + `supervisor` (ext 114). Both see all team calls.
4. Customer numbers shown in full.
5. New tools approved: argon2-cffi, PyJWT, uvicorn, Node.js 22 (Docker build stage only).
