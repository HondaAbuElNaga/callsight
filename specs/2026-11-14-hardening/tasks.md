# Tasks: Hardening

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–10 in spec.md)
- [ ] 2. Tech stack (postgresql-client-16, pip-tools) before adopting
- [ ] 3. Tests first: agent scope on every route (route-walk test), 404 for others' calls, agent param override,
         backup rotation + manifest + tmp/rename, snapshot counts, verify refuses live DB, retention selection (off,
         N ≥ 30, dry-run, DB paths only), health checks with fakes, CA-file/fingerprint client config
- [ ] 4. Agent role (`visible_scope`, ROLES, create-user --ext check, token_version bump) + migration 0008 (backup_run,
         job_run, retention audit; additive)
- [ ] 5. `backup` / `backup verify` (+ maint queue, 01:30 beat, lock) + compose mounts; run both on this PC
- [ ] 6. Health page (admin) + banner + asr GPU heartbeat hook
- [ ] 7. CA file + fingerprint pinning + `pbx-cert --fetch/--accept` (not run tonight)
- [ ] 8. Retention code (off, dry-run) [may be ~]
- [ ] 9. App lock file + `--require-hashes` (only if full suite passes in the rebuilt image) [may be ~]
- [ ] 10. `docs/scale-out.md` incl. restore procedure + "keep .env safe" [may be short]
- [ ] 11. ⚠ NEEDS USER: encrypted 2nd disk/NAS for BACKUP_DIR; create agent accounts; retention days; PBX fingerprint
- [ ] 12. Verify; review with 3 subagents; fix; spec → done/[~]; roadmap; merge (--no-ff)
