# Tasks: ERP API

- [ ] 1. Approval: the user approves the spec and answers the open questions (or "approve" = recommendations).
- [ ] 2. Branch `spec/2026-11-18-erp-api` from an up-to-date `main` (after cleanup is pushed).
- [ ] 3. Tests first: key create/hash/revoke, 401 cases, cookie-vs-key separation, 405 on writes.
- [ ] 4. Migration 0022 (`api_key`, `api_request_log`) + models; `call.updated_at` bump from analysis/flag/follow-up writers.
- [ ] 5. Key functions in `callsight/api/apikeys.py`; CLI `api-key create|list|revoke`; admin log entries.
- [ ] 6. Users page section «مفاتيح الأنظمة الأخرى (API)» (admin only): create (show once + copy), list, revoke.
- [ ] 7. Tests first, then `callsight/api/ext.py`: `/agents`, `/calls` (cursor + `changed_since`), `/calls/{id}`.
- [ ] 8. `/forwards`, `/kpi` (same code as `/api/kpi`), `/calls/{id}/transcript` (per open question 3).
- [ ] 9. Rate limit (Redis) + request log; optional IP allowlist (open question 2).
- [ ] 10. Leak tests: no private notes / password hashes / admin log / deleted content in any ext response.
- [ ] 11. `docs/erp-api.md` for the ERP developer.
- [ ] 12. Verify: full test suite; tick each acceptance criterion.
- [ ] 13. 3-agent review (bugs, security, accuracy); fix confirmed critical/high.
- [ ] 14. On "ok push": push → live; admin makes the "نظام ERP" key; curl check; revoke check; give the key to the ERP side.
- [ ] 15. Spec → done; roadmap + finish plan updated.
