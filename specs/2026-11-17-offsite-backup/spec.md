# Spec: Off-site backup on AWS + restore drill

**Status:** approved (2026-11-18, user: "approve all" — every open question = the recommendation) · **Plan step:** 3 of [finish plan](../../product/finish-plan.md)
**Roadmap items:** Phase 6 "Done when: restore-from-backup drill succeeds"; hardening task 11 (encrypted second
copy, `BACKUP_OFFSITE_CONFIRMED`).

## Goal
Today the nightly backup sits on the same disk as the data (`/srv/callsight/backup`), plus the daily disk
snapshots (7 kept). If the server or its disk is lost, recordings and the database must still come back. This
spec adds an encrypted copy in a separate place and proves a full restore works.

## Scope
**In:** an S3 bucket in Frankfurt (encrypted, versioned, no public access, old versions expire), the main
server's role may only write/read that bucket, the nightly backup uploads there, a monthly automatic restore check,
and one full restore drill onto a fresh test server.
**Out:** backups of the GPU server (holds nothing that cannot be downloaded again); a second AWS region.

## Requirements
1. **Bucket** in `callsight.yaml`: SSE-S3 encryption, versioning, block all public access, lifecycle (DB dumps:
   keep 30 days; recordings: keep while retention keeps them; old versions removed after 30 days). The user
   applies the stack update by hand (no access keys), click by click.
2. **Server role:** the main server's instance role gets only `s3:PutObject/GetObject/ListBucket` on that bucket.
   No keys in `.env`.
3. **Upload:** after `callsight backup`, new DB dump + new recordings/reports are copied to the bucket (only new
   files; sha256 checked). Failure shows red on the health page and in the log.
4. **Retention follows:** when retention deletes a recording, its S3 copy is deleted too (PDPL: copies too).
5. **Monthly check:** a beat job downloads the newest DB dump from S3 and runs `backup verify` (restore into the
   scratch DB, counts equal). Result on the health page.
6. **Restore drill (once):** a workflow task restores the S3 copy onto a temporary second server (or a scratch
   compose project on the main server), opens the website, compares call counts, then removes it. Written as a
   step list in `docs/aws.md` section 7 ("if the server is lost").
7. `BACKUP_OFFSITE_CONFIRMED=true` on the server after the first good upload (health warning gone).

## Acceptance criteria
- [ ] cfn-lint 0 errors for eu-central-1; the bucket refuses public access (AWS console check)
- [ ] pytest: upload picks only new files, retention deletes the S3 copy (fake S3)
- [ ] First nightly upload on the server: DB dump + recordings in the bucket, health page green
- [ ] Monthly check run by hand once: counts equal
- [ ] Restore drill done; the time it took is written in `docs/aws.md`

## Technical approach
Python `boto3` (new in the tech stack: needed for S3; pinned with hashes) inside the worker; the instance role
gives credentials, so nothing secret is stored. Cost: about 30 GB/year of audio ≈ under $1/month.

## Open questions
1. Keep recordings in S3 only as long as on the server (**recommended**), or longer?
2. Drill target: scratch project on the main server (**recommended: free, 20 minutes**) or a temporary second
   server (closer to real loss, ≈ $0.10)?
