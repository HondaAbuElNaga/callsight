# Tasks: Gold set + speech-to-text benchmark

- [x] 1. User approves the spec and answers the open questions (who types, clip length, Audar licence)
         → approved 2026-11-14: user types, 60 s clips, Audar included
- [ ] 2. Tech stack update; branch `spec/2026-11-14-asr-benchmark`
- [ ] 3. Tests + code: normaliser, WER/CER, chunking
- [ ] 4. Tests + code: gold set picker/cutter + migration 0003 `gold_clip` + CLI `goldset pick`
- [ ] 5. Tests + code: gold set API (admin only) + "Gold set" page (player, 5 s back, RTL text box, save, progress)
- [ ] 6. Pick + cut the 30 clips; user's team starts typing
- [ ] 7. `asr-bench` container (CUDA 12.8 PyTorch); smoke test: GPU visible, Whisper on 1 clip
- [ ] 8. Model adapters: Whisper large-v3, Audar-ASR-V1-Turbo, Jais-ASR (4-bit try)
- [ ] 9. Run all models on the 30 clips (after all texts are typed); write `docs/asr-benchmark.md`
- [ ] 10. Verify the acceptance criteria
- [ ] 11. Review with 3 subagents (bugs, security, accuracy); fix confirmed findings
- [ ] 12. Spec status → done; tick roadmap items; update "Current position"; merge to main
