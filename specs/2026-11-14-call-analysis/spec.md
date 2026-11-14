# Spec: Call analysis — LLM summary + first satisfaction estimate, flags, silence metrics, rating page

**Roadmap items:** Phase 3: "Labelling tool", "LLM call summary", "Bad words / escalation flags", "Silence,
interruption & talk-ratio metrics", "`nlp` worker", and the training code for "Fine-tune MARBERT / CAMeLBERT-DA"
(the training *run* and "Label ≥ 500 calls" need the user's labels).
**Status:** draft

## Goal
Every transcribed call gets a short Arabic summary, a topic, action items, warning flags and a **first
(unvalidated) satisfaction estimate 1–10** from a local LLM; supervisors rate calls on a page so that, once ≥ 500
labels exist, the tuned model can be trained and checked against the Phase 3 "Done when" (±2 on ≥ 80 % of a
100-call human-rated test set).

## Facts / constraints (checked 2026-11-14)
- Judges' decision R4: Ollama, model in config, default `qwen2.5:7b` (Apache-2.0, ~4.7 GB Q4); quick Arabic test vs
  `qwen3:8b`, keep the better; LLM score labelled "تقدير أولي", **kept out of KPI avg satisfaction and PDFs**;
  blind 100-call test set without pre-fill; Ollama on the internal Docker network only; `keep_alive=0`.
- GPU 8 GB shared with the asr worker (which unloads after idle, decision 8 of the transcription spec).
- Transcripts: full text + segments with times, `speaker` NULL (no diarization yet), dropped-window flags.
- Recordings are mono: agent/customer talk ratio and interruptions need diarization ⚠ NEEDS USER (pyannote licence).

## Scope
- In:
  - **Ollama** service (compose profile `llm`, GPU, internal network, models in a volume). Model pull is a separate
    one-off container on the normal network with no call data mounted.
  - `callsight/analysis/llm.py`: prompt (Arabic, JSON output with schema: summary ≤ 3 sentences, topic from
    `config/topics.yaml`, action_items list, satisfaction 1–10 + one-line reason, escalation yes/no), strict JSON
    validation, 1 retry on invalid JSON, `prompt_version`.
  - `call_analysis` table (call, model, prompt_version, summary, topic, action_items, llm_score, reason, created);
    queue **`nlp`** task `analyze_call` (runs in the normal worker image: HTTP to Ollama only, no torch); queued
    when a transcript becomes `done` (newest first, same ON CONFLICT pattern as transcription); skips calls whose
    transcript is mostly dropped windows.
  - **Flags** (no LLM): `config/rules.yaml` word lists (bad words, escalation phrases like "ابي المدير", "بشتكي",
    competitor names) matched on normalised text → `call_flag` rows (kind, phrase, segment time).
  - **Silence metrics** from the audio (RMS, no diarization): total silence %, longest silence, number of silences
    > 10 s (hold-like). Talk ratio + interruptions = later (needs diarization).
  - **Rating page** (supervisor + admin) "تقييم المكالمات": queue of transcribed calls, player + transcript + summary,
    1–10 buttons + optional note; `satisfaction_label` table (call, user, score, note, blind flag, time).
    **Blind test set:** the first 100 calls picked by a fixed-seed sampler (balanced over agents) are shown
    without any LLM estimate; the rest show the LLM estimate as a pre-fill. Progress: x / 500 labels, y / 100 test.
  - Call detail shows summary, topic, action items, flags, silence metrics and "تقدير أولي: n/10 (غير معتمد)".
  - **Training code** `callsight/analysis/train.py` (runs in the asr-worker image which has transformers):
    fine-tune a dialect Arabic BERT as a 1–10 regressor on the labelled calls' text; evaluates on the blind set
    (MAE, % within ±2) vs the LLM baseline; writes a report. Tested on a tiny synthetic dataset with a tiny model.
- Out:
  - Running the real fine-tune (⚠ NEEDS USER: ≥ 500 labels); diarization; summaries in PDFs/KPIs; using the LLM
    estimate in any KPI.

## Requirements
1. LLM output that isn't valid JSON or breaks the schema is never stored; after 1 retry the call is marked `error`.
2. Prompt and transcript never leave the PC (Ollama on the internal network; worker → `http://ollama:11434` only).
3. Logs and task results contain no transcript text, summary or reason (ids/counts only).
4. Rules file edits take effect without code changes (re-run `flags --recompute`).
5. Blind-set calls never show any LLM estimate on the rating page (API omits it), checked by test.
6. Analysis is idempotent per (call, model, prompt_version).

## Acceptance criteria
- [ ] pytest: prompt building, JSON validation (bad/missing fields/out-of-range scores), retry, idempotency, flags
      matching (normalised, word boundaries), silence metrics on synthetic WAVs, rating API (auth, blind omission,
      progress counts), blind-set sampler repeatable, train.py on a tiny synthetic dataset (in Docker)
- [ ] On this PC: Ollama model pulled; 5 real transcribed calls analysed; quick Arabic quality check of qwen2.5:7b
      vs qwen3:8b on those 5 written down (which is kept and why)
- [ ] Rating page works in the browser (screenshot)
- [ ] ⚠ NEEDS USER: rate 500 calls (100 blind first) → then run training (one command) and compare with the LLM

## Open questions (decided by 2 judges on the user's behalf)
1. **Base model for the fine-tune:** MARBERTv2 vs CAMeLBERT-DA (check licences: must allow our business use).
2. **Topic list:** start with a small fixed list in `config/topics.yaml` (e.g. استفسار، شكوى، طلب صيانة، فاتورة/دفع،
   متابعة طلب، أخرى) that the manager can edit (recommended) vs free-text topics from the LLM.
3. **Which LLM by default:** qwen2.5:7b vs qwen3:8b — decided by the quick test on 5 real calls (recommended).
4. **Silence threshold:** fixed RMS threshold + 10 s "long silence" (recommended) vs adaptive per call.
