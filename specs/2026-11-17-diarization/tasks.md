# Tasks: diarization
- [x] 1. User approves; user makes the Hugging Face token (click by click) and fills the 30-row "who speaks first" form.
- [x] 2. Branch `spec/2026-11-17-diarization`. Tech stack: pyannote 🔜 → ✅ (pinned version + hashes).
- [x] 3. Tests first: naming rules, metrics, export, migration.
- [x] 4. Build: diarize step in asr worker, segment `speaker`, metrics, view + export, KPI averages.
- [ ] 5. Score on the 30 gold clips (≥ 27/30). Needs the server + the user's 30-row table (still to fill).
- [x] 6. 3-agent review; fix.
- [ ] 7. "ok push" → live; gpu-update; build-models with the token, then internet-off again.
- [ ] 8. Re-run task on rating set + calls since ASR_START_DATE; measure GPU time.
- [ ] 9. Roadmap: Phase 2 goal + Phase 3 talk-ratio item ticked.
