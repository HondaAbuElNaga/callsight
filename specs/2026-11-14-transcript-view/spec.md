# Spec: Transcript view, export and search

**Roadmap item:** Phase 2, "Transcript view next to the audio player; export transcript as .txt"
**Status:** approved (by 2 judge agents on the user's behalf, 2026-11-14 — user may revise)

## Goal
A supervisor opens a call and reads what was said while listening, jumps in the audio by clicking a line, exports
the text, and finds calls by words that were said (mission: "Can't search or read what was said" → "Full transcript
per call").

## Facts
- Spec `2026-11-14-transcription` stores `transcript` (one per call + model; status, full text, flags) and
  `transcript_segment` (start/end s, `speaker` NULL until diarization, text, `flag` for dropped windows).
- Call list page `web/src/CallsPage.tsx` has a detail panel + audio player (recordings audited in `recording_play`).
- Users today: admin + supervisor (both see all team calls). Agent role comes in Phase 6.
- Postgres 16 has no Arabic stemmer; Arabic spelling varies (أ/ا, ة/ه, ى/ي, harakat).

## Scope
- In:
  - API `GET /api/calls/{id}/transcript` → status + segments (+ model, created time); 404 when none.
    Opening a transcript is **audited** like a recording play (transcripts are personal data).
  - Detail panel: transcript block next to the player; each segment shows its time (mm:ss) and text, RTL;
    clicking a segment seeks the player there; the segment being played is highlighted; dropped windows shown as
    "— (صمت/غير واضح)"; status messages for queued / running / error / none.
  - Export: `GET /api/calls/{id}/transcript.txt` → UTF-8 text file with a header (call date Riyadh time, agent ext,
    direction, model, "تفريغ آلي — قد يحتوي أخطاء"), one line per segment `[mm:ss] text`. Customer number masked
    (last 4 digits). Download audited.
  - Search: call list gets a "كلمات في المكالمة" text filter (applies instantly with a short debounce, like the
    other filters) → calls whose transcript contains all typed words, matched on **normalised** Arabic (same
    `normalize()` as the benchmark: harakat removed, أإآ→ا, ة→ه, ى→ي, digits), using a stored normalised text column
    + `pg_trgm` GIN index; results show a short snippet around the first hit.
- Out:
  - Editing transcripts; speaker labels (diarization later); PDF of a transcript; agent role restrictions (Phase 6).

## Requirements
1. Transcript API + .txt + search require login (admin/supervisor); every transcript view and download writes an
   audit row (user, call, kind = view|export, time).
2. Normalised search text is written when a transcript is stored (and backfilled for existing rows by the migration).
3. Search with 1–5 words, each ≥ 2 characters, max 100 characters; combined with all existing filters; the summary
   counts agree with the list (existing invariant).
4. Clicking a segment seeks the audio to its start; the active segment follows playback.
5. .txt export opens correctly in Windows Notepad (UTF-8 with BOM, CRLF line ends).

## Acceptance criteria
- [ ] pytest: API auth (401 without login), 404 without transcript, audit rows for view + export, .txt format
      (BOM, CRLF, masked number, header), search (normalised matching: "مسؤول" finds "مسئول"/"مسوول", "الفاتورة" finds
      "الفاتوره", multiple words AND, combined with date/agent filters, summary = list count), migration backfill
- [ ] Browser check on real transcribed calls (after the transcription spec ran on 5 calls): click-to-seek works,
      highlight follows, export downloads (screenshot)
- [ ] Search on real transcripts returns the expected call (screenshot)

## Technical approach
- Migration **0006**: `transcript.search_text` (normalised), `pg_trgm` extension + GIN trigram index,
  `transcript_access` audit table. `CREATE EXTENSION pg_trgm` needs DB owner rights (our `callsight` user owns the DB).
- Search query: `search_text LIKE '%w1%' AND … LIKE '%wN%'` (trigram index makes it fast), joined to the call filter.
- Frontend: new `TranscriptPanel.tsx` inside the detail panel; the player gets an `onTime` callback.

## Open questions (decided by 2 judges on the user's behalf)
1. **Search method:** normalised text + pg_trgm LIKE (recommended: robust to Arabic spelling, no stemmer needed)
   vs Postgres full-text `simple` config (word-exact, misses prefixes like "والفاتوره").
2. **Audit transcript views** (recommended: yes, same as recordings) vs only exports.
3. **Customer number in .txt:** masked to last 4 digits (recommended) vs full number.
4. **Who sees transcripts now:** admin + supervisor (recommended, same as recordings) — agent role later.

## Decisions (2 judges, 2026-11-14; override the text above where different)
1. **Search:** normalised text + `pg_trgm` LIKE via a new `search_normalize()` = benchmark `normalize()` **plus
   ى→ي**; `callsight/asr/normalize.py` stays unchanged (WER comparability). Known limit: ؤ→و but ئ→ي, so
   "مسئول" ≠ "مسؤول"; the test uses "مسؤول" finds "مسوول". Query = one `EXISTS` (displayed transcript of the call,
   status done, all words LIKE) inside `CallFilter.apply` → a call is never counted twice, summary = list. Escape
   `% _ \` (`autoescape=True`). After normalising: 1–5 words, each ≥ 2 characters, only letters/digits/spaces,
   else 422. 2-character words scan within the date range (bounded by `MAX_RANGE_DAYS`) — fine at < 200 calls/day.
   Dropped/hallucination-flagged windows are kept out of `search_text`. Test proves `show_trgm` handles Arabic and
   `EXPLAIN` uses the index for words ≥ 3 characters. The test fixture creates the extension before the index.
2. **Displayed transcript** when several exist: the `done` one for the current `ASR_MODEL`, else the newest `done`.
   `GET` returns 200 with `status` for queued/running/error/skipped; 404 only when no row exists.
3. **Audit:** `transcript_access(kind=view|export)`; views deduplicated with the existing `AUDIT_WINDOW` like
   `audit_play`; only when a `done` transcript is opened (status polling writes nothing); transcript loads only when
   the detail panel opens. No transcript text in logs or the audit table.
4. **No snippets** in list/search responses (they would show text without an audit row): only a badge
   "مطابقة في التفريغ". Search words never go into the page URL, app logs or exception texts (test checks logs).
5. **Export:** filename `callsight-call-{id}-{YYYYMMDD}.txt` (ASCII, no phone number), `Content-Disposition:
   attachment`, `Cache-Control: private, no-store` (tested). UTF-8 with BOM, CRLF, every line starts with U+200F (RLM)
   so `[mm:ss]` stays in place in Notepad. Header: date, agent ext + name, direction, model, masked customer number
   (last 4), disclaimer "تفريغ آلي — قد يحتوي أخطاء؛ الأرقام المنطوقة في المكالمة غير مخفية". Dropped windows:
   `[mm:ss] — (صمت/غير واضح)`, never the dropped text.
6. **UI:** segments are `<button>`s (Enter/Space seeks), `mm:ss` in `<span dir="ltr">` with Western digits, active
   segment has `aria-current`, auto-scroll pauses while the user scrolls/focuses the list; note in the UI that a line
   covers up to 30 s (no word-level seeking). De-overlapped display times from the transcription spec.
7. **Access:** admin + supervisor via the same role helper as recordings; a test stub asserts a future `agent` role
   is denied for now. `python -m callsight transcripts reindex` rebuilds `search_text` when normalisation changes.
8. **Migration 0006** (down_revision 0005): `search_text`, `pg_trgm` + GIN index, `transcript_access`; batched
   Python backfill; `pg_dump` first. `search_text` lives on the transcript row (CASCADE) → S7 retention/backup.
9. Roadmap note: redaction of spoken ID/card numbers = later idea.
