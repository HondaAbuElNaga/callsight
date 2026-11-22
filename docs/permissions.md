# Who can see and change what (permissions map)

Written 2026-11-19 from the code on `main` (plus the local "rules page admin only" change, live after the next push).
Source of truth: `callsight/api/main.py` (gates `manager` = admin or supervisor, `admin` = admin only),
`callsight/api/work.py`, `callsight/api/admin.py`, `callsight/api/rules.py`, `callsight/api/forwards.py`.
Test that every route is classified: `tests/test_agent_role.py`.

## 1. How a request is checked

```
Request  ->  1. who are you?            ->  2. role from the database     ->  3. the route's gate          -> answer
             login cookie (web page)        admin / supervisor / agent        Admin / Manager / own call
             or X-Api-Key + X-Act-As (ERP)
```

- Not logged in (no cookie, bad key, unknown or disabled user): **401**.
- Logged in but the role is not allowed: **403**.
- An agent asking for a call that is not theirs: **404** (the call "does not exist" for them).
- The web page hides links the role cannot use. That is only for a clean screen: **the server is the real lock**.

## 2. Pages (what each person sees in the menu)

| Page | Admin | Supervisor | Agent |
|---|---|---|---|
| سجل المكالمات (call list) | ✅ all calls | ✅ all calls | ❌ (opens only own calls from المتابعات) |
| المتابعات (follow-ups) | ✅ all | ✅ all | ✅ own calls only (their home page) |
| المكالمات الحالية (live board) | ✅ | ✅ | ❌ |
| المؤشرات (KPIs) + PDF reports | ✅ | ✅ | ❌ |
| التحويلات (forwards) | ✅ | ✅ | ❌ |
| تقييم المكالمات (rating) | ✅ | ✅ | ❌ |
| النصوص المرجعية (gold set) | ✅ | ❌ | ❌ |
| القواعد (rules) | ✅ | ❌ | ❌ |
| حالة النظام (health) | ✅ | ❌ | ❌ |
| المستخدمون والإعدادات (users, settings, keys, admin log) | ✅ | ❌ | ❌ |

## 3. Every API route

R = read, W = write. "own" = only calls of the agent's own extension.

### Calls, recordings, transcripts

| Route | What | Admin | Supervisor | Agent |
|---|---|---|---|---|
| GET /api/me | who am I | R | R | R |
| GET /api/agents | agent names | R | R | R |
| GET /api/calls, /api/calls/summary | call list + counts | R | R | ❌ |
| GET /api/calls/{id} | one call | R | R | R own |
| GET /api/calls/{id}/recording | play the recording (audited) | R | R | R own |
| GET /api/calls/{id}/transcript (+ .txt) | transcript (audited), agent / customer lines once split; word search and the talk numbers (talk %, interruptions, wait) = staff only | R | R | R own (no talk numbers) |
| GET /api/calls/{id}/analysis | AI analysis, score, flags | R | R | ❌ |
| GET /api/calls/{id}/summary | AI summary only (no score, no flags) | R | R | R own |
| POST /api/calls/{id}/delete-content | erase a call's recording + text (needs reason + call number) | W | ❌ | ❌ |

### Follow-ups, notes, forwards, shares

