# CallSight on AWS

Written 2026-11-15 (spec `specs/2026-11-15-aws-deploy`). Region: Europe (Frankfurt), `eu-central-1`
(changed from Bahrain on 2026-11-15: the Bahrain region did not answer; see the spec, "Region change").
Status 2026-11-15: the **main server is live** (stack `callsight`, fixed address 63.184.90.120,
https://calls.mohanadyehia.com). The PBX login is still refused and the GPU server does not exist yet: see
`specs/2026-11-15-aws-deploy/tasks.md`. Items in section 10 about the GPU server are still not verified.

## 1. What runs where

| | Main server (always on) | GPU server (on only while it works) |
|---|---|---|
| AWS type | `t3.medium`, Ubuntu 24.04, 100 GB disk, fixed internet address | `g4dn.xlarge` (NVIDIA T4 16 GB), AWS GPU image, 100 GB disk, **no** internet address |
| Runs | postgres, redis, worker, beat, api, live, caddy (HTTPS) | asr-worker, ollama, nlp-worker |
| Holds | database, recordings, reports, config, PBX secret | model files only; database + redis passwords |
| Compose | `docker-compose.yml` + `deploy/aws/compose.main.yml` | `deploy/aws/compose.gpu.yml` |
| Private address | 10.20.1.10 | 10.20.11–13.x (zone 1–3) |

How a call flows:

1. The main server takes in every call and downloads its recording, all day. The call waits as "queued".
2. AWS (EventBridge Scheduler) starts the GPU server at :00 and :30, 08:00–23:30 Riyadh time, every day.
3. At boot the service `callsight-gpu` (`deploy/aws/gpu_run.py`) starts the three containers and asks the main
   database every 30 s how many calls still wait (`python -m callsight gpu-work --queue`).
4. After 2 minutes in a row with the answer 0 it powers the server off. AWS then shows it as "stopped": the disk
   is kept, the hourly cost ends.

Safety rules of the GPU service: a failed question never counts as 0; 10 minutes of failed questions = power off
(the main server is not reachable, nothing can be done); 3 hours = power off in any case.

The health page (admin) has the item **"أقدم مكالمة تنتظر التفريغ / التحليل"**: a warning when a call has waited more
than 90 minutes between 08:00 and 23:30. That is how a missed wake-up shows (for example AWS had no free GPU
server). Nothing is lost: the calls are done at the next wake-up.

## 2. Who can reach what

- From the internet: only ports 80 and 443 of the main server (80 redirects to HTTPS). Port 22 only from the one
  address typed into the stack ("Setup allowed from this internet address"); empty = closed.
- Database (5432), redis (6379, with a password) and the recordings share (NFS 2049, read-only) of the main server:
  only from the GPU server. This is enforced by the AWS security group. The ports are published on the server's
  network address, so **never open them in the security group**.
- The GPU server has no internet address. Its only way out leads through the main server, which passes nothing
  on unless `sudo bash deploy/aws/setup-internet.sh on` was run (only during setup). Normal state: `off`.
- Changed from the PC setup: there the GPU services sit on a Docker network without internet. On AWS they must
  reach the main server, so that protection is replaced by "no internet address + nothing passed on". Audio and
  text still never go to an outside AI service.
- Database and redis traffic between the two servers is **not encrypted** (it stays inside the private AWS
  network). The disks of both servers are encrypted.

## 3. Files

| File | Purpose |
|---|---|
| `deploy/aws/callsight.yaml` | Everything on AWS in one CloudFormation file: network, 2 servers, fixed address, wake-up schedule, daily disk snapshots (7 kept), cost alarm |
| `deploy/aws/compose.main.yml` | Main server: override of `docker-compose.yml` (ports for the GPU server, redis password, HTTPS, `Secure` cookie) |
| `deploy/aws/compose.gpu.yml` | GPU server: the three GPU services, own file |
| `deploy/aws/Caddyfile` | HTTPS for the website (automatic Let's Encrypt certificate) |
| `deploy/aws/gpu_run.py`, `callsight-gpu.service` | GPU server: work, then power off |
| `deploy/aws/install-main.sh`, `install-gpu.sh`, `setup-internet.sh` | One-time installation |
| `deploy/aws/env.main.example`, `env.gpu.example` | The two `.env` files |

The PC setup (`docker compose up -d`) does not use any of these files and works as before.

## 4. Setup, in order

The user's clicks in the AWS website are in `specs/2026-11-15-aws-deploy/user-steps.md`. The commands below are
run from the setup PC (Git Bash), in the repository folder. `EIP` = the fixed address from the stack's Outputs.

**Once on the PC**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/callsight_aws -N "" -C callsight-setup    # the .pub line goes into the stack
```

**Main server** (stack created with "Create the GPU server?" = no)

```bash
ssh -i ~/.ssh/callsight_aws ubuntu@EIP "sudo install -d -o ubuntu -g ubuntu /opt/callsight"
git archive HEAD | ssh -i ~/.ssh/callsight_aws ubuntu@EIP "tar -x -C /opt/callsight"
ssh -i ~/.ssh/callsight_aws ubuntu@EIP "sudo bash /opt/callsight/deploy/aws/install-main.sh"
# .env: copy deploy/aws/env.main.example to /opt/callsight/.env, fill it, chmod 600
ssh -i ~/.ssh/callsight_aws ubuntu@EIP "cd /opt/callsight && sudo docker compose -f docker-compose.yml -f deploy/aws/compose.main.yml up -d --build"
```

- The website's DNS record (type A, name → `EIP`) must exist **before** this start, or no certificate is issued.
- Accounts are new on AWS: `docker compose ... exec api python -m callsight create-user NAME --role admin --generate`.
- Set `ASR_START_DATE` to the go-live day. Then **stop the services on the PC** (`docker compose stop`): only one
  system may log in to the PBX.
- PBX: `YEASTAR_BASE_URL=https://sstli.ras.yeastar.com`, certificate check on. Make the **first login only after
  the supplier confirmed** that API Access is on and `EIP` is permitted (a refused login blocks all logins until
  `pbx-unblock`).
- Check from outside: only 80 and 443 answer on `EIP`; 5432, 6379, 2049 do not.

**GPU server** (stack updated with "Create the GPU server?" = yes; costs money from this minute)

```bash
# ~/.ssh/config on the PC:
#   Host cs-main
#     HostName EIP
#     User ubuntu
#     IdentityFile ~/.ssh/callsight_aws
#   Host cs-gpu
#     HostName <GpuPrivateAddress from the Outputs>
#     User ubuntu
#     IdentityFile ~/.ssh/callsight_aws
#     ProxyJump cs-main
ssh cs-main "sudo bash /opt/callsight/deploy/aws/setup-internet.sh on"
ssh cs-gpu "sudo install -d -o ubuntu -g ubuntu /opt/callsight"
git archive HEAD | ssh cs-gpu "tar -x -C /opt/callsight"
ssh cs-gpu "sudo bash /opt/callsight/deploy/aws/install-gpu.sh"          # leaves /etc/callsight/stay-on in place
# .env: copy deploy/aws/env.gpu.example to /opt/callsight/.env, fill it, chmod 600
ssh cs-gpu "cd /opt/callsight && C='sudo docker compose -f deploy/aws/compose.gpu.yml --env-file .env' \
  && \$C build \
  && \$C --profile setup run --rm model-download openai/whisper-large-v3 --revision 06f233fe06e710322aca913c1bc4249a0d71fce1 --include '*.json' '*.txt' model.safetensors \
  && \$C --profile setup run --rm ollama-pull qwen2.5:7b \
  && \$C up -d"
ssh cs-main "sudo bash /opt/callsight/deploy/aws/setup-internet.sh off"
ssh cs-gpu "curl -m 5 -sI https://example.com || echo 'no internet: correct'"
ssh cs-gpu "sudo rm /etc/callsight/stay-on && sudo systemctl start callsight-gpu"   # works, then powers off
```

- The Whisper revision above is the one pinned in `callsight/asr/models.py`; compare the `ollama list` digest with
  the one in the call-analysis `tasks.md`.
- Measure Whisper speed on the T4 on the first day (a 60 s clip took 7.4 s on the PC's RTX 5050).
- Last step: update the stack with "Wake the GPU server every 30 minutes?" = on.

## 5. Every day

| Want | How |
|---|---|
| Start the GPU server now | EC2 → Instances → `callsight-gpu` → Instance state → Start. It powers itself off when done |
| Keep it on for a repair | `ssh cs-gpu "sudo touch /etc/callsight/stay-on"`; afterwards `sudo rm /etc/callsight/stay-on` (it then powers off within 2 quiet minutes). The schedule must be on, or start it by hand first |
| Stop the wake-ups | Update the stack: "Wake the GPU server every 30 minutes?" = off. Calls keep coming in and wait |
| How much waits | Health page, or `ssh cs-main "cd /opt/callsight && sudo docker compose exec -T api python -m callsight gpu-work"` |
| Why did it power off | `ssh cs-gpu "journalctl -u callsight-gpu -n 50"` (reasons: `idle`, `cap`, `no-answer`) |
| Update the code | `git archive HEAD \| ssh cs-main "tar -x -C /opt/callsight"`, then `up -d --build`; same for `cs-gpu` (stay-on file + download switch on while it builds) |
| Change word lists (`config/`) | Copy the file to `/opt/callsight/config/` on the main server; the GPU server reads the same folder |
| Speaker split (agent / customer), first time | In this order: Actions → GPU setup → `gpu-update` (rebuilds the image with pyannote) → `build-diarize` (downloads the 3 pinned pyannote models with the `HF_TOKEN` secret, internet off again after) → `internet-off` (check) → `ssh cs-main "cd /opt/callsight && sudo docker compose exec -T worker python -m callsight transcribe --resplit --limit 500"` (rating set first; run again for more). GPU time per call: to be measured on the T4 (PC: Whisper +33 % for the word times, plus pyannote) |
| First-speaker check (page "من يتكلم أولاً") | Actions → GPU setup → `goldset-pick` (once: picks + cuts 30 calls that already have the speaker split, on the main server; refuses if a gold set exists). The admin fills the page; score after every row |
| Turn the speaker split off | `DIARIZATION=off` in `/opt/callsight/.env` on the GPU server, then `up -d asr-worker`; transcripts are saved as before |

Stack updates: never change the two "System image" fields or the setup key afterwards. A new value there makes
CloudFormation **replace the server** (its disk is deleted). Changing the other fields is safe.

## 5b. Automatic update from GitHub

- Repository (private): `HondaAbuElNaga/callsight`. It must stay private: anyone who can push to `main` can run
  commands on the server.
- Every push to `main` runs `.github/workflows/deploy.yml` on the runner `callsight-main`, which is installed on
  the main server (`/home/ubuntu/actions-runner`, a systemd service). The runner connects out to GitHub; no port
  is opened for it.
- It runs `deploy/aws/deploy-main.sh`: copy the code to `/opt/callsight` (never `.env`), database dump, build and
  restart, then check that the web page answers. A failed run is red in the repository's **Actions** tab.
- So: run the tests first and push to `main` only what should go live. Other branches do not deploy.
- Not covered: `.env` changes (edit on the server, then `up -d`) and the GPU server.

## 6. Cost

Estimate ≈ **$113 a month** (range $90–140), to be replaced by the real bill after the first week:

| Part | Estimate |
|---|---|
| GPU server, ≈ 2.5 h a day × $0.645 | ≈ $50 |
| GPU server disk (100 GB, also while stopped) | ≈ $10 |
| Main server `t3.medium` | ≈ $37 |
| Main disk + 7 daily snapshots | ≈ $12 |
| Fixed address | ≈ $4 |

Prices are from price-comparison sites for Bahrain, not from an AWS bill; Frankfurt prices were not looked up (expected to be close). A wake-up with nothing to do costs about 3 minutes
(≈ $0.03). The stack's cost alarm emails at $120 spent and at a forecast above $150 (whole AWS account).
`t3.medium` runs in AWS's default "unlimited" mode: long full-CPU periods (image builds) add a few cents.

## 7. Backups on AWS

- The nightly `callsight backup` (01:30) writes to `/srv/callsight/backup` on the same disk, as on the PC.
- AWS takes a snapshot of the whole main disk every day at 03:30 Riyadh time and keeps 7 (EC2 → Elastic Block
  Store → Snapshots). This is the copy that survives a broken server. It is in the same AWS account and region.
- `.env` is inside the disk snapshot, but keep your own copy too (see `docs/scale-out.md`, section 1).
- The GPU server has no backup: it holds nothing that cannot be downloaded again.

## 8. Going back to the PC

1. Stack: "Wake the GPU server every 30 minutes?" = off. On the main server: `docker compose ... stop`.
2. On the PC: `docker compose up -d`, then the asr / live profiles as before. In the PC's `.env`,
   `YEASTAR_BASE_URL` stays the office address. Ask the supplier to remove the AWS address from the permit list.
3. AWS started empty, so the calls made while AWS was live exist only there. To keep them, restore an AWS database
   dump on the PC (`docs/scale-out.md`, section 3) and copy `/srv/callsight/audio`.

## 9. Remove everything (stops all cost)

1. EC2 → Instances → `callsight-main` → Actions → Instance settings → Change termination protection → off.
2. CloudFormation → the stack → Delete.
3. EC2 → Snapshots: delete the remaining `callsight` snapshots (they are kept on purpose when the stack goes).

## 10. What was verified, what is open, known risks (updated 2026-11-18)

**Verified on the real servers (2026-11-15 / 16):** stack CREATE_COMPLETE (main), UPDATE_COMPLETE with the GPU part
(zone 2: zone 1 had no g4dn.xlarge); the AWS GPU image runs Docker with the NVIDIA runtime (Tesla T4); Whisper
large-v3 ≈ 0.1× real time (508 s call → 56 s) side by side with qwen2.5:7b; the live board through
`sstli.ras.yeastar.com` (on since 2026-11-15, team-only since 2026-11-16); wake-up every 30 min (19:30 run finished
the queue).

**Still to check (AWS tasks 15, 17):** the GPU server state is `stopped` after an idle cycle; from outside only
80/443 answer on 63.184.90.120; after 7 days: transcript delay and the real bill.

**Rules of operation:**
- One GitHub runner on the main server: jobs of Deploy, Server task and GPU setup run one at a time. GitHub keeps
  only ONE waiting run per workflow group: a second waiting run cancels the first, so check each run finished.
- A deploy updates the main server only. After a change to code the GPU runs (transcription, analysis), run
  GPU setup → `gpu-update` (refuses unless started from `main`).
- `gpu-cmd` logs are kept 90 days: never print the GPU `.env` (the task refuses `.env`, `env`, `compose config`).

**Known and accepted risk:** the GPU server talks to Postgres and Redis on the main server without TLS. Both run
only inside the private network (10.20.0.0/16), the ports are open only to the GPU server's security group, the
GPU server has no internet (except during a build), and Redis has a password. Whoever could read that traffic
would already be inside our AWS account. Revisit if a third server or another network is added.
