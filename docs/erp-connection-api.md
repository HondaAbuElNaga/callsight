# Connecting CallSight and the ERP (customer-service system): the API in both directions

There are **two separate connections**. People often mix them up, so this page keeps them apart:

| | Direction | Who calls whom | Why |
|---|---|---|---|
| **Part A** | CallSight → ERP | Our server calls `api4.sstli.com` | Make a ticket for a forwarded problem and follow it |
| **Part B** | ERP → CallSight | The ERP server calls our server | Show our calls, transcripts, follow-ups, KPIs inside the ERP screens |

For each part, this page says: what is needed, who gives it, where to find it, and the exact requests.

> The browser never talks to the other system. Only server ↔ server. The keys are never in a web page.

---

# Part A: CallSight → ERP (forwarding a problem)

## A1. What CallSight needs from the ERP team

| What | Example | Who gives it | Where we put it |
|---|---|---|---|
| The API address | `https://api4.sstli.com/api/customer-service` | ERP team | Server `.env` → `CS_API_URL` |
| An API key | (secret text) | ERP team | Server `.env` → `CS_API_KEY` |
| The "PBX account" guid | `xxxxxxxx-xxxx-…` | ERP team (one user in their system that all our actions are recorded under) | Server `.env` → `CS_USER_GUID` |
| The key must be allowed these paths | see A3 | ERP team | Their key settings |

If `CS_API_KEY` or `CS_USER_GUID` is empty → forwarding is **off** (the «تحويل» options do not appear in the form).

Code: `callsight/cs_api.py` (the client), `callsight/forward.py` (what is sent and when).

## A2. How every request looks

- Header: `X-Api-Key: <CS_API_KEY>`
- Header: `Accept: application/json`
- Every request carries `userGuid` = `CS_USER_GUID` (in the body, or in the URL for GET).
- Timeout: 20 seconds.

How we read their answers:

| Their answer | What we do |
|---|---|
| 200 + JSON | OK |
| 400 / 404 / 409 … with `{"message": "..."}` | Failed. We show their `message` to the agent ("لم تُحوَّل إلى …: <message>") |
| 401 / 403 | Key problem. That run stops; the next run (5 min later) tries again. If the key is not fixed within 24 h, the form becomes "failed" |
| 408 / 429 / 5xx / network error | Busy. We try again in 5 minutes (for up to 24 h). **Except** a 5xx / timeout / network error on `POST /tickets`: the form becomes "failed" and is not resent, because the ticket may already have been made |

## A3. The requests we send (all of them)

### 1) The list of people the agent can forward to

```http
GET /forward-targets?userGuid=<CS_USER_GUID>
```

We need back:

```json
{
  "groups": [
    { "key": "men",
      "branches": [
        { "name": "فرع الرياض",
          "employees": [
            { "guid": "aaaa-1111", "name": "محمد أحمد", "isBranchSupervisor": true },
            { "guid": "bbbb-2222", "name": "خالد علي",  "isBranchSupervisor": false } ] } ] } ]
}
```

We use: `guid`, `name`, branch `name`, group `key`, `isBranchSupervisor`. We keep them in our table `forward_person`.

### 2) Find the customer by phone (create if missing)

```http
POST /customers/by-phone
{ "userGuid": "<CS_USER_GUID>", "phone": "0551234567", "createIfMissing": true }
```

We need back: `{ "accountGuid": "cccc-3333" }`

### 3) Make the ticket and give it to the chosen person

```http
POST /tickets
{
  "userGuid": "<CS_USER_GUID>",
  "accountGuid": "cccc-3333",
  "callType": 0,
  "priority": 3,
  "close": false,
  "forwardToGuid": "aaaa-1111",
  "notes": "مشكلة محوّلة من خدمة العملاء - الأولوية: عالية\nرقم العميل: 0551234567\nوقت المكالمة: 2026-11-22 10:15\nالمهلة: 24 ساعة عند كل مسؤول من وقت وصولها إليه\nالموظف: أحمد (201) - رقم المكالمة عندنا: 12345\nما حدث: ...\nملاحظة للمسؤول: ..."
}
```

We need back: `{ "guid": "dddd-4444", "number": 5521 }`

- `priority`: high `3`, medium `2`, low `1` (their scale: 1 low / 2 normal / 3 high / 4 urgent).
- `notes`: max **590** characters (their limit).
- `guid` must be ≤ 64 characters. `number` is shown to the agent as the ticket number.

### 4) Read the ticket back (every 5 minutes while it is open)

