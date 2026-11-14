# Tasks: Transcript view, export and search

- [x] 1. Spec approved (2 judge agents on the user's behalf, 2026-11-14; decisions 1–9 in spec.md)
- [ ] 2. Tests first: transcript API (auth, 404 vs status, displayed-transcript choice, audit dedupe, no-store),
         .txt (BOM, CRLF, RLM, header, mask, dropped windows, filename), search (normalised AND, ى fold, escaping,
         422 rules, EXISTS no double count, summary = list, combined filters, dropped text not searchable, no search
         words in logs), pg_trgm Arabic + EXPLAIN index use, migration backfill, agent-role stub
- [ ] 3. `search_normalize()` + migration 0006 + model fields + `transcripts reindex` CLI
- [ ] 4. API: transcript, transcript.txt, search filter in CallFilter (+ badge flag in list rows)
- [ ] 5. Frontend: TranscriptPanel (buttons, ltr times, highlight, aria-current, pause auto-scroll), player onTime,
         search box (debounced, instant, not in URL), badge, export link
- [ ] 6. Browser check on real transcribed calls (screenshots) — after the transcription spec ran on real calls
- [ ] 7. Verify; review with 3 subagents; fix; spec → done; roadmap; merge (--no-ff)
