# Tasks: Web screen, call list

- [x] 1. User approves the spec and answers the open questions (access, accounts, number masking)
         → approved 2026-11-13: this PC only, admin + supervisor 114, full numbers
- [ ] 2. Tech stack: add argon2-cffi, PyJWT, uvicorn, Node 22 (build only); branch `spec/2026-11-13-web-call-list`
- [ ] 3. Migration 0002: `app_user`, `recording_play`; CLI `create-user`, `set-password`, `disable-user`
- [ ] 4. Write API tests: auth (login, cookie, 401, lockout), filters, paging, summary = list, detail, recording Range + path check + audit
- [ ] 5. Implement `callsight/api/` until the tests pass
- [ ] 6. Frontend: Vite + React + TS skeleton, RTL layout, Arabic font bundled, login page
- [ ] 7. Calls page: filter bar, summary chips, table, paging, inline audio player, detail panel
- [ ] 8. Dockerfile Node build stage + `api` service in compose; `.env.example` keys `APP_SECRET`, `WEB_PORT`
- [ ] 9. Create the 2 accounts; check the 7-day counts against `stats`; time the 7-day API call
- [ ] 10. User tries the page (filters, play the 19:19:59 test call) and gives feedback
- [ ] 11. Verify the acceptance criteria
- [ ] 12. Review with 3 subagents (bugs, security, accuracy); fix confirmed findings
- [ ] 13. Spec status → done; tick roadmap item; update "Current position"; merge to main
