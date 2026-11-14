# Overnight report (2026-11-14) — what was recommended, what was done, what is left

**Status: stopped early. The usage limit was reached partway through the night.** Plan + every judges' decision:
`docs/overnight-plan.md`. Each spec's own decisions are in its `spec.md` → "Decisions".

## How it worked
- Claude's plan (R1–R7) and every spec's open questions → **2 independent judge agents** → merged decisions written
  into the spec (status "approved by 2 judges on the user's behalf — user may revise").
- Finished specs → **3 reviewers** (bugs, security, accuracy) → fixes → 2nd round for critical/high → merge `--no-ff`.
- Safety: `main` tagged `pre-overnight-2026-11-14`; live DB backed up to
  `data/db-backups/callsight-before-0004-*.dump`; no Yeastar login; no PBX settings changed; nothing deleted.

## Done
| Spec | State |
|---|---|
| S1 ASR benchmark | **Merged to main.** Reviewed (21 findings incl. 1 critical CLI syntax error, all confirmed ones fixed; round 2 clean). New `goldset score` command writes `docs/asr-benchmark.md`. All 3 models run offline (no network). Jais 4-bit does not fit 8 GB (needs the server). Overnight run of 30 clips × 3 models started (Whisper 30/30 done). |
| S4 KPI + reports | **Built, not merged** (branch `spec/2026-11-14-kpi-reports`, worktree `.claude/worktrees/agent-a9967fac5b3f193fb`). 214 tests pass; PDF 1.2 s for 6,000 calls; real week 2026-11-08 matches raw SQL. The 3-reviewer review was started, then stopped at the usage limit. |
| S2 transcription | Builder **stopped halfway** (branch in worktree `.claude/worktrees/agent-af51c271a053280d5`). |
| S3 transcript view, S5 call analysis, S6 live board, S7 hardening | **Specs + tasks approved by 2 judges**; not built yet. |

## Next steps for Claude (next session)
1. Re-run the 3 reviewers on S4 → fix → migrate the live DB (backup already taken) → merge → rebuild the stack.
2. Finish S2 (continue on its branch) → review → merge. Then S3, S5, S7, S6 in that order.
3. Run `goldset score` once your texts are typed.

## ⚠ NEEDS YOU
1. Type the 30 reference texts on the "Gold set" page (admin). Then the benchmark picks the winner.
2. Put the PC back on the office LAN (Phase 1 needs 7 nights of OK reconcile; live board testing).
3. Later, when those specs are built: set `ASR_START_DATE` in `.env`; rate calls (100 blind + 500 total); give the real bad-word/escalation lists;
   create agent accounts; choose an **encrypted second disk/NAS** for backups; decide retention days; enable
   "Call Status Monitor" per trunk on the PBX; legal steps before Listen (IVR notice, staff policy).
4. Optional: pyannote (speaker split) needs you to accept its licence on Hugging Face and give a token.
5. Phase 0 leftovers: legal recording announcement (PDPL), server purchase.

## Model downloads + licences
Whisper large-v3 (MIT), Audar-ASR-V1-Turbo (AudarAI Community License: check before production use), Jais-ASR
W4A16 AWQ (Apache 2.0). Planned: qwen2.5:7b / qwen3:8b (Apache 2.0), CAMeLBERT-DA (Apache 2.0). MARBERT was rejected
because its licence is research-only.
