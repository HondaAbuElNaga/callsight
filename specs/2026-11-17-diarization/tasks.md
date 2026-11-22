# Tasks: diarization
- [x] 1. User approves; user makes the Hugging Face token (click by click) and fills the 30-row "who speaks first" form.
- [x] 2. Branch `spec/2026-11-17-diarization`. Tech stack: pyannote 🔜 → ✅ (pinned version + hashes).
- [x] 3. Tests first: naming rules, metrics, export, migration.
- [x] 4. Build: diarize step in asr worker, segment `speaker`, metrics, view + export, KPI averages.
- [x] 5. Score on the 30 gold clips (≥ 27/30): first try 17/30 (rules g1) → naming rules g2 → **27/30** (2 wrong, 1 unsure; all 20 inbound right), 2026-11-22.
- [x] 6. 3-agent review; fix.
- [x] 7. "ok push" → live; gpu-update; build-models with the token, then internet-off again.
- [x] 8. Re-run task on rating set + calls since ASR_START_DATE (~400 redone 2026-11-21); GPU time ≈ 35 s per call on the T4 (Whisper + pyannote; 30 calls in ~18 min).
- [x] 9. Roadmap: Phase 2 goal + Phase 3 talk-ratio item ticked.
