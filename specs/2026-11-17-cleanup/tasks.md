# Tasks: cleanup
- [x] 1. User approves the spec. → "approve all", 2026-11-18.
- [x] 2. Branch `spec/2026-11-17-cleanup` from `main` (after step 0 is live). → from f6afec0.
- [x] 3. Tests first: LoginGate retry, agent 403 on `/api/calls`, KPI log both dates.
- [x] 4. Fixes: take 376fb94 (LoginGate), agent 403, KPI log `date_to` (migration if a column is needed). → merge
      98218a3; `list_allowed` in `callsight/api/main.py`; `report_download.period_end` (migration 0021, up/down/up OK).
- [x] 5. Merge `ops/nlp-on` workflow tasks. → merge 0754067.
- [ ] 6. Queue test: user calls queue 6401 while agents are busy; save the masked event; fix the board if needed.
      → waits for the user's test call.
- [x] 7. Tick the old specs (A3, 11–14, ring-time 10, rating-set 7, asr-benchmark 9, pbx-discovery 11–13).
      → aws task 14 kept [~] (GPU `stopped` after idle not recorded yet).
- [~] 8. AWS tasks 15–16: acceptance check + 3-agent review of deploy files; fix findings. → review done (below);
      task 15 needs the live checks: GPU state `stopped` after an idle cycle, open ports from outside.
- [x] 9. Rewrite roadmap "Current position". → + stale Phase 0/2/3/5/7 lines, tech-stack AWS rows ✅.
- [x] 10. Full pytest; 3-agent review of this branch; fix. → 867 passed before the review. 3 reviewers
      (bugs, security, accuracy; deploy files included): 0 critical / 0 high; 24 findings, 22 fixed, 2 rejected:
      - fixed: LoginGate lock without expiry (gets an expiry, full wait) + test; blind-reset needs
        `yes-delete-blind-set` and refuses once any blind call is rated, `psql -q`; gpu-update only from `main`;
        prepare / build-models always switch the GPU internet off again (trap); gpu-cmd refuses commands that
        would print secrets; main-ps no longer fails when grep finds nothing; link-keys backup outside the rsync
        target; endpoint test for the من/إلى download; 13 stale doc lines.
      - rejected: "gpu-setup races with deploy / server-task" — one runner on the main server runs one job at a
        time, so they never overlap (written in server-task.yml); "Postgres / Redis traffic to the GPU server not
        encrypted" — accepted risk inside the private network, written in docs/aws.md section 10.
- [ ] 11. "ok push" → live; delete merged branches/worktrees (user said yes via "approve all").
