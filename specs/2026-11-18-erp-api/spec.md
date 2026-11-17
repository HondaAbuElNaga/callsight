# Spec: ERP API — the ERP system pulls all CallSight data

**Status:** draft (2026-11-18) · **Plan step:** new, proposed as step 1b of [finish plan](../../product/finish-plan.md)
**Asked by:** the ERP side ("he needs all the system added in his system").

## Goal
The company's ERP shows CallSight data next to its own records (employees, customers, tickets), so managers use
one system. Today CallSight has no way in for another system: every request needs a person's login cookie. The
only key we hold (`CS_API_KEY`) is the customer-service system's key that CallSight uses to call **them**.

## Scope
**In:** API keys made by the admin; a read-only, versioned JSON API under `/api/ext/v1/` that covers calls,
agents, analysis, flags, follow-ups, forwards and KPIs; sync by "changed since"; a usage log; a short guide for
the ERP developer.
**Out:** the ERP writing anything into CallSight; logging into CallSight from the ERP (SSO); webhooks/push
(the ERP pulls); private notes, passwords, users, the admin log; live board events.

## Requirements
1. **Keys.** The admin makes a key with a name (e.g. "نظام ERP") on the users page, in a new section
   «مفاتيح الأنظمة الأخرى (API)», or with the CLI `callsight api-key create "<name>"`. The key looks like
   `cs_` + 40 random characters. It is shown **once**. Only its SHA-256 hash and its first 8 characters are stored.
   Also list (name, start of the key, made by, made at, last used) and revoke. Supervisors and agents cannot see
   the section.
2. **Auth.** The key goes in the `X-Api-Key` header, which is the same style the customer-service system uses.
   Keys work **only** on `/api/ext/v1/*`. Cookies do not work there, and keys do not work anywhere else. A wrong,
   revoked or missing key gets 401 with one message. Every request is HTTPS (already true on AWS).
3. **Read-only, whole company.** A key sees every team's data, like the admin does, but it can only use GET.
4. **Endpoints (JSON, UTF-8, times in ISO 8601 with offset):**
   - `GET /agents`: ext, name, team, active.
   - `GET /calls?since=&until=&changed_since=&cursor=&limit=` (max 500 per page): one row per call. It has the
     call fields (direction, customer number, agent ext, times, wait/talk/hold seconds, status, hang-up by,
     recording yes/no) plus the priority, the follow-up stage and the satisfaction score when they exist.
   - `GET /calls/{id}`: the row above plus legs, the analysis (topics, summary, sentiment, score), flags and the
     follow-up history. Private notes are not included.
   - `GET /calls/{id}/transcript`: segments. These carry the redaction from spec 7 once it is live (see open
     question 3).
   - `GET /forwards?changed_since=&cursor=`: forward events and hops.
   - `GET /kpi?from=&to=&team=`: the same numbers as the KPI page (`/api/kpi`), computed by the same code.
5. **Sync.** `changed_since` uses `updated_at` (calls get one update stamp whenever their analysis, flags or
   follow-up change). Paging uses an opaque cursor, so a sync that runs every few minutes never misses or repeats
   a row.
6. **Limits and log.** Each key gets 120 requests per minute (429 + `Retry-After` when it goes over). Every
   request is logged with key, path, status, rows and time. The admin log shows key create and revoke events.
7. **Deleted content.** If a call's content was deleted (`content_deleted_at`) or removed by retention, the API
   returns the call row without a transcript or analysis, exactly like the web page does.
8. **Guide.** `docs/erp-api.md` (English): how to get a key, every endpoint with one example, sync loop,
   errors. This file goes to the ERP developer.

## Acceptance criteria
- [ ] Tests: key create shows it once and stores only the hash; revoked key → 401; key on `/api/calls` → 401;
      cookie on `/api/ext/v1/calls` → 401; POST on any ext path → 405.
- [ ] Tests: `/calls` paging over 1 200 test calls returns each call exactly once; `changed_since` returns a call
      again after its analysis changes.
- [ ] Test: `/kpi` equals `/api/kpi` for the same range.
- [ ] Tests: no private note, password hash or admin-log row appears in any ext response; deleted content is
      not returned.
- [ ] Test: request 121 in one minute → 429.
- [ ] On the live server: the admin makes the "نظام ERP" key, `curl` with it returns today's calls, and revoking
      it makes the same `curl` return 401.
- [ ] 3-agent review (bugs, security, accuracy) done; no open critical/high.

## Technical approach
- Migration `0022_api_key`: tables `api_key` (id, name, prefix, sha256, created_by, created_at, last_used_at,
  revoked_at) and `api_request_log`. `call.updated_at` gets bumped by the analysis/flag/follow-up writers.
- New `callsight/api/ext.py` router. It is a FastAPI dependency that checks the header with a constant-time
  hash compare. It reuses `calls.list_calls` / `call_detail` with the admin scope and `followups.kpi`, so its
  numbers never differ from the web pages.
- Rate limit uses the existing Redis (already in the tech stack), so no new tool is needed.
- Risk: this is the first door into CallSight that has no person behind it. A leaked key exposes customer
  numbers and transcripts. That's why the key is shown once, can be revoked, every request is logged, and there
  is the optional IP allowlist (question 2).

## Open questions
1. **What "all the system" means:** data in the ERP through this API (recommended), or only a link/button from
   the ERP that opens CallSight pages? Recommendation: the API. The ERP shows the data its own way, and nobody
   needs a second login.
2. **IP allowlist:** should a key work only from the ERP server's IP? Recommendation: yes, if the ERP side can
   give a fixed IP. Leave it empty (= any IP) until then.
3. **Transcripts and recordings:** recommendation is transcripts **yes**, but only after redaction (spec 7) is
   live. Until then, `/transcript` returns 403 "waiting for redaction". Recordings (audio) **no** in v1: they are
   large and the most sensitive. Add them later if the ERP really needs them.
4. **Customer phone numbers:** send them in full (the ERP needs them to match its customers)? Recommendation:
   yes, full numbers. The key is admin-made and logged.
5. **Where in the plan:** recommendation is step 1b, right after cleanup is pushed and before rollout, because
   the ERP side is waiting.
