# Spec: Run CallSight on AWS (small server always on + GPU server that wakes every 30 min)

**Roadmap item:** Phase 0, "Buy & install server" (changed 2026-11-15 by the user: no server is bought, the system
moves to AWS)
**Status:** approved (2026-11-15: the user said "go with this solution" and answered the four questions below)

## Goal
The system no longer depends on the analytics PC being switched on and plugged into the office network. Every call
is taken in all day, the website and live board are always reachable, and transcription + call analysis run on a GPU
that is paid for only while it works. Target cost: **about $110 a month (range $90–140)**.

## Decisions already made by the user (2026-11-15)
- AWS, region **Frankfurt (`eu-central-1`)** (first choice was Bahrain `me-south-1`, see "Region change" at the end); the
  Saudi region (planned December 2026) is a possible later move.
- GPU server **`g4dn.xlarge`**, **woken every 30 minutes**, works until nothing is waiting, then stops itself.
- Calls come **08:00–23:00 almost every day** (two shifts). A result up to ~35 min after hang-up is accepted
  (this replaces "within 15 min" in the Phase 2 "Done when").
- The PBX is reached through the Yeastar remote address `sstli.ras.yeastar.com` (API Access for the FQDN, one
  permitted IP). The user calls the supplier to open it. Fallback: small tunnel box in the office.
- Rejected: GPU always on (~$500), GPU on for working hours only (~$320), Hetzner (~$210).

## Facts (checked 2026-11-15)
- Volume: 60–110 recorded team calls a day ≈ **2–3 h of talk a day** (DB, 2026-11-07 … 11-14), first call ~08:00,
  last ~22:00. Recordings are mono WAV 8 kHz 16-bit ≈ 58 MB per hour ≈ 5 GB a month.
- Whisper large-v3 on this PC (RTX 5050, 8 GB): 60 s clip in 7.4 s. The T4 in `g4dn.xlarge` (16 GB) is unmeasured.
- `sstli.ras.yeastar.com:443` answers from the internet; FQDN API Access was off on 2026-11-12. Whether the
  WebSocket (live board) works through the FQDN is **not verified**.
- Compose today: `postgres` and `redis` are reachable only inside the PC; `asr-worker`, `ollama`, `nlp-worker` sit on
  an `internal: true` network (no internet) and read recordings from a shared folder; `api` listens on 127.0.0.1 only.
- Transcripts are queued by a beat job every 5 min; analyses are queued by another beat job after the transcript is
  done. So "the queues are empty" is **not** the same as "no work is left".
- Prices are from price-comparison sites (g4dn.xlarge Bahrain ≈ $0.645/h), not from the AWS order page.
- The AWS account exists. AWS CLI is not installed on the PC. New accounts have a GPU quota of 0 (request sent by
  the user in step A3).

## Scope
- In:
  - **Main server** (always on): `t3.medium`, Ubuntu 24.04, 100 GB gp3 disk, Elastic IP. Runs postgres, redis, worker,
    beat, api, live. Holds the recordings, reports and config.
  - **GPU server** (`g4dn.xlarge`, AWS GPU base image with NVIDIA driver + Docker, 100 GB gp3): runs `asr-worker`,
    `ollama`, `nlp-worker` only. Same private network as the main server. No public address.
  - **Wake:** Amazon EventBridge Scheduler starts the GPU server at :00 and :30 from 08:00 to 23:30 Riyadh time,
    every day. No AWS key is stored on either server for this.
  - **Sleep:** a boot service on the GPU server starts the three containers, then asks the main database every 30 s
    "is any work left?" and powers the server off after 2 quiet minutes (instance shutdown behaviour = stop).
    Safety cap: off after 3 h in any case.
  - **"Work left" command** (new, small): `python -m callsight gpu-work [--queue]` prints the number of calls still
    waiting for a transcript or an analysis (same eligibility rules as the two beat jobs: `ASR_START_DATE`,
    `ASR_MIN_TALK`, `NLP_ENABLED`, running rows count as work); `--queue` also queues them now instead of waiting
    for the 5-min beat job.
  - **Private links main ↔ GPU:** postgres and redis published on the main server's private address only; redis gets
    a password; security group allows those two ports + NFS from the GPU server only. Recordings and `config/` reach
    the GPU server read-only over NFS.
  - **Compose files:** `deploy/aws/compose.main.yml` and `deploy/aws/compose.gpu.yml` (overrides of the existing
    file; the PC setup keeps working unchanged).
  - **Website over HTTPS:** Caddy in front of `api` on ports 80/443 with an automatic Let's Encrypt certificate;
    cookie `Secure`; `LIVE_ORIGINS` set to the public address. Login stays as it is (roles, argon2, JWT cookie).
  - **PBX:** `YEASTAR_BASE_URL=https://sstli.ras.yeastar.com`, certificate verification **on**; the Elastic IP is the
    one permitted address. One real login only after the supplier confirms (a refused login blocks us).
  - **Infrastructure as one CloudFormation template** `deploy/aws/callsight.yaml` (security groups, 2 instances,
    Elastic IP, scheduler + its role, daily disk snapshots of the main server kept 7 days, a $150 monthly budget
    alarm by email).
  - **Start empty (answer 4):** only `config/` and a new `.env` go to the main server; admin + supervisor accounts
    are created again; `ASR_START_DATE` = the go-live day. Then the PC's services are stopped (only one system logs
    in to the PBX). The PC keeps its old data.
  - **Health page:** new item "oldest call waiting for the GPU"; warning when older than 90 min between 08:00 and
    23:30 (covers a wake-up that AWS refused for lack of capacity).
  - Docs: `docs/aws.md` (how it is built, how to start/stop by hand, costs, how to go back to the PC).
