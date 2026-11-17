# Tasks: Run CallSight on AWS

Spec: [spec.md](spec.md) · Branch: `spec/2026-11-15-aws-deploy`

## Approval
- [x] 1. User approves the spec and answers open questions 1–4 → 2026-11-15: by hand (no access key), company domain, legal yes, start empty.

## User steps on AWS (guided one by one)
- [x] A2. Region: Bahrain was enabled but does not answer → the user chose **Europe (Frankfurt)** 2026-11-15 (on by default, nothing to enable).
- [x] A3. Service Quotas → EC2 → "Running On-Demand G and VT instances" → 4 (wait for approval). Request sent 2026-11-15 in Frankfurt ("Case Opened"); not approved yet. → approved (CloudShell printed 4.0, 2026-11-15).
- [x] A3b. (done 2026-11-15: both image ids written into the template; GPU type offered in all three zones) Paste one command in AWS CloudShell and send the answer (image ids + zones that offer the GPU type).
- [x] A4. (main server created 2026-11-15, CREATE_COMPLETE) Upload `deploy/aws/callsight.yaml` in CloudFormation (main server first), guided. A4c = GPU server, A6 = schedule on.
- [x] A4b. DNS record added 2026-11-15: `calls.mohanadyehia.com` → 63.184.90.120 (Cloudflare, DNS only; the user's own domain).
- [x] A5. Supplier enables FQDN API Access and permits the Elastic IP. User reported "done" 2026-11-15, but the login was refused until the FQDN → API Access entry was saved; works since 2026-11-15 (see task 12).

## Preparation in the repository (no AWS cost)
- [x] 2. `product/tech-stack.md`: AWS services, Caddy, NFS, cfn-lint added; "100 % local" principle changed (also
        the pitch in `product/mission.md`). No AWS CLI is used (answer 1).
- [x] 3. Tests for `gpu-work` (requirement 4), then `callsight/gpuwork.py` + CLI command `gpu-work [--queue]`.
        `--queue` runs the two existing beat jobs in-process (same rules, ON CONFLICT → never twice).
        Assumption: with `NLP_ENABLED=false` no analysis counts as work, also not rows queued by hand.
- [x] 4. Tests for the health item, then `gpu.wait` on the health page (API + Arabic label). Calls that came
        after 23:30 count their wait from 08:00 (else a false warning every morning).
- [x] 5. Tests for the boot/idle loop with fake commands and a fake clock, then `deploy/aws/gpu_run.py` +
        `callsight-gpu.service`. **Changed:** Python instead of `gpu-run.sh` (testable on the Windows PC; needs only
        the system's Python 3). **Added:** a failed work check is never "0" (power off after 10 min of failures);
        the file `/etc/callsight/stay-on` blocks every power-off (setup, repairs).
- [x] 6. `deploy/aws/compose.main.yml` (override) and `deploy/aws/compose.gpu.yml`; `docker compose config` valid
        for main, GPU and the unchanged PC file. **Changed:** the GPU file is a file of its own, not an override
        (no postgres/redis/api definitions on that server). Also: `Caddyfile`, `env.main.example`,
        `env.gpu.example`, `install-main.sh`, `install-gpu.sh`, `setup-internet.sh` (syntax-checked only).
- [x] 7. `deploy/aws/callsight.yaml`: GPU server + schedule behind yes/no fields. cfn-lint for `eu-central-1` (first run was for `me-south-1`):
        0 errors, 2 warnings (the optional GPU image field is a plain text field). **Changed vs spec:**
        (a) image ids are fixed values, not "latest" look-ups: a look-up is repeated at every stack update and a
        newer image would replace the server and delete its disk → new user step A3b gets the ids;
        (b) the GPU server's outbound rule is not tightened by a stack update: its only way out leads through the
        main server, which passes nothing on unless `setup-internet.sh on` was run (same effect, one stack update
        less for the user);
        (c) three private zones + field "Zone of the GPU server", because it is not verified which zone
        offers `g4dn.xlarge`.
- [x] 8. `docs/aws.md` (section 10 lists what is not verified until the first setup).

## Build on AWS (costs start here)
- [x] 9. User created the stack with the main server only (A4). Elastic IP **63.184.90.120**. Checked from the PC: SSH
        login works (Ubuntu 24.04.5, 2 CPUs, 3.7 GB, 96 GB disk, 10.20.1.10); ports 80/443/5432/6379/2049 do not answer
        yet (nothing installed).
- [x] 10. Main server installed 2026-11-15 (Docker 29.1.3, compose 2.40.3, NFS export, compose up). Let's Encrypt
         certificate issued for `calls.mohanadyehia.com`; from the PC: HTTPS 200 with a valid certificate, HTTP → 308 to
         HTTPS; ports 80/443 open, 22 open (allowed from the PC's address only), 5432/6379/2049/8080/5433 closed.
- [x] 11. Copy `config/` + new `.env`; create admin + supervisor accounts; set `ASR_START_DATE`; stop the PC services. → done except "stop the PC services": moved to spec 2026-11-17-rollout.
         Done 2026-11-15: `config/` (with the code), new `.env` (new random passwords; PBX ID/secret left EMPTY so
         no login is tried before the supplier confirms), account `admin`. PBX ID/secret added later the same day (after
         the user reported A5 done). Open: supervisor account, `ASR_START_DATE` (go-live day), stopping the PC
         services (`docker compose stop` on the PC; they collect nothing anyway: the PC is off the office network).
- [x] 12. After A5: one PBX login through the FQDN; ingest runs; test the live board through the FQDN. → PBX login through the FQDN works since 2026-11-15; live board on (team-only since 2026-11-16).
         **2026-11-15: first login REFUSED, `errcode=70087 IP FORBIDDEN`** (1 failed attempt; 5 in a row = the PBX
         blocks the address). The server set `pbx:auth_blocked` and does not retry. Checked: the API answers through
         the FQDN with a valid certificate (errcode 10004 without a token); the server's outgoing address is
         63.184.90.120; the user's screenshot shows 63.184.90.120 / 255.255.255.255 as the ONLY line in Integrations
         → API → IP Restriction (the PC's .199 line is gone). Not checked yet: that the line was saved, and System →
         Network → Yeastar FQDN → API Access (enabled + its own permitted IPs). Next, only after the user confirms:
         `docker compose -f docker-compose.yml -f deploy/aws/compose.main.yml exec -T worker python -m callsight pbx-unblock`
         on the server, watch the worker log for `ingest:`; then `backfill --days N` for the calls missed since
         2026-11-14 22:21 Riyadh, set `ASR_START_DATE`, stop the PC services.
         **Later on 2026-11-15: login WORKS.** The missing part was System → Network → Yeastar FQDN → API Access
         (the user confirmed + saved it: Enabled, 63.184.90.120 / 255.255.255.255). After `pbx-unblock` the first
         ingest brought 88 calls (2026-11-14 11:44 → 2026-11-15 11:01 Riyadh) and recordings download through the
         FQDN (16 ok / 17 pending a few minutes later). Still open in this task: live board test through the FQDN.
         `ASR_START_DATE` stays empty until the GPU server exists (then: 2026-11-15, so all AWS calls are done).
- [x] 12b. (added) Automatic update from GitHub: private repo `HondaAbuElNaga/callsight`, self-hosted runner
         `callsight-main` on the main server, `.github/workflows/deploy.yml` → `deploy/aws/deploy-main.sh` on every
         push to `main` (code copy without `.env`, DB backup, build + restart, web check). First run OK (1m51s).
         The AWS branch was merged to `main` for this although the spec is still open. GPU server not covered yet.
- [x] 13. After A3: user updates the stack with GPU = yes; models + images; measure Whisper speed on the T4; → GPU server created 2026-11-16 (zone 2), Whisper ≈ 0.1× real time on the T4 (508 s call → 56 s).
         `setup-internet.sh off` + check that the GPU server reaches nothing but the main server.
- [~] 14. Turn on the schedule; the user makes one real test call with the GPU server stopped (acceptance). → schedule on 2026-11-16 19:14 Riyadh; wake-up verified, the user sees texts on the website. Not yet recorded: the GPU server state `stopped` after an idle cycle (acceptance) — check in task 15.

## Finish
- [ ] 15. Verify every acceptance criterion that does not need 7 days; full `pytest`.
- [ ] 16. Review by 3 subagents (bugs, security, accuracy); fix confirmed findings.
- [ ] 17. After 7 days: latency + real cost check; replace the cost estimate with the real bill.
- [ ] 18. Spec → done, roadmap + "Current position" + Phase 2 "Done when" updated, merge to `main`.
