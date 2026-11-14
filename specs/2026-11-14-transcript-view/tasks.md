# Tasks: Transcript view, export and search

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–9 in spec.md)
- [x] 2. Tests first: transcript API (auth, 404 vs status, displayed-transcript choice, audit dedupe, no-store),
         .txt (BOM, CRLF, RLM, header, mask, dropped windows, filename), search (normalised AND, ى fold, escaping,
         422 rules, EXISTS no double count, summary = list, combined filters, dropped text not searchable, no search
         words in logs), pg_trgm Arabic + EXPLAIN index use, migration backfill, agent-role stub
         → `tests/test_transcript_view.py`, 16 tests, all pass on `callsight_tv_test`. pg_trgm: `show_trgm('الفاتوره')`
         gives 9 trigrams (Arabic letters are word characters; multibyte trigrams are shown hashed); the index is
         **usable** (EXPLAIN of a plain `LIKE` with seqscan off shows `ix_transcript_search_trgm`). The real search
         statement (correlated EXISTS on the displayed transcript) was EXPLAINed in review: on small tables the
         planner goes call-first by date and does not pick the trigram index — fine at today's volume (date range
         ≤ 400 days); re-check on the 60k-call load test. `test_migration_0005_up_and_down` adjusted (model now has `search_text` from 0007).
- [x] 3. `search_normalize()` + migration 0006 + model fields + `transcripts reindex` CLI
         → `callsight/transcripts.py` (normalize.py unchanged). Migration is **0007** (orchestrator: a parallel
         hardening branch owns 0006; `down_revision = "0005"` now, becomes "0006" at merge). Additive: `search_text`,
         `CREATE EXTENSION IF NOT EXISTS pg_trgm`, GIN trigram index (built after the batched backfill),
         `transcript_access`. Model registers a `before_create` DDL so `create_all` makes pg_trgm first. Worker `_save`
         writes `search_text`. `python -m callsight transcripts reindex`.
         ⚠ NEEDS USER (deploy): `pg_dump` the live DB, then `alembic upgrade head` — live DB NOT migrated here.
- [x] 4. API: transcript, transcript.txt, search filter in CallFilter (+ badge flag in list rows)
         → `GET /api/calls/{id}/transcript`, `GET /api/calls/{id}/transcript.txt` (admin + supervisor via `Manager`;
         agent role 403). `words` query param on `/api/calls` + `/api/calls/summary` (checked by hand: a 422 never
         echoes the words; 403 for non-manager roles). Rows carry `transcript_match` (badge, no snippet).
         `ApiConfig.asr_model` from `ASR_MODEL`. Security middleware now keeps a route's own Cache-Control
         (`private, no-store` on .txt/PDF/recordings), else `no-store` as before.
- [x] 5. Frontend: TranscriptPanel (buttons, ltr times, highlight, aria-current, pause auto-scroll), player onTime,
         search box (debounced, instant, not in URL), badge, export link
         → `web/src/TranscriptPanel.tsx`, CallsPage (search box "كلمات في المكالمة", 0.4 s debounce, client-side
         rule check; badge "مطابقة في التفريغ"; player `seek` + `onTime`), api.ts, format.ts `mmss`, style.css.
         Built with `node:22-alpine` (`npm ci && npm run build`, tsc clean). HTTP smoke test against a local server
         on the test DB with a synthetic call: index 200, transcript JSON, .txt headers + BOM/RLM, search total 1,
         queued status, recording Range 206.
- [~] 6. Browser check on real transcribed calls (screenshots) — after the transcription spec ran on real calls
         ⚠ NEEDS USER: needs the live DB migrated (0007) and the api image rebuilt; the Chrome extension was not
         connected for a synthetic run either. Check: open a transcribed call → click a line (audio jumps), highlight
         follows playback, "تنزيل النص (.txt)" opens right in Notepad, search "الفاتورة" finds the expected call.
- [x] 7. Verify; review with 3 subagents; fix; spec → done; roadmap; merge (--no-ff)
         Verify done: full pytest 305 passed, 5 skipped (4 WeasyPrint on Windows, pre-existing). Review + merge: orchestrator.
         → 3 reviewers: 0 critical, 0 high, 1 medium, 8 low; all fixed: pg_trgm row in tech-stack (M); roadmap
           redaction later-item; EXPLAIN claim reworded (index usable; real EXISTS goes call-first on small tables);
           auto-scroll pauses on focus; invalid words box clears the applied search; stale seek cleared on play/close;
           words 422 message only when a words search is applied; reindex locks + re-checks each row (new test).
           After fixes: pytest 306 passed, 5 skipped; frontend `npm run build` OK. Merge: orchestrator.
         → merged after hardening: migration renumbered 0007 (down_revision 0006); agents get transcript + .txt of
           their own calls (404 others, audited), word search staff-only; 391 passed, 8 skipped. pg_dump
           `callsight-before-0007-20261114-1516.dump`; live DB at 0007, 5/5 real transcripts backfilled (search_text),
           4 match a greeting word in SQL. Browser check (click-to-seek, highlight, .txt in Notepad): ⚠ NEEDS USER.