```http
GET /tickets/{guid}?userGuid=<CS_USER_GUID>
```

We need back: `{ "ticket": { ..., "closedAt": "..." }, "timeline": [ ... ] }`
From it we learn: closed or not, who holds it now, questions written on it, the solution text.

### 5) Move the ticket to the next person (after 24 h without a solution)

```http
POST /tickets/{guid}/forward
{ "userGuid": "<CS_USER_GUID>", "toUserGuid": "<next person's guid>", "note": "..." }
```

Chain: employee → branch supervisor → management. 24 hours each. It starts at the level of the person picked.
At management, each extra manager (up to 4 in all) gets a copy ticket through `POST /tickets`.

### 6) Write on the ticket (the agent's answer or note)

```http
POST /tickets/{guid}/followup
{ "userGuid": "<CS_USER_GUID>", "note": "...", "close": false }
```

### 7) Open a closed ticket again

```http
POST /tickets/{guid}/reopen
{ "userGuid": "<CS_USER_GUID>", "note": "..." }
```

## A4. Checklist for the ERP team (Part A)

- [ ] Give us: API address, API key, PBX account guid.
- [ ] Allow the key: `GET /forward-targets`, `POST /customers/by-phone`, `POST /tickets`, `GET /tickets/{guid}`,
      `POST /tickets/{guid}/forward`, `POST /tickets/{guid}/followup`, `POST /tickets/{guid}/reopen`.
- [ ] The PBX account must have the right to create tickets and forward them to every person in `/forward-targets`.
- [ ] Errors: always JSON `{"message": "..."}` (Arabic is fine: the agent reads it).

## A5. How we test it (on our server)

```bash
docker compose run --rm worker python -m callsight forward-targets   # reads the people list only (no ticket made)
docker compose run --rm worker python -m callsight forward-sync      # copies that list into our forward_person table
```

Or open **حالة النظام** (health page): the job `forward_send` shows OK / failed and the last error.

---

# Part B: ERP → CallSight (the ERP reads our data)

Spec: `specs/2026-11-18-erp-api/spec.md` (live on `main`). Rights: `docs/permissions.md` part 4.

## B1. What the ERP team needs from us

| What | Example | Who gives it | How to find / make it |
|---|---|---|---|
| Our address | `https://<SITE_NAME>` (e.g. `https://calls.example.com`) | Us | Server `.env` → `SITE_NAME` |
| An API key | `cs_ab12cd34ef_<48 hex>` | Our admin | See B2 (made on our Users page) |
| A CallSight username to act as | `erp_reader` | Our admin | See B3 (made on our Users page) |
| The list of our endpoints | this page, B5 | Us | This page |

## B2. How to make the key (our admin, 1 minute)

1. Log in to CallSight as **admin**.
2. Open **المستخدمون والإعدادات** → section **«مفاتيح الأنظمة الأخرى»**.
3. Write a name (e.g. `ERP`) → make the key.
4. **Copy the key now**: it is shown **once only**. We keep only its fingerprint (SHA-256).
5. Send it to the ERP team in a safe way (not in a group chat).

To stop a key: same section → «إيقاف». A stopped key never works again; make a new one.
Both actions are written in the admin log.

## B3. Which user the ERP acts as (important)

Every request says **who** it acts as: `X-Act-As: <CallSight username>`. The request gets **exactly that user's
rights**, and every audit row (recording played, transcript read, report downloaded) names that user.

| Act as | Sees | Can change | Use it? |
|---|---|---|---|
| **supervisor** account (e.g. `erp_reader`) | All calls, transcripts, analysis, follow-ups, KPIs, reports, forwards, live board | Only follow-ups, notes, shares, ratings | ✅ **Recommended** |
| agent account | Only that agent's own calls; no call list, no KPIs | That agent's forms | Only if the ERP shows one agent their own work |
| admin account | Everything, **and** can create users, reset passwords, erase call content | Everything | ❌ **Never** |

Two ways for the ERP:
- **Simple:** one supervisor account `erp_reader` for all requests.
- **Per person:** the ERP keeps its own table "ERP user → CallSight username" and sends the right name each time
  (then the audit shows the real person).

A key can **never**: make or stop keys (403), change the rules page (403).

## B4. How every request looks

```http
GET https://<SITE_NAME>/api/calls?from=2026-11-22&to=2026-11-22
X-Api-Key: cs_ab12cd34ef_0123456789abcdef0123456789abcdef0123456789abcdef
X-Act-As: erp_reader
Accept: application/json
```

