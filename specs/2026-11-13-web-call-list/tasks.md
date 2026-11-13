# Tasks: Web screen, call list

- [x] 1. User approves the spec and answers the open questions (access, accounts, number masking)
         → approved 2026-11-13: this PC only, admin + supervisor 114, full numbers
- [x] 2. Tech stack: add argon2-cffi, PyJWT, uvicorn, Node 22 (build only); branch `spec/2026-11-13-web-call-list`
         → tech-stack.md updated; branch created
- [x] 3. Migration 0002: `app_user`, `recording_play`; CLI `create-user`, `set-password`, `disable-user`
         → migration 0002; CLI create-user/set-password/disable-user (--generate prints a random password; plain stdin check hangs on Windows)
- [x] 4. Write API tests: auth (login, cookie, 401, lockout), filters, paging, summary = list, detail, recording Range + path check + audit
         → 29 API tests
- [x] 5. Implement `callsight/api/` until the tests pass
         → 115 tests pass
- [x] 6. Frontend: Vite + React + TS skeleton, RTL layout, Arabic font bundled, login page
         → Vite 5 + React 18 + TS, IBM Plex Sans Arabic bundled via @fontsource (no internet at runtime)
- [x] 7. Calls page: filter bar, summary chips, table, paging, inline audio player, detail panel
         → done; clicking a summary chip filters by that status
- [x] 8. Dockerfile Node build stage + `api` service in compose; `.env.example` keys `APP_SECRET`, `WEB_PORT`
         → multi-stage Dockerfile; api on 127.0.0.1:8080 only (not reachable from the LAN address, checked)
- [x] 9. Create the 2 accounts; check the 7-day counts against `stats`; time the 7-day API call
         → accounts admin + supervisor (114); 11-05..11-11: API 767 calls, per-status = DB; 119 answered 11-12: 28 = DB; 7-day list ≈ 8 ms; recording Range → 206
- [x] 10. User tries the page (filters, play the 19:19:59 test call) and gives feedback
         → feedback: no "عرض" button (filters apply instantly); status boxes must keep all counts → both done; user: "its good now"
- [x] 11. Verify the acceptance criteria
         → all 7 ticked in spec.md
- [ ] 12. Review with 3 subagents (bugs, security, accuracy); fix confirmed findings
- [ ] 13. Spec status → done; tick roadmap item; update "Current position"; merge to main
