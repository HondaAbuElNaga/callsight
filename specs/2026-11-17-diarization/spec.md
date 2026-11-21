# Spec: Diarization — agent and customer separated

**Status:** approved (2026-11-18, user: "approve all" — every open question = the recommendation) · **Plan step:** 4 of [finish plan](../../product/finish-plan.md)
**Roadmap items:** Phase 2 goal "agent and customer separated"; Phase 3 "talk ratio + interruptions need
diarization", "customer-side text needs diarization".

## Goal
Recordings are mono (both voices mixed). Today a transcript is one block of text. This spec labels every
segment as **agent** or **customer**, so the transcript reads like a chat, the satisfaction model reads the
customer's words only (step 5), and talk ratio / interruptions can be measured.

## Scope
**In:** speaker split on the GPU server, agent/customer naming, transcript view with two colours, talk ratio and
interruption count per call, re-run on the 500 rating-set calls.
**Out:** more than 2 speakers per call (transfers keep 2 labels: agent side / customer side); live transcripts.

## Requirements
1. **Speaker split:** `pyannote/speaker-diarization-3.1` (MIT; tech stack row is already 🔜) runs on the GPU in
   the asr worker after Whisper, fixed to 2 speakers. The model is downloaded once while the GPU server has
   internet (build-models task), then internet is closed again as today.
2. **Who is the agent:** the speaker who talks first on an answered inbound call (the agent greets), checked by a
   greeting word list (`السلام عليكم`, `معك`, `المعهد السعودي`...) in the first 10 s; outgoing calls: the speaker
   who says the institute's name or a greeting word first. If unsure, the call is marked `speaker_unsure` and both
   are shown as "متحدث 1 / 2".
3. **Stored:** each transcript segment gets `speaker` (agent / customer / unknown); migration adds the column.
   The model name and version stored per transcript.
4. **Transcript view + .txt export:** agent lines and customer lines in two colours / prefixes.
5. **Metrics per call:** talk ratio (agent % of speech), interruptions (one starts within 0.3 s while the other is
   still speaking, > 1 s overlap of speech turns), longest customer silence. Shown on the call page; team averages
   on the KPI page (show-only, not judged).
6. **Re-run:** a task re-runs diarization on the 500 rating-set calls and on all calls since `ASR_START_DATE`
   (queue, GPU wake-ups as usual).
7. **GPU time:** measure the extra time per call; if it makes the 30-min wake-up miss its work, write it down.

## Acceptance criteria
- [ ] On the 30 gold-set clips the user marks who speaks first (a 30-row form); agent/customer naming right on
      ≥ 27 of 30
- [ ] pytest: naming rules, metrics math, export format, migration up/down
- [ ] Extra GPU time per call measured on the T4 and written in `docs/aws.md`
- [ ] Transcript page shows two colours on a real call (screenshot)

## Technical approach
pyannote needs a Hugging Face token once to download the gated model (the user creates a free account and accepts
the model terms, click by click). The token is used only during build-models, never stored on the server after.
Speaker turns are matched to Whisper segments by time overlap.

## Decisions taken while building (2026-11-19, 3 judges, 3/3)
- **Option A: same Whisper decode + word timings.** The adapter adds `return_token_timestamps=True` (alignment-head
  DTW after the same greedy decode): the text is byte-identical, so the WER benchmark stays valid. Rejected: B
  (transcribe each speaker turn: short clips hallucinate, WER must be re-run, more GPU) and C (timestamp-token
  decoding: different decode mode, phrases span speaker changes).
- Words kept per window (`transcript_segment.words`, JSONB `[[word, start, end], ...]`); overlap words deduped by
  time (a word belongs to the window whose display range holds its midpoint). `Transcript.text`, search and
  `merge_texts` unchanged. Dropped / silent windows give no words.
- pyannote 3.1 (`num_speakers=2`) runs after Whisper while the GPU lock is held. Raw turns stored in `speaker_turn`
  (source of talk ratio, interruptions, longest customer silence: pyannote turns, not word times).
- Each word goes to the speaker whose turn overlaps its midpoint (nearest turn if none). Same-speaker words in a
  row = one line in `transcript_turn` (speaker agent / customer / unknown, start, end, text).
- Agent naming per requirement 2; unsure → `transcript.speaker_unsure` and "متحدث 1 / 2".
- Old calls have no word timings: the re-run task decodes them once more with timings (text expected identical;
  a call whose text differs is logged and keeps its old text). After that, diarization alone can re-run.
- Migration **0024** (not 0009).

## Changes found while building (2026-11-19)
- **Word times without changing the text.** `generate(return_token_timestamps=True)` switches the decoder to
  "eager" attention; its fp16 rounding changed 3 words in 1 window of 8 test clips. So the code does the normal
  decode (same text as today), then ONE teacher-forced eager pass over that same token sequence for the
  cross-attentions, then transformers' own DTW (alignment heads, median filter), cropped to the real audio length.
  Checked on the PC GPU: 9/9 windows identical text, times within 0.02 s of transformers' method, **+33 % Whisper
  time** (pyannote comes on top; measure on the T4, R7).
- **Agent naming (R2) exactly as written:** inbound = the first speaker if they greet in the first 10 s, else
  unsure (no guess from the second speaker). Outbound / internal = the first to say the institute or a greeting.
  Risk: a customer who answers an outgoing call with "السلام عليكم" is named agent → the 30-row check includes
  outgoing calls.
- **Interruption (R5):** a turn that starts more than 0.3 s after the other speaker's turn began, while that turn is
  still going, and overlaps it by more than 1 s (back-channels "اها / نعم" under 1 s do not count).
- **"Longest customer silence" (R5)** = longest wait after the customer stopped before anyone spoke again
  (`longest_wait_s`).
- **Talk numbers** (ratio, interruptions, wait) are returned to admin / supervisor only (open question 2), on the
  call page and as show-only averages on the KPI page; never in the PDF or a score.
- **Re-run** = `transcribe --resplit [--limit N]` (rating set first, then newest since ASR_START_DATE, max 500 per
  run). It also picks calls that have word times but no speaker split (pyannote missing / failed). A window whose
  re-decoded text differs keeps its old text and gets no speaker (count logged, never text). A redo always clears
  the old split first. Re-run of the naming rules alone (CPU, from stored words + turns) is left for later.
- `DIARIZATION=off` (asr worker env) turns the split off; a missing model only logs a warning and transcripts are
  saved as before.

## Open questions
1. pyannote 3.1 (**recommended**: best open model, MIT, used by whisperX) vs a simple 2-voice clustering of our
   own (no token, weaker on overlapping speech)?
2. Agents see the talk-ratio number? **Recommended: no** (same rule as the score: show-only for staff until
   checked).

## First-speaker check page (2026-11-21, user: "ok make it")
- Gold clips are cut from the MIDDLE of each call, so the 30-row form plays the START of the full recording
  (`/api/calls/{id}/recording`, audited as usual). Page "من يتكلم أولاً" (admin only), migration **0025**
  (`gold_clip.first_speaker` agent / customer / skip, by, at).
- Answers: الموظف / العميل / لا أعرف. "لا أعرف" and calls without a recording are left out; target = 90 % of the
  answered rows rounded up (27 of 30). "unsure" (agent not named) counts as not right. No pass/fail while a call is
  not split yet. The system's answer appears only after every row is answered.
- System answer = the speaker of the first pyannote turn, mapped to agent / customer through the lines overlapping
  that speaker's turns (a dropped first "ألو" still counts for its speaker). `--resplit` takes the gold calls first.
