# Spec: API for the ERP system (2026-11-18)

Status: built on branch `spec/2026-11-18-erp-api`, tested locally; not on `main` yet (push = live).

## Why
The company ERP (api4.sstli.com + its web page) wants to show CallSight's data inside its own customer-service
screens: calls, transcripts, recordings, analysis, follow-ups, shares, KPIs, PDF reports, live board.
The ERP server calls CallSight; the browser never talks to CallSight and never sees the key.

## What
1. **API keys** (`api_client` table, migration 0017). Admin makes a key on the Users page ("مفاتيح الأنظمة الأخرى");
   the key (`cs_<prefix>_<48 hex>`) is shown once, only its SHA-256 is kept. Admin can stop a key (never re-enabled).
   Both actions go to the admin log. A key can never make or stop keys (403): only a logged-in admin.
2. **Acting user.** Each request sends `X-Api-Key` and `X-Act-As: <CallSight username>`. The request gets exactly
   that user's role and scope (agent = own calls), and every audit row (recording plays, transcript views, report
   downloads, listen) names that user. Unknown / disabled user or bad / stopped key = 401. With `X-Api-Key` present
   the cookie is ignored (no fallback). The ERP keeps its own table "ERP user -> CallSight username".
3. **Same endpoints as the web page.** No second copy of any rule: the key only replaces the login cookie.
   The Origin rule stays: a server sends no Origin; a page of another site is still refused.
4. **Live board over HTTP:** `GET /api/live/snapshot` (admin + supervisor) = the WebSocket's first message, for
   polling. Listen (`POST /api/live/{id}/listen`) skips only the Origin check for key requests (no cookie to steal);
   it stays OFF by `LISTEN_ENABLED` as before.

## Not changed
Login, cookies, roles, the web page's behaviour, PBX, workers.

## Tests
`tests/test_erp_api.py` (8): key shown once + hash only, rights of the acting user (agent / supervisor / admin),
refusals (bad key, no user, unknown, disabled user), cookie never mixed in, stopped key + keys cannot manage keys,
audit names the acting user, writes from a server without Origin + other-site Origin still refused, live snapshot
staff only. `test_agent_role.py`: new routes classified STAFF_ONLY. Full suite: 730 passed, 1 skipped.
Migration 0017 up / down / up OK.