- Out:
  - Moving to the Saudi region; Spot instances for the GPU (cheaper, can be interrupted): later ideas.
  - Recordings in S3; a managed database (RDS): not needed at this size.
  - Diarization, model training, Listen: unchanged, separate roadmap items.
  - The legal decision itself (recordings stored outside Saudi Arabia, PDPL): the user's, see open question 3.

## Requirements
1. With the GPU server stopped, a new call still appears in the call list and on the live board; its recording is
   downloaded; it waits with status "queued".
2. After a scheduled wake-up, every waiting call gets its transcript and (when `NLP_ENABLED=true`) its analysis,
   and the GPU server is stopped again within 3 minutes of the last job finishing.
3. The GPU server never stops while a transcript or analysis is running or still to be queued (tested with the
   "transcript done, analysis not queued yet" case).
4. `gpu-work` returns 0 when nothing is waiting, counts running rows, respects `ASR_START_DATE` / `ASR_MIN_TALK` /
   `NLP_ENABLED`, and with `--queue` never queues a call twice.
5. Postgres, redis and NFS on the main server are reachable from the GPU server only; from the internet only ports
   80 and 443 answer (SSH only from the address used for setup, closed afterwards or on request).
6. The website works only over HTTPS with a valid certificate; HTTP redirects to HTTPS.
7. The GPU server has no public address and no PBX secret, `APP_SECRET` or AWS key on it.
8. (dropped: start empty, answer 4)
9. The PC setup (`docker compose up -d`) still works unchanged from the same repository.
10. A wake-up that fails (no capacity) loses nothing: the calls are processed at the next wake-up and the health
    page shows the warning after 90 min.

## Acceptance criteria
- [ ] `pytest` passes, incl. new tests for `gpu-work` (requirement 4) and the health item (requirement 10).
- [ ] The boot/idle script has a test with a fake "work left" command: stays up while work > 0, powers off after
      2 quiet minutes, powers off at the 3 h cap (requirements 2, 3).
- [ ] The AWS website accepts `deploy/aws/callsight.yaml` (stack reaches CREATE_COMPLETE); `docker compose -f … config` is
      valid for both AWS compose files and for the unchanged PC file (requirement 9).
- [ ] From outside: `nmap`-style check shows only 80/443 open on the Elastic IP; 5432/6379/2049 refused
      (requirement 5); `https://<address>/` has a valid certificate (requirement 6).
- [ ] One real test call made by the user with the GPU server stopped: visible in the list within 5 min, transcript
      present after the next wake-up, GPU server state `stopped` afterwards (requirements 1, 2).
- [ ] After 7 days: ≥ 95 % of calls between 08:00 and 23:00 have a transcript within 40 min of hang-up
      (`transcribe status` latency), and AWS Cost Explorer shows ≤ $35 for the week.
