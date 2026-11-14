# Overnight report (2026-11-14): what was recommended, what was done, what is left

The plan and every judges' decision are in `docs/overnight-plan.md`. Each spec's own decisions are in its `spec.md`, under "Decisions".
*(Final update, 2026-11-14 evening: all 7 specs are merged and the live database is at migration 0008. The Claude usage limit paused the work several times; it was resumed each time.)*

**In one line:** the code for all phases is built and running on this PC. What is left needs you (list below). Automatic transcription, automatic call analysis, the live board and Listen are all **switched off** until you turn them on.

## How it worked
- Claude's plan (R1–R7) and every spec's open questions went to **2 independent judge agents**. Their merged decisions
  are written in the spec, with the status "approved by 2 judges on the user's behalf — user may revise".
- Each finished spec went to **3 reviewers** (bugs, security, accuracy). Findings were fixed, critical/high fixes got a
  2nd round, then the spec was merged with `--no-ff`.
- Safety:
  - `main` is tagged `pre-overnight-2026-11-14`.
  - A `pg_dump` is taken before every migration, in `data/db-backups/`.
  - No Yeastar login, no PBX settings changed, nothing deleted, no paid or cloud APIs.

## What was recommended → what was done
| Spec | Recommended (judges' key decisions) | Done |
|---|---|---|
| S1 ASR benchmark | Test 3 local models on 30 real clips: Whisper large-v3, Audar Turbo, Jais 4-bit. All run offline (no network). Strict WER. | ✅ **Merged.** 21 review findings (1 critical) fixed. **All 3 models ran 30/30 clips.** Jais 4-bit needs 11.2 GB (more than 8 GB) and runs about 4× slower than real time, so it needs the server. The **winner is picked when you type the 30 reference texts** (`goldset score`). |
| S4 KPI + weekly/monthly PDF | KPIs from SQL. Repeat callers within the same team. Masked numbers in the PDF. PDF engine allowed to load fonts only. | ✅ **Merged, live.** Migration 0004 applied. PDFs made for week 2026-11-01 and month 2026-10. Real week 2026-11-08: 453 calls, 218 answered, AHT 149 s, 80 repeats. Review: 0 critical/high. |
| S2 Transcription | Our own `asr` worker (not speaches). The model is set in config. 30 s windows. Bad windows dropped. Worker unloads when idle. Internal network, no PBX secret. | ✅ **Merged, live.** Migration 0005 applied. **5 real calls transcribed** in 5–10× real time. GPU freed after stop. 2 review rounds, all findings fixed. Automatic transcription stays **off until you set `ASR_START_DATE`**. |
| S7 Hardening | Agent role (own calls only, deny-by-default route test). `backup` + automatic restore check. Health page. PBX certificate file. Locked dependencies. Retention code **off**. | ✅ **Merged, live** (0006). Review: 1 high (ASR image build), 5 medium, 8 low, all fixed. Live backup: 18 tables + 418 recordings; **restore drill OK** (counts equal). Load test + GPU-image hashes = stretch, not done. |
| S3 Transcript view | Transcript next to the player. Arabic search (normalised, pg_trgm). Audited views/exports. .txt export. | ✅ **Merged, live** (0007). Review: 1 medium, 8 low, all fixed. Agents see transcripts of their own calls only. Real transcripts searchable. Browser check ⚠ NEEDS USER. |
| S5 Call analysis | Local Ollama `qwen2.5:7b` (vs `qwen3:8b`). Summary, topic, flags. First satisfaction estimate **never in KPIs**. Blind 100-call set. Fine-tune with CAMeLBERT-DA. | ✅ **Merged, live** (0008). Review: 0 critical/high, 8 medium, ~19 low; fixed (1 kept by design and documented). 566 tests pass. **Real run: 5 calls → 4 analysed in 26–82 s each, 1 skipped correctly (67% unclear audio).** `qwen2.5:7b` kept (faster than `qwen3:8b` on 5 calls). Automatic analysis **off** (`NLP_ENABLED=false`). Rating page built; no call rated yet. Fine-tune code ready, waits for 500 ratings. Not checked: pages in a browser; the training self-test after the fixes. |
| S6 Live board | Separate `live` service, **off by default**, own single PBX login. `pbx:auth_blocked` guard. WebSocket with Origin check. Listen = 403 stub + audit. | ✅ **Merged, live** (0009), **switched off**. Review: 16 findings fixed. Tested with fake PBX events only: **no contact with the real PBX**. The real test needs the office LAN. Listen always answers 403 and writes an audit line. |

## Not done (honest list)
- Winner of the ASR benchmark (needs your 30 texts).
- Phase 1 "7 OK nights" (needs the office LAN).
- Live board against the real PBX; Listen (paused by you; legal steps first).
- Satisfaction model training (needs 500 ratings); talk ratio / interruptions (need speaker split).
- Load test with 60k calls, GPU-image hashes, browser checks of the new pages.
- Transcript word search does not use its fast index yet (fine at today's size).

## ⚠ NEEDS YOU
1. **Type the 30 reference texts** on the "Gold set" page (admin). Then Claude runs `goldset score` and the best ASR model is chosen.
2. **Put the PC back on the office LAN** (192.168.50.189). Phase 1 needs 7 nights of OK reconcile.
3. **Set `ASR_START_DATE`** in `.env` (for example today's date) when you want new calls transcribed automatically.
4. **Hand-check one KPI week** (open the PDF, compare 2–3 numbers with the PBX).
5. Call analysis:
   - turn it on: `docker compose --profile llm up -d ollama nlp-worker`, then `NLP_ENABLED=true` in `.env`;
   - rate **500 calls** on "تقييم المكالمات": the first 100 are the blind test set (final, no hints), then 400 more;
   - put the real bad-word / escalation / competitor lists in `config/rules.yaml`;
   - get a legal/HR OK for internal use of the LLM estimate (it is never in KPIs or PDFs, and agents do not see it);
   - then download CAMeLBERT-DA and run the training (commands in `specs/2026-11-14-call-analysis/tasks.md`, task 10).
6. Accounts, backups, retention:
   - create the agent accounts;
   - choose an **encrypted second disk/NAS** for backups (`BACKUP_HOST_DIR`, then `BACKUP_OFFSITE_CONFIRMED=true`);
   - decide retention days (deletion of old recordings stays off until you decide);
   - confirm the PBX certificate fingerprint (`pbx-cert --fetch`, then `--accept`).
7. Live board (on the office LAN): enable "Call Status Monitor" per trunk on the PBX, set `LIVE_ENABLED=true`,
   run `docker compose --profile live up -d live`, check the delay is under 2 s.
8. Before Listen is ever enabled: IVR notice "calls may be recorded or monitored", written staff policy, lawyer's OK.
9. Delete `D:/callsight-backup-test` (750 MB unencrypted test copy of recordings made by the backup test) when you no longer need it. Decide if the gold-set clips are kept under retention.
10. Log in as admin and look at the new pages: "الحالة" (health), a call's transcript (click a line → audio jumps there), the analysis block on a call, and "تقييم المكالمات".
11. Optional: pyannote (speaker split) needs you to accept its licence on Hugging Face and give a token.
12. Phase 0 leftovers: legal recording announcement (PDPL), server purchase.

## Model downloads + licences
| Model | Licence | Status |
|---|---|---|
| Whisper large-v3 | MIT | downloaded, in use |
| Audar-ASR-V1-Turbo | AudarAI Community License: **check before production use** | downloaded (benchmark) |
| Jais-ASR W4A16 AWQ | Apache 2.0 | downloaded (benchmark; needs the server) |
| qwen2.5:7b | Apache 2.0 | downloaded, in use for call analysis |
| qwen3:8b | Apache 2.0 | tested for comparison only, not used |
| CAMeLBERT-DA | Apache 2.0 | **not downloaded yet** (needed only for the fine-tune) |
| MARBERT | research-only | **rejected** |
