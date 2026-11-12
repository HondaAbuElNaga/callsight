# Tasks: Call ingest

- [x] 1. User approves the spec and answers the open questions (team extensions, git)
         → approved 2026-11-12; team = 104, 119, 116 (+ queue 6401); save team calls only; local git
- [ ] 2. Tech stack: add SQLAlchemy 2, Alembic, psycopg 3, PyYAML; `git init` + branch `spec/2026-11-12-call-ingest`
- [ ] 3. Repo skeleton: `callsight/` package, `config.py` (.env), `requirements.txt`, `Dockerfile`, `docker-compose.yml`
         (postgres, redis, worker, beat), `.env.example` new keys
- [ ] 4. Save masked real call shapes as test fixtures (`tests/fixtures/cdr_shapes.json`)
- [ ] 5. Write tests for classification + team filter (every shape + status rule, wait/talk/ivr seconds, missed_agent, late leg)
- [ ] 6. Implement `ingest/classify.py` until the tests pass
- [ ] 7. Write tests for `YeastarAdapter` (paging since id, token refresh, 3-failed-login stop, recording download) with mocked HTTP
- [ ] 8. Implement `pbx/base.py` + `pbx/yeastar.py`; probe imports the shared client
- [ ] 9. DB models + first Alembic migration; tests for upsert/idempotency (legs → calls) against a test Postgres
- [ ] 10. Jobs + CLI: `ingest`, `backfill`, `sync-agents`, `stats`; `config/teams.yaml`
- [ ] 11. Recording job (download, WAV check, retries) + tests
- [ ] 12. Reconcile job + CLI `reconcile --date` + tests
- [ ] 13. Celery beat schedule (ingest 2 min, recordings 5 min, reconcile 02:00, sync-agents 03:00); run the stack
- [ ] 14. Backfill 7 days on this PC; hand-check 20 calls; 1 real test call each: answered, abandoned in queue, hung up in IVR
- [ ] 15. Verify the acceptance criteria
- [ ] 16. Review with 3 subagents (bugs, security, accuracy); fix confirmed findings
- [ ] 17. Spec status → done; tick roadmap items; update "Current position"; update docs/pbx-discovery.md (CDR leg shapes)