- [ ] `product/tech-stack.md`, `product/mission.md` ("100 % local" wording) and the Phase 2 "Done when" updated.

## Technical approach
- **Why EventBridge + self power-off:** no code and no AWS credentials on the servers for start/stop; a start with
  nothing to do costs about 3 minutes (≈ $0.03). A stopped GPU server costs only its disk.
- **Why NFS for recordings:** the workers read plain files today; NFS inside the private network needs no code
  change. The GPU side mounts read-only.
- **The `internal: true` network cannot be kept on the GPU server** (the workers must reach the main server). It is
  replaced by: no public address + a security group whose outbound rule allows only the main server. Model and
  image downloads happen once during setup, before that rule is tightened. Prompts and audio still never go to a
  third-party AI service.
- **GPU memory:** 16 GB holds Whisper (3.2 GB) and `qwen2.5:7b` (~5 GB) together; the Redis "gpu" lock and
  `keep_alive=0` stay as they are (correct, just not needed for memory).
- **New tools (tech-stack first):** AWS EC2, EBS snapshots (Data Lifecycle Manager), EventBridge Scheduler,
  CloudFormation, AWS Budgets; Caddy 2 (reverse proxy, Apache-2.0); NFS (`nfs-kernel-server` / `nfs-common`).
- **Cost estimate (to be replaced by the real bill):** GPU ≈ 2.5 h/day × $0.645 ≈ $50; GPU disk ≈ $10; main server
  ≈ $37; main disk + snapshots ≈ $12; Elastic IP ≈ $4 → ≈ $113.
- **Risks:**
  - T4 speed is unmeasured → measured on the first day; if 30-min cycles are too slow the schedule or cap changes.
  - AWS has no free `g4dn.xlarge` at a wake-up → requirement 10; if it happens often: allow `g4dn.2xlarge` or
    another zone as second choice.
  - Live board through the FQDN may not work (WebSocket unverified) → the live board stays off on AWS until tested;
    fallback is the office tunnel box.
  - The supplier refuses FQDN API Access → office tunnel box (small always-on device with WireGuard/Tailscale).
  - GPU quota request refused or slow → nothing else is blocked; the main server can go live first and calls wait.
  - Recordings leave the office and the country → open question 3 must be answered before any recording is copied.

## Answers to the open questions (user, 2026-11-15)
1. **How the AWS parts get created: by hand, no access key.** So: the user uploads the one CloudFormation file in
   the AWS website (Create stack → upload), which creates all parts in one go; no AWS CLI and no AWS key on the PC.
   Claude installs the servers over SSH from this PC with an SSH key made on this PC (public half goes in as a stack
   parameter; SSH is open only to the PC's current internet address). Template check = the AWS website accepts it.
2. **Website: a sub-address of the company's own domain**, open from anywhere with login, HTTPS. The user (or whoever
   manages the domain) adds one DNS record pointing to the Elastic IP; the name is asked for when the IP exists.
3. **Legal: yes**, recordings and call texts may be stored on AWS in Bahrain (later the same day: Frankfurt, see below).
4. **Old data: start empty.** AWS begins with new calls only; the old data stays on this PC. Config files, word
   lists and team settings are copied; user accounts are created again on AWS. Requirement 8 and the row-count
   criterion are therefore dropped.

## Region change (2026-11-15, same day)
- Found at user step A3: `me-south-1.console.aws.amazon.com` and `ec2.me-south-1.amazonaws.com` time out from the PC
  (UAE and Frankfurt answer in under 1 s). News reports say the Bahrain region has been offline since strikes in
  March/April 2026 and that AWS cannot recover data stored there; the UAE region was reported hit too. Not
  confirmed on AWS's own status page.
- Options given to the user: Frankfurt (recommended), UAE, United States (N. Virginia, where the user's other
  project runs), wait for the Saudi region. The user was told that choosing Frankfurt means recordings and call
  texts are stored in Germany, then selected **Europe (Frankfurt)** in the AWS website and asked for the next step.
- Effect on this spec: region name only. Prices in "Facts" and the cost estimate were looked up for Bahrain;
  Frankfurt prices are not looked up yet (expected close; the real bill replaces the estimate, task 17).
  Wake-up times stay in Riyadh time. The template was re-checked with cfn-lint for `eu-central-1`.
