# Spec: Satisfaction model — a trusted 1–10 score

**Status:** draft (2026-11-17) · **Plan step:** 5 of [finish plan](../../product/finish-plan.md)
**Roadmap items:** Phase 3 "Label ≥ 500 calls", "Fine-tune CAMeLBERT-DA", "nlp worker: tuned model's score".
**Phase 3 Done when:** on the 100 blind calls, the score is within ±2 of the human on ≥ 80 % of calls.

## Goal
Today every call gets an LLM first guess (`llm_score`), not checked, so it is never used in KPIs (decision 9; one
exception: the forward proof rule > 7). This spec turns the 500 human ratings into a checked model, and only a
model that passes the blind test may feed the KPIs.

## Scope
**In:** training on the GPU server, comparison on the frozen 100 blind calls, choosing the winner, re-scoring,
switching the KPI "average satisfaction" on.
**Out:** new rating pages (built); new labels beyond the 500.

## Requirements
1. **Data:** the 400 rated non-blind calls train (5-fold inside for early stopping); the 100 blind calls are only
   for the final test. Input = the customer's text from step 4 (whole text if diarization failed).
2. **Training:** `callsight/analysis/train.py` (built) runs as a GPU task (`gpu-setup.yml -f task=train`), seed
   fixed, CAMeLBERT-DA revision pinned; the model file is versioned and kept on the main server + S3.
3. **Compare** on the blind 100: tuned model, LLM first guess, and the average of both. Report: % within ±2,
   mean error, per-agent bias. Written to `docs/satisfaction-model.md`.
4. **Choose:** the best that reaches ≥ 80 % within ±2 becomes the score. If none passes: KPIs keep the score
   hidden, report written, user decides (more ratings or accept).
5. **Re-score:** all calls since `ASR_START_DATE` get the new score (`analyze --rescore`), model version stored.
6. **KPIs:** "average satisfaction" shown on the KPI page and PDF only from the passing model. Agents still see
   no score (decision kept).
7. **Forward proof rule (> 7):** uses the new score once it passes.

## Acceptance criteria
- [ ] 500 ratings exist (rating-status: 100 blind + 400)
- [ ] Training run reproducible (same seed → same blind result ± 0.5 %)
- [ ] Blind test result written; Phase 3 "Done when" ticked only if ≥ 80 % within ±2
- [ ] pytest: KPI hides the score unless the model is marked passed; rescore stores version

## Open questions
1. If the tuned model and LLM both pass: **recommended: the tuned model** (fast, no GPU-heavy LLM per call).
2. If nothing passes: rate 300 more calls (**recommended**) or accept a lower bar?
