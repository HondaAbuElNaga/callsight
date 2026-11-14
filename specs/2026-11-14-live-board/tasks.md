# Tasks: Live call board (+ Listen switched off)

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–10 in spec.md)
- [ ] 2. Tests first (synthetic fixtures, fake WebSocket, fake Redis/PBX): parser, state transitions, pruning/caps,
         back-off + jitter, login rate limit, `pbx:auth_blocked` stops ingest/CLI/live logins, masking before publish,
         API WebSocket auth + Origin allowlist + seq/gap, Listen 403 + both gates + audit rows, default compose = no live
- [ ] 3. `pbx:auth_blocked` in the Yeastar adapter login path (+ `pbx-unblock` CLI)
- [ ] 4. `callsight/live/` parser + state + service (own token, heartbeat 30 s, reconnect ~25 min, del_token)
- [ ] 5. Compose `live` service (profile live, exits unless LIVE_ENABLED, restart on-failure capped)
- [ ] 6. API WebSocket `/api/live` + live page (cards, timers, "no live data"/"جاري المزامنة", monitoring notice)
- [ ] 7. Listen stub + migration `listen_audit` (additive) [should]
- [ ] 8. ⚠ NEEDS USER (office LAN): Call Status Monitor per trunk, `LIVE_ENABLED=true`, test call, capture payloads;
         legal steps before Listen
- [ ] 9. Verify; review with 3 subagents; fix; spec → done/[~]; roadmap; merge (--no-ff)
