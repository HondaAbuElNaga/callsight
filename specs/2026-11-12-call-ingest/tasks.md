# Tasks: Call ingest

- [x] 1. User approves the spec and answers the open questions (team extensions, git)
         → approved 2026-11-12; team = 104, 119, 116 (+ queue 6401); save team calls only; local git
- [x] 2. Tech stack: add SQLAlchemy 2, Alembic, psycopg 3, PyYAML; `git init` + branch `spec/2026-11-12-call-ingest`
         → tech-stack.md updated; local git, branch spec/2026-11-12-call-ingest
- [x] 3. Repo skeleton: `callsight/` package, `config.py` (.env), `requirements.txt`, `Dockerfile`, `docker-compose.yml`
         (postgres, redis, worker, beat), `.env.example` new keys
         → done; DB on 127.0.0.1:5433 (not 'localhost': IPv6 hang on Windows); Postgres data in a Docker volume, audio in D:/callsight-data/audio
- [x] 4. Save masked real call shapes as test fixtures (`tests/fixtures/cdr_shapes.json`)
         → 49 real calls (1–4 per shape, team + non-team), customer numbers replaced by fake ones
- [x] 5. Write tests for classification + team filter (every shape + status rule, wait/talk/ivr seconds, missed_agent, late leg)
         → 35 tests (18 shapes, timings, transfer, late leg, *51 dial code, team filter)
- [x] 6. Implement `ingest/classify.py` until the tests pass
         → all classification tests pass
- [x] 7. Write tests for `YeastarAdapter` (paging since id, token refresh, 3-failed-login stop, recording download) with mocked HTTP
         → 18 tests incl. Yeastar 8-token limit (errcode 60002, found live) not counted as bad password
- [x] 8. Implement `pbx/base.py` + `pbx/yeastar.py`
         → done; tools/pbx_probe.py kept standalone (discovery tool with its own tests), not refactored
- [x] 9. DB models + first Alembic migration; tests for upsert/idempotency (legs → calls) against a test Postgres
         → migration 0001; DB tests run against callsight_test (skip if DB is down)
- [x] 10. Jobs + CLI: `ingest`, `backfill`, `sync-agents`, `stats`; `config/teams.yaml`
         → CLI: ingest, backfill, recordings, reconcile, sync-agents, stats
- [x] 11. Recording job (download, WAV check, retries) + tests
         → WAV check, 3 tries then 'missing', unsafe file names rejected
- [x] 12. Reconcile job + CLI `reconcile --date` + tests
         → 79 tests total, all pass
- [x] 13. Celery beat schedule (ingest 2 min, recordings 5 min, reconcile 02:00, sync-agents 03:00); run the stack
         → 4 containers up; first scheduled ingest succeeded in 1 s
- [x] 14. Backfill 7 days on this PC; hand-check 20 calls; 1 real test call each: answered, abandoned in queue
         → backfill: 894 team calls / 1,636 legs, re-run adds 0; 408/408 recordings OK (695 MB); reconcile 2026-11-11: 181/181 calls, 0 missing → OK.
           Real test calls by user 2026-11-12: 19:19:59 answered by 119 (talk 76 s, recording ok, in DB < 2 min) ✅;
           19:23:43 hung up in queue 6401 after 10 s → abandoned_queue ✅. 19:30:02 queue no answer 39 s → abandoned_queue ✅. Hand-check: user spot-checked 2/20, accepted
- [x] 15. Verify the acceptance criteria
         → all 8 ticked in spec.md (86 tests, reconcile 2026-11-11 OK)
- [x] 16. Review with 3 subagents (bugs, security, accuracy); fix confirmed findings
         → 3 reviewers: 13 findings, all confirmed, 0 critical/high. Fixed 11 (page cap on long backfill, disk errors,
           recordings right after ingest, reconcile expects recordings for answered customer calls, PBX-down doesn't use up
           retries, per-call lock, employee names removed from fixtures, doc fixes). 2 deferred to Phase 6 by user (PBX
           certificate trust, pinned versions). 86 tests pass. Note: old fixture with names remains in local git history (not pushed).
- [x] 17. Spec status → done; tick roadmap items; update "Current position"; update docs/pbx-discovery.md (CDR leg shapes)
         → done 2026-11-12; merged into local main (no remote)
