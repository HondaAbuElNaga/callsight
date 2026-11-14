# Tasks: Live call board (+ Listen switched off)

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–10 in spec.md)
- [x] 2. Tests first (synthetic fixtures, fake WebSocket, fake Redis/PBX): parser, state transitions, pruning/caps,
         back-off + jitter, login rate limit, `pbx:auth_blocked` stops ingest/CLI/live logins, masking before publish,
         API WebSocket auth + Origin allowlist + seq/gap, Listen 403 + both gates + audit rows, default compose = no live
         → `tests/test_live_board.py` (44) + `tests/test_live_api.py` (22) + route walk in `tests/test_agent_role.py`
         (WS routes get close codes 4401/4403 right after accept). Fixtures `tests/fixtures/live_events_synthetic.json`, labelled
         SYNTHETIC (from the Yeastar docs; no real 30011 payload yet). Fakes in `tests/live_fakes.py` (no PBX contact).
- [x] 3. `pbx:auth_blocked` in the Yeastar adapter login path (+ `pbx-unblock` CLI)
         → `callsight/pbx/authblock.py`; adapter checks it before every login, sets it on a refused login and on
         errcode 60002 (first reason kept). Wired into worker/CLI via `app.pbx()` (Redis down = warning + the old
         in-process limit, since Redis is not published to the PC) and into live (Redis down = blocked).
         `python -m callsight pbx-unblock` prints who blocked and why.
- [x] 4. `callsight/live/` parser + state + service (own token, heartbeat 30 s, reconnect ~25 min, del_token)
         → `events.py` (30011/30012, msg as JSON string or object, both member shapes, caller masked to ••••1234 at
         parse time), `state.py` (waiting/ringing/talking/holding, transfer, 4 h prune, 500-call cap, reset on
         reconnect), `bus.py` (snapshot key TTL 90 s + pub/sub, seq grows across restarts), `service.py` (own login,
         saved token reused < 25 min (0600 file on the live-only volume `live-state`, not Redis), 1 login / 10 min
         via Redis SET NX, at 25 min refresh_token (if refused: one gated login, max 1 per 10 min) + reconnect, heartbeat
         30 s / dead after 75 s silence, back-off 5 s → 5 min ±20 %, failed login never retried, del_token on stop;
         httpx/websockets loggers at WARNING; only exception class names logged).
- [x] 5. Compose `live` service (profile live, exits unless LIVE_ENABLED, restart on-failure capped)
         → `restart: on-failure:5`. `docker compose config --services` = postgres, redis, worker, api, beat (no live);
         with `--profile live` live is added. Stack not started.
- [x] 6. API WebSocket `/api/live` + live page (cards, timers, "no live data"/"جاري المزامنة", monitoring notice)
         → `callsight/api/live.py`: Origin allowlist (LIVE_ORIGINS, default 127.0.0.1/localhost:WEB_PORT) checked
         before accept; right after accept cookie login (4401), staff only (4403), 3 per user / 20 total (1013)
         (a close before accept reaches browsers only as 1006; the page also checks /api/me after 1006), re-checked every
         30 s (logout / disable / 8 h close it), bounded queue 64 (slow client dropped), one Redis subscriber per
         process. `web/src/LivePage.tsx` (link "المكالمات الحالية" for admin/supervisor): cards with agent, masked
         caller, direction, state, live timer (server clock), queue; waiting list; reconnect 1 → 30 s + jitter;
         gap → asks for "snapshot". `tsc --noEmit` in a throwaway node:22 container: only the old echarts
         "module not found" errors of Chart.tsx (the PC's node_modules has no echarts), none in the new files;
         the real build runs in the Docker image build.
- [x] 7. Listen stub + migration `listen_audit` (additive) [should]
         → `POST /api/live/{call_id}/listen`: 403 "feature off" unless LISTEN_ENABLED (read per request); adapter
         `listen()` refuses too (2nd gate). Every attempt audited (denied_role / denied_off / denied_no_ext /
         not_found / pending + result row). Migration 0009 (down_revision 0007 for now) + DB trigger: UPDATE/DELETE
         refused. Up/down/up checked on a scratch DB. No Listen button markup in web/ (test checks it).
- [ ] 8. ⚠ NEEDS USER (office LAN): Call Status Monitor per trunk, `LIVE_ENABLED=true`, test call, capture payloads;
         legal steps before Listen
- [x] 9. Verify; review with 3 subagents; fix; spec → done/[~]; roadmap; merge (--no-ff)
         (verify done: full pytest 457 passed, 8 skipped, 0 failed on callsight_lb_test; review + merge not done
         in this run)
         → 3 reviewers: 0 critical, 0 high, 3 medium, 13 low; all fixed: WS login/role/cap closes after accept +
           page checks /api/me on 1006; SIGTERM interrupts recv() (del_token runs) + stop_grace_period 30s; Listen
           POST needs the page Origin (denied_origin audited); queue number never truncated from a long digit run;
           TRUNCATE trigger on listen_audit (non-empty table); Listen denials max 10 audited rows / user / min
           (then 429); live_lookup error audited (503); PBX token in a 0600 file on volume `live-state` (not Redis);
           live service without env_file / APP_SECRET / DB password; page ignores stale deltas + one snapshot
           request at a time; waits re-publish every 30 s; old pubsub closed before resubscribe; refused login
           re-sets pbx:auth_blocked if Redis missed it; docs (refresh fallback, tech-stack rows, client_ip note).

Cut line (decision 10): Must = all done. Should (Listen stub + audit) = done. Cut first: agent-status row (30007/
30008/30009/30029) = CUT; queue waiting list = best effort only (from `call_path`, plus calls with no extension
member yet); PBX wiring of Listen = CUT (adapter refuses).
         → merged to main before call analysis (0009 after 0007); 467 passed, 8 skipped. pg_dump
           `callsight-before-0009-20261114-1858.dump`; live DB at 0009; default services = postgres redis beat worker api
           (no live); POST /api/live/x/listen without login = 401. No PBX contact.
