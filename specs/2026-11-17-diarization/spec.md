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

## Open questions
1. pyannote 3.1 (**recommended**: best open model, MIT, used by whisperX) vs a simple 2-voice clustering of our
   own (no token, weaker on overlapping speech)?
2. Agents see the talk-ratio number? **Recommended: no** (same rule as the score: show-only for staff until
   checked).