| Answer | Meaning |
|---|---|
| 200 | OK |
| 401 | Bad / stopped key, or unknown / disabled user |
| 403 | That user's role may not do this |
| 404 | No such call, **or** the call is not that agent's (we do not say which) |
| 422 | Bad parameters (e.g. dates) |

Rules:
- Only from a **server**. A request sent from a browser page of another site is refused (Origin check on POST / PUT / PATCH / DELETE; GET requests are not Origin-checked).
- When `X-Api-Key` is sent, any cookie is ignored.
- Times are ISO 8601 with time zone (UTC). Dates in `from` / `to` are Riyadh days (`YYYY-MM-DD`).

## B5. The endpoints the ERP will use most

The same endpoints as our web page. Full list with rights: `docs/permissions.md` part 3.

### Calls

| Request | What you get |
|---|---|
| `GET /api/calls?from=&to=&agent=&status=&direction=&q=&page=` | Call list, 50 per page |
| `GET /api/calls/summary?from=&to=…` | Counts by status |
| `GET /api/calls/{id}` | One call |
| `GET /api/calls/{id}/recording` | The audio (audited) |
| `GET /api/calls/{id}/transcript` | The transcript (audited) |
| `GET /api/calls/{id}/summary` | AI summary only |
| `GET /api/calls/{id}/analysis` | Full AI analysis: score, flags (staff only) |

Filters: `status` = `answered`, `abandoned_queue`, `abandoned_ivr`, `no_answer`, `busy`, `voicemail`;
`direction` = `inbound`, `outbound`, `internal`; `agent` = extension; `q` = part of the customer number.

Example answer of `GET /api/calls`:

```json
{
  "total": 132, "page": 1, "page_size": 50,
  "items": [
    { "id": 12345, "start_time": "2026-11-22T07:15:02+00:00", "direction": "inbound",
      "customer_number": "0551234567", "agent_ext": "201", "agent_name": "أحمد",
      "answered_by_agent": true, "status": "answered",
      "ivr_seconds": 8, "wait_seconds": 21, "talk_seconds": 184, "total_seconds": 213,
      "ring": null, "has_recording": true, "transcript_match": false }
  ]
}
```

### Follow-ups and forwards

| Request | What you get |
|---|---|
| `GET /api/followups?from=&to=&agent=&state=&priority=` | Follow-up list + counters |
| `GET /api/calls/{id}/followup` | The form, its step, history, forward status (ticket number, chain) |
| `GET /api/forwards`, `GET /api/forwards/{id}` | Forwards page |
| `GET /api/kpi/forwards` | Forward KPIs |

### KPIs, reports, live

| Request | What you get |
|---|---|
| `GET /api/kpi?period=…&team=&agent=` | KPIs |
| `GET /api/reports/pdf?period=…` | PDF report (audited) |
| `GET /api/live/snapshot` | The live board now (poll it; supervisor/admin only) |

## B6. How the ERP links a ticket to our call

Every ticket we make has this line in `notes`:

```
الموظف: أحمد (201) - رقم المكالمة عندنا: 12345
```

`12345` is our call id. The ERP can open the call with `GET /api/calls/12345` and
`GET /api/calls/12345/followup` (with its key + `X-Act-As`).

## B7. Checklist (Part B)

- [ ] Our admin makes the key (B2) and sends it safely.
- [ ] Our admin makes a **supervisor** account for the ERP (e.g. `erp_reader`), or the ERP sends real usernames.
- [ ] We give the ERP our address `https://<SITE_NAME>`.
- [ ] The ERP calls only from its server, with `X-Api-Key` + `X-Act-As`.
- [ ] First test: `GET /api/me` → should answer with the acting user and role.

```bash
curl -s https://<SITE_NAME>/api/me \
  -H "X-Api-Key: <the key>" -H "X-Act-As: erp_reader"
```

---

# Summary in one table

| | Part A: we call them | Part B: they call us |
|---|---|---|
| Address | `api4.sstli.com/api/customer-service` | `https://<SITE_NAME>` |
| Key header | `X-Api-Key` (their key) | `X-Api-Key` (our key `cs_…`) |
| Who | `userGuid` in every body (PBX account) | `X-Act-As: <our username>` |
| Key stored in | our `.env`: `CS_API_KEY`, `CS_USER_GUID` | made on our Users page, shown once |
| Used for | tickets for forwarded problems | showing our calls / follow-ups / KPIs in the ERP |
| Code | `callsight/cs_api.py`, `callsight/forward.py` | `callsight/api/auth.py` (key check), all `/api/*` routes |
