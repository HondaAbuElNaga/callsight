# The follow-up form (متابعة المكالمة) and the forward to the other system

This page explains, step by step:

1. What happens when the agent opens a call and fills the form
2. What the «حفظ» button does (and how it differs from «مشاركة»)
3. Exactly what JSON we send to the customer-service system (api4.sstli.com)
4. What happens after the ticket is made (24 h chain)
5. The new features you asked for, and the questions still open

---

## 1. Where the code is

| Part | File | What it does |
|---|---|---|
| Screen (what the agent sees) | `web/src/WorkPanels.tsx` → `FollowupPanel` (line 307) | The form, the notes box, the history |
| Where the panel opens | `web/src/CallsPage.tsx` → `DetailPanel` (line 296) | The side panel that opens when you click a call |
| Panel size | `web/src/style.css` line 104 | Fixed panel on the left, 380px wide |
| Server routes | `callsight/api/work.py` (line 143–219) | `GET` / `PUT /api/calls/{id}/followup`, `POST /followup/notes` |
| Rules (checks) | `callsight/followups.py` → `validate()` (969), `save()` (1058) | Every rule is checked again on the server |
| Building the ticket | `callsight/forward.py` → `ticket_notes()` (290), `send_pending()` (315) | Makes the ticket text and sends it |
| Talking to the other system | `callsight/cs_api.py` | The HTTP calls to api4.sstli.com |

---

## 2. Which calls need the form

A call needs the form only when **all** of these are true (`followups.py:143`, `is_needed`):

- Inbound call (العميل اتصل بنا)
- Answered
- The agent's extension is in a team (`config/teams.yaml`)
- Talk time **more than 20 seconds**
- After the start date set on the settings page
- Its content was not deleted

Calls that do not match → no form is shown.

---

## 3. What the agent fills, step by step

When the agent clicks a call in the list, the side panel opens. The follow-up box is the **first** box in it
(so the agent sees what to do first).

### Option A: no follow-up needed
Button: **«لا تحتاج متابعة (رقم خطأ، استفسار انتهى…)»**
→ saved at once, nothing else to fill.

### Option B: the normal form

| Field | Required? | Rule |
|---|---|---|
| ماذا حدث؟ | Yes | Max 500 characters |
| الأولوية (عالية / متوسطة / منخفضة) | Yes | Form only (notes have no priority); the agent can change it when editing the form |
| هل حُلّت المشكلة؟ (نعم / لا) | Yes | |

If **نعم** (solved) → the form is complete. Press «حفظ».

If **لا** (not solved), more fields appear:

| Field | Required? | Rule |
|---|---|---|
| من يجب أن يحلّها؟ | Yes | A user, العميل, قسم آخر, **or a person to forward to** (تحويل · <اسم الفرع>). One search box (2026-11-28): type a name or branch to filter the list (max 80 rows shown), ↑/↓ + Enter or click to pick |
| ملاحظة للمسؤول | No | Only when forwarding, max 500 |
**No times at all (2026-11-23, user decision):** the two fields «وقت الحل المتوقع» and «وقت معاودة الاتصال» are
removed from the form and the notes, for every choice (not only «تحويل»). The server stores no expected / callback
time and ignores any sent by an old page; old rows keep theirs (their steps and KPI numbers still count). Effect:
a not-solved problem with «العميل» / «قسم آخر» / a user stays «بانتظار الحل» until a note closes it (never «فات موعد
الحل»), and no new callbacks enter the callback KPI, so the callback columns are gone from the KPI page and the PDF.
A forward still gets **save time + 24 h** from the server (the chain's own time). Code: `followups.validate()`.

**Suggestion:** if the caller's phone is found in the training system, the form shows the student and
**pre-picks** the trainer or the branch supervisor. The agent can change it.

### Time rules (deadline)

- The form must be filled **by 21:30 Riyadh on the call's day** (the work day ends at 21:30).
  If the call ended at/after 21:30 → **by 21:30 the next day** (spec 2026-11-23-agent-kpi; calls before
  2026-11-24 keep the old rule: 23:00, or 2 hours after a call that ended after 21:00).
- After the deadline: a first form can still be saved, but it **counts as late**.
- After the deadline (or after the callback time passed): the agent **cannot edit** an existing form.
  (The server would accept a supervisor's save, but the screen never shows supervisors the form.)
- After the problem was forwarded successfully: **nobody** can edit the form any more.
- If the forward **failed**: the form opens again so the agent picks another way.

### Who can fill it

- Only the **agent of the call**.
- Supervisors and admins only **see** it (no form, no buttons).

---

## 4. The «حفظ» button: save or share or forward?

| Button | Who sees it | What it does |
|---|---|---|
| «حفظ» (in the agent's form) | the agent | Saves the form. **If** a name under «تحويل · <اسم الفرع>» is picked, the same click also sends the problem to the ERP as a ticket |
| «إرسال» (box «مشاركة المكالمة مع موظف») | supervisor, admin | Sends a note about the call to an agent, inside our system only. **Never** goes to the ERP |

No new button was added for sending: «حفظ» already did it before 2026-11-22.

**«حفظ» = save the form.** There is only one button on purpose.

- If the agent picked a normal person / العميل / قسم آخر → it is only saved in our system.
- If the agent picked **a person under «تحويل · <اسم الفرع>»** → the same «حفظ» **also sends the problem to the
  other system** (a ticket is made and given to that person).

How the send works:

```
Agent presses «حفظ»
   │
   ▼
Server saves the form   (forward_status = "pending")
   │
   ▼
Server tries to send right away  (api/work.py:198)
   │
   ├── OK      → forward_status = "sent", ticket number saved, the 24 h chain starts
   ├── Busy / network error before the ticket is made → tried again every 5 minutes, for up to 24 h
   ├── Busy (408/429) on the ticket request → tried again too
   ├── Server error / timeout on the ticket request → "failed", NOT resent (the ticket may already exist: check the ERP first)
   └── Refused (bad number, person not allowed …) → "failed", the form opens again for the agent
```

Since 2026-11-22 the button **says** what it will do: with a «تحويل» person picked it reads
**«حفظ وتحويل إلى <الاسم>»**, with the line «عند الحفظ تُرسل المشكلة إلى نظام خدمة العملاء (تذكرة) ولا يمكن تعديل
النموذج بعدها.» Otherwise it reads «حفظ».

**«مشاركة» is something else.** It is a supervisor sending a note about a call to an agent inside our system.
It never goes to the other system.

---

## 5. Exactly what we send to the other system (JSON)

- Address: `https://api4.sstli.com/api/customer-service`
- Header on every request: `X-Api-Key: <CS_API_KEY>`
- The key lives only in the server `.env` (`CS_API_KEY`, `CS_USER_GUID`). It never goes to the browser.
- `userGuid` = the "PBX account": every action in their system is recorded under this account.

### Step 0: the list of people (filled the forward list)

```http
GET /forward-targets?userGuid=<CS_USER_GUID>
```

Answer (shape):

```json
{
  "groups": [
    {
      "key": "men",
      "branches": [
        {
          "name": "فرع الرياض",
          "employees": [
            { "guid": "aaaa-1111", "name": "محمد أحمد", "isBranchSupervisor": true },
            { "guid": "bbbb-2222", "name": "خالد علي",  "isBranchSupervisor": false }
          ]
        }
      ]
    }
  ]
}
```

We store these people in our table `forward_person`. The agent picks one of them in the form.

### Step 1: find (or create) the customer by phone

```http
POST /customers/by-phone
```

```json
{
  "userGuid": "<CS_USER_GUID>",
  "phone": "0551234567",
  "createIfMissing": true
}
```

Answer:

```json
{ "accountGuid": "cccc-3333" }
```

If the customer is not there, they create one (name = the number).

### Step 2: make the ticket and give it to the person

```http
POST /tickets
```

```json
{
  "userGuid": "<CS_USER_GUID>",
  "accountGuid": "cccc-3333",
  "callType": 0,
  "priority": 3,
  "close": false,
  "forwardToGuid": "aaaa-1111",
  "notes": "مشكلة محوّلة من خدمة العملاء - الأولوية: عالية\nرقم العميل: 0551234567\nوقت المكالمة: 2026-11-22 10:15\nالمهلة: 24 ساعة عند كل مسؤول من وقت وصولها إليه\nالموظف: أحمد (201) - رقم المكالمة عندنا: 12345\nما حدث: <ما كتبه الموظف>\nملاحظة للمسؤول: <الملاحظة إن وجدت>"
}
```

Answer:

```json
{ "guid": "dddd-4444", "number": 5521 }
```

What each field means:

| Field | Value | Where it comes from |
|---|---|---|
| `accountGuid` | the customer | Step 1 |
| `callType` | `0` = general inquiry | Fixed (their advice for the PBX) |
| `priority` | high = `3`, medium = `2`, low = `1` | The form's الأولوية |
| `close` | `false` = open ticket | Fixed |
| `forwardToGuid` | the chosen person | The form's «من يجب أن يحلّها؟» |
| `notes` | the text above, **max 590 characters** | Built by `ticket_notes()` (`forward.py:290`) |

How `notes` is built: the fixed lines first (priority, phone, time, solve-by, agent, our call number),
then «ما حدث» and «ملاحظة للمسؤول» share the space left. If too long, the end is cut with «…».

The ticket number (`5521`) is shown to the agent: «حُوّلت إلى … · تذكرة رقم 5521».

---

## 6. After the ticket is made: the 24 h chain

Every 5 minutes the server reads the ticket back and moves it when needed:

| Request | When |
|---|---|
| `GET /tickets/{guid}?userGuid=…` | Read the status (closed? questions? answers?) |
| `POST /tickets/{guid}/forward` `{"userGuid", "toUserGuid", "note"}` | The person did not solve it in 24 h → move to the **branch supervisor**, then to **management** (الإدارة) |
| `POST /tickets/{guid}/followup` `{"userGuid", "note", "close"}` | Write the agent's answer or note on the ticket |
| `POST /tickets/{guid}/reopen` `{"userGuid", "note"}` | Open a closed ticket again (e.g. to write a new answer) |

```
Employee (24 h) ──not solved──► Branch supervisor (24 h) ──not solved──► Management (last level)
```

- Each person has **24 clock hours**.
- The chain starts at the level of the person the agent picked (picking a branch supervisor starts at level 2).
- At management, ONE manager gets the same ticket (2026-11-28): a women's branch -> the `side: women` manager in the yaml; any other branch -> the other managers in turn. (Older problems: each other manager got a **copy ticket**, a new `POST /tickets`.)
- When the branch supervisor closes it, we check there is a **real call to the customer**: answered,
  more than 60 seconds, satisfaction more than 7 → «مؤكد ✓», otherwise «غير مؤكد».
- If the person asks a question on the ticket, the agent sees «سؤال ينتظر ردك» and can answer;
  the answer is written on the ticket.

### Supervisor close (user decision 2026-11-22, migration 0029)

On the problem's story (page «التحويلات», or the KPI page), a supervisor or admin presses
**«إغلاق: حلّها هذا الشخص»** on anyone who really got it (employee / branch supervisor / management), then
«نعم، أغلقها». No reason, no proof call needed: the decision replaces the call check.

- That person gets it as **solved**; «حلّ خلال 24 ساعة» only if his own time was within 24 h (his own close
  time if he closed it, else the time of the decision for the one holding it, or the time it passed on).
- The others keep their marks: whoever was already late stays late; the one holding it now ends «حلّها غيره».
- The chain stops at once in our system (`forward.decide`, row lock on the form; busy → «حاول بعد دقيقة»).
- The worker closes every open ticket in the ERP (`forward.close_decided`, `followup(close=True)` with
  «أُغلقت بقرار مشرف خدمة العملاء: حلّها «الاسم»»): tried every 5 minutes for 3 days; refused → «تعذّر إغلاق التذكرة»
  in the story, close it there by hand.
- The story shows «بقرار <username>» and the time; every decision is in the admin log (`forward_decide`).
- The problem's solve time (list, averages) is the decision time.

---

## 7. New feature 1: fill «ماذا حدث» from the call summary

**Idea:** a button **«املأ من الملخص»** next to «ماذا حدث؟». One click copies the AI summary of the call
(and its action points) into the box.

How it would work:

1. The panel already loads the summary (`AgentSummaryPanel`, `/api/calls/{id}/summary`).
2. The button takes `summary` + `action_items`, joins them, cuts to 500 characters, and puts them in the box.
3. The agent reads it, can change it, then presses «حفظ» → it is saved and (if a forward person is picked)
   sent to that person as «ما حدث» in the ticket.

**Limit:** the summary exists only **after** the call is transcribed and analysed. Until then the panel shows
a greyed-out «املأ من ملخص المكالمة» button with the text «الملخص قيد التجهيز…» next to it.

### Built (2026-11-22): option A

- Button under «ماذا حدث؟»: **«املأ من ملخص المكالمة»**.
- Text = the summary, then each action point on its own line (`- …`), cut to 500 characters (ends with «…» if cut).
- Box already has the agent's text → the button says **«أضف ملخص المكالمة للنص»** and adds the summary **below**
  (the agent's text is never erased).
- After the click: «راجع النص وعدّله قبل الحفظ: يُحفظ ويُرسل كما هو.»
- Summary not ready → the button is grey, with «الملخص قيد التجهيز…» next to it. No analysis / analysis error → no button.
- Only on the first form (not on the notes after a callback: those are about another call).
- Nothing is sent by the button. «حفظ» still does the save (and the forward, when a forward person is picked).
- Code: `FillFromSummary`, `summaryText`, `fillWhat` in `web/src/WorkPanels.tsx`.

### The choice that was made

| Option | What happens | Risk |
|---|---|---|
| **A (recommended)** | The button only fills the text. The agent checks it, then presses «حفظ». | None: the agent stays responsible for what is sent |
| B | One click fills **and** sends to the forward person at once | A wrong or weak AI summary reaches the other system with nobody checking it |

---

## 8. Feature 2: "the distance is not right" when the agent clicks a call

Now: clicking a call opens a **side panel fixed on the left**, **380px wide** (`style.css:104`).
Inside it, from top to bottom:

1. Call details (time, number, status, agent, durations)
2. ▶ تشغيل التسجيل
3. **متابعة المكالمة (the form)**
4. ملخص المكالمة
5. Shares
6. The transcript

### Found from the screenshot and fixed (2026-11-22)

The problem was not the order: the panel's content was **wider than the panel** (sideways scroll bar, right edge cut:
«تفاصيل» showed as «اصيل»). Cause: the form is a `<fieldset>`, and a browser makes a fieldset at least as wide as
its widest content. The «من يجب أن يحلّها؟» list holds ~474 long names (name + branch + «(مشرف الفرع)»), so the list,
the fieldset and the whole panel grew wider than the screen.

Fix (`web/src/style.css`): the form may shrink (`min-width: 0`), every field takes the panel's width
(`width: 100%`), the panel never scrolls sideways, and long words wrap. Long names in the list are cut by the browser
inside the box; the full name is seen when the list opens.

### The options that were listed before the screenshot

- (a) The panel is **too narrow** → make it wider (for example 520px, or half the screen)?
- (b) The agent must **scroll too far** to reach the form → put the form at the very top, above the call details?
- (c) The summary is **below** the form, far from «ماذا حدث» → move the summary above the form?
- (d) Something else → please send a screenshot.

---

## 9. Summary

- «حفظ» saves; with a «تحويل» person picked, it also sends the ticket. No other button is needed.
- We send 2 requests: `POST /customers/by-phone`, then `POST /tickets` (JSON above).
- Then every 5 minutes: read the ticket, move it after 24 h, write answers on it.
- New button «املأ من ملخص المكالمة»: built (part 7).
- The "distance" fix: built (part 8).