| Route | What | Admin | Supervisor | Agent |
|---|---|---|---|---|
| GET /api/followups | follow-up list | R all | R all | R own |
| GET/PUT /api/calls/{id}/followup | follow-up form | R/W | R/W | R/W own |
| GET /api/calls/{id}/student | training look-up of the caller | R | R | R own |
| POST /api/calls/{id}/followup/notes | add a note | W | W | W own |
| POST /api/calls/{id}/forward/messages | answer the person holding a forwarded problem | ❌ (only the call's agent) | ❌ | W own |
| PUT /api/calls/{id}/my-note | private reminder (nobody else reads it) | W | W | W own |
| GET /api/shares, /api/calls/{id}/shares | notes shared with agents (`box=open\|archive`, `from`/`to`, `agent`: ignored for agents) | R | R | R (shared with them) |
| POST /api/calls/{id}/shares, DELETE /api/shares/{id} | share a call with an agent / remove | W | W | ❌ |
| POST /api/shares/{id}/seen, PUT /api/shares/{id}/reply | mark seen / reply | only the person it was shared with | same | W (shared with them) |
| POST /api/shares/{id}/close | move to my archive (the agent and each supervisor/admin have their own) | W (any) | W (any) | W (shared with them, only after replying; the reply is then locked) |
| GET /api/forwards, /api/forwards/{id}, /api/kpi/forwards | forwards page | R | R | ❌ |

### KPIs, reports, rating, live

| Route | What | Admin | Supervisor | Agent |
|---|---|---|---|---|
| GET /api/kpi | KPIs | R | R | ❌ |
| GET /api/reports, /api/reports/pdf, /api/reports/{id}/pdf | PDF reports (audited) | R | R | ❌ |
| GET /api/rating/queue, GET/PUT /api/rating/calls/{id} | rating page | R/W | R/W | ❌ |
| WS /api/live, GET /api/live/snapshot | live board | R | R | ❌ |
| POST /api/live/{id}/listen | Listen button (OFF: `LISTEN_ENABLED`) | W* | W* | ❌ |

\* refused while the feature is off (it is off now).

### Admin only

| Route | What | Admin | Supervisor | Agent |
|---|---|---|---|---|
| GET/PUT /api/goldset/{id}, audio | gold-set text editor | R/W | ❌ | ❌ |
| GET /api/first-speaker, PUT /api/first-speaker/{id} | "من يتكلم أولاً" check of the speaker split (30 gold calls) | R/W | ❌ | ❌ |
| GET /api/health | health page | R | ❌ | ❌ |
| GET/POST/PATCH /api/admin/users…, …/password | users: create, change role/ext, enable/disable, new password | R/W | ❌ | ❌ |
| GET/PUT /api/admin/settings | follow-up start date, KPI start date | R/W | ❌ | ❌ |
| GET /api/admin/log | admin log (last 200) | R | ❌ | ❌ |
| GET/POST /api/admin/api-clients…, …/disable | ERP keys: make / stop | R/W (person only) | ❌ | ❌ |
| GET /api/rules, /history, POST /test | rules page: read, history, test a sentence | R | ❌ | ❌ |
| POST /api/rules/lists/{name}, /topics, /recheck | rules page: change words / topics, re-check 7 days | W (person only) | ❌ | ❌ |

Admin accounts themselves are made only on the command line (the web page can never lock out the last admin).

## 4. The ERP (other system, API key)

How it works (spec `specs/2026-11-18-erp-api`): the ERP server sends `X-Api-Key` + `X-Act-As: <CallSight username>`.
The request gets **exactly the rights of that user** in the tables above. Every audit row (recording played,
transcript read, report downloaded) names that user.

What a key can **never** do, whoever it acts as ("person only" in the table):

| Action | Key answer |
|---|---|
| make or stop API keys | 403 |
| change the rules page (words, topics, re-check) | 403 |
| any request with a bad / stopped key, unknown or disabled user | 401 |

**Important:** a key acting as **admin** gets every other admin right too: read everything, create users, reset
passwords, change settings, and erase a call's content. If the ERP should only **read**, the ERP must act as a
**supervisor** account (reads every call, KPI, report, forward, live board; writes only follow-ups, notes,
shares and ratings), never as the admin. Which CallSight username the ERP uses is set on the ERP side.

## 5. What is written down (audit)

| What | Where |
|---|---|
| recording played, transcript read / exported, AI summary read | `recording_play` (user, call, kind, time) |
| PDF report downloaded | `report_download` |
| users, settings, keys, call content erased, rules changed / re-check | `admin_log` (never deleted) |
| rules page words and topics added / removed + reason | `rule_change` (never deleted) |
| Listen attempts (allowed and refused) | `listen_audit` |
