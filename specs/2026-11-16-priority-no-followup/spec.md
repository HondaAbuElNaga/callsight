# Spec: Priority on the follow-up form + "لا تحتاج متابعة" made visible

**Roadmap item:** Phase 7 follow-on (idea: user, 2026-11-16).
**Status:** parts 1–3 **done** (2026-11-16, branch `spec/2026-11-16-priority-no-followup`, not pushed yet) — part 4 (requirements 12, 16–21): API received 2026-11-16, reviewed below; missing parts M1–M4 asked from its developer

## Goal
1. Every problem the agent records gets a **priority** (عالية / متوسطة / منخفضة), so the supervisor sees what is urgent and,
   later, another employee in another system can pick the follow-ups up by priority. The stored value is a fixed English
   code so the other system never breaks when Arabic wording changes.
2. Calls the agent marks **"لا تحتاج متابعة"** are today only a row in the list (counted in "form on time", nowhere else).
   Make them visible: a counter + filter on المتابعات and a column per agent on the KPI page, so overuse of the button
   (to keep hard calls out of the KPIs) is easy to see.
3. A call whose problem is **not solved** goes to a side section **"تحتاج حل"** on المتابعات that does **not** follow the
   من/إلى dates: it stays there, whatever dates are chosen, until the problem is marked solved. Today such a call
   disappears from the list when the dates move past its day (only the "older_action" sentence is left).

4. **Forward the problem to a person in another system (user, 2026-11-16; WAITING for the other system's API, the user
   sends it 2026-11-17).** When the agent chooses "لا" (not solved), they pick **who must solve it** from a list of people
   that comes from the other system. That person then gets: priority, expected solve time, customer number, a summary
   of the complaint, and the agent's optional note to them. The call stays in "تحتاج حل" until **that person** marks it
   solved (the agent cannot close it).

## User decisions (2026-11-16)
- Priority is **required**.
- Set **once, in the first form** (not in later notes after a callback).
- The other system and how it gets the data: decided later (only keep the codes stable now).
- "لا تحتاج متابعة" → **no priority** (nothing to rank) — Claude's recommendation, user said "yes write spec for both".
- Answers to the open questions (2026-11-16):
  1. Filter by priority: **yes, now** (requirement 13).
  2. عالية = shorter solve time: **yes** → expected solve time ≤ **1 day** for عالية, ≤ 2 days for متوسطة/منخفضة
     (requirement 14).
  3. The forward person sees the problem **in their own system**.
  4. Supervisor wants the **whole pipeline in one simple line per problem**: agent, problem, priority, current step, who
     must solve it, solved or not (requirement 15). How "solved" comes back from the other system: decided with the API.
  5. The other system's API gives the names for the drop-down list; the user tells the **PBX extension of each person**.
  6. Agent's text at once + AI summary when ready: **yes**.
  7. 2-day limit + optional callback stay for forwarded problems: **yes** (1 day when عالية, see 2).
  8. **Both**: the forward person's "solved" click **and** a good call (answered, > 60 s, satisfaction > 7).
     The agent must see the solution status like **tracking a delivery** (requirement 17), and the forward person can
     **ask the agent questions** (requirement 18).
  9. **Show every call, the last call decides** (user, 2026-11-16: "i see what happend and check last"). Every call of
     the forward person to the customer is listed in the tracking view (time, answered, duration, score), so the
     supervisor and agent see what happened; the confirmation is judged on the **latest** call only. A bad call after a
     good one (e.g. a later short or unanswered call) makes the problem "غير مؤكد" again.
  10. **Use the current AI satisfaction score now** ("consider model as model until we finished"): the > 7 rule is on from
      the start with `llm_score`, and later uses the trained model. This changes decision 9 of the call-analysis spec
      (llm_score was never to be used for a decision) **only for this check**; it stays out of the KPI rates.
  11. Forward people **have PBX extensions**.
  12. Sending the customer number and summary to the other system: **allowed**.

## Scope
**In**
- Column `priority` on `call_followup` (migration 0015), codes `high` / `medium` / `low`.
- Form: required select "الأولوية" in the first form; server check.
- Priority shown in: follow-ups list, the call's follow-up panel (history round 1), the API.
- Counter + filter "لا تحتاج متابعة" on the follow-ups page.
- KPI page + PDF: per-agent column "لا تحتاج متابعة" (count and %).
- Side section "تحتاج حل" on المتابعات (independent of من/إلى).

**Out**
- Any link, export or push to the other system (later, user decides).
- Priority on notes (rounds ≥ 2), priority changing over time, priority in KPI rates (e.g. "high solved on time %").
- Filling a priority for old forms (they stay empty, shown "—").

## Requirements
1. **DB:** `call_followup.priority` `String(10)`, nullable (old rows and "لا تحتاج متابعة" rows are NULL), check constraint
   `priority IN ('high','medium','low') OR priority IS NULL`. Migration 0015, add-only, with downgrade.
2. **One label table** in `callsight/followups.py`: `PRIORITIES = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}`
   (order high → low). The web uses the same three labels (`format.ts`).
3. **Validation** (`followups.validate`): when `no_followup` is false, `priority` must be one of the three codes, else
   422 "اختر الأولوية". Required whether solved is yes or no. When `no_followup` is true, priority is stored as NULL
   (a sent value is ignored).
4. **Set once:** priority lives only on the first form. The note API (`NoteIn`) does not accept it (extra field → 422).
   While the first form can still be edited (same rules as today: before the deadline / before notes exist, or by a
   supervisor), the priority can be corrected with it.
5. **API out:** `form` in `/api/calls/{id}/followup` and `/api/followups` items carries `priority` (code or null) and
   `priority_ar` (Arabic label or null). History round 1 carries the same two fields.
6. **Form (web):** select "الأولوية" with اختر… / عالية / متوسطة / منخفضة, placed right after "ماذا حدث؟", required in the
   browser too (the save shows the same error message as the server).
7. **List (web):** follow-ups page shows the priority as a small tag next to the step (عالية = red, متوسطة = amber,
   منخفضة = grey); empty for old forms and "لا تحتاج متابعة".
8. **Follow-ups counter:** `counts.no_followup` = items in من/إلى with step `no_followup`; a new counter button
   "لا تحتاج متابعة" (neutral colour); clicking it shows only those calls (`state=no_followup`), clicking again goes back
   to "يحتاج عملاً". Also added to the state filter list.
9. **KPI:** per agent and in the total: `no_followup` (count) and `no_followup_rate` = no_followup ÷ (no_followup +
   answered_forms) = share of the filled forms. Shown as two columns "لا تحتاج متابعة" and "نسبتها" in the "مراحل
   المتابعة" table, and in the PDF follow-up stages table (the PDF shows follow-up KPIs only when they are active, as
   today). Show-only like the other follow-up numbers; it does not change any existing rate.
10. Retention: priority is part of `call_followup`, already erased with the call's content (nothing new).
11. **"تحتاج حل" section (user, 2026-11-16):**
    - Holds every call (from the follow-up start date, **no date limit**) whose latest entry says **not solved**: steps
      `waiting_callback`, `callback_missed`, `note_needed`, `no_number`, `waiting_solution`, `solution_overdue`
      (= today's `OPEN_STEPS`). A call leaves it only when a note marks it solved (or the call's content is erased).
    - **Does not change with من/إلى** or with the state filter / counters. It follows only "who": an agent sees only
      their own calls; a supervisor sees all team calls, or one agent when the الموظف filter is set.
    - Order: priority (عالية → متوسطة → منخفضة → none), then most urgent step (callback_missed, solution_overdue,
      note_needed, waiting_*, no_number), then oldest call first.
    - Each line: customer number, agent, call date, priority tag, step, and the next time (callback time or expected
      solve time). Clicking a line opens the call in the side panel, like the main list.
    - Heading "تحتاج حل (N)". Empty → "لا توجد مشاكل مفتوحة".
    - Place: a column on the side of the list on wide screens; above the list on phones.
    - API: `/api/followups` returns it as `unsolved: [...]` (same item shape as `items`), always, for any `from/to/state`.
    - The main list, its counters and the "older_action" sentence stay as they are.

12. **Forward to a person (part 4) — design waits for the API; what is fixed already:**
    - "من يجب أن يحلّها؟" offers the people from the other system's list (fetched through its API, kept up to date).
      Whether العميل / قسم آخر / our own users stay as choices: open question 5.
    - Saved with the form: the chosen person's id **from the other system** (stable id, not the name) + name for display,
      and an optional **note to that person** ("ملاحظة للمسؤول", ≤ 500 characters, may be empty).
    - Sent to that person: priority (`high`/`medium`/`low` + Arabic), expected solve time, customer number, complaint
      summary (open question 6), agent's note, agent name/extension, call time, a link to our call.
    - A forwarded problem is solved **only** when the forward person marks it solved; the agent can still write
      updates but cannot mark it solved. Until then it stays in "تحتاج حل" with step "عند المسؤول" (new step
      `forwarded`); after the expected solve time passes → "تأخر عند المسؤول" (`forward_overdue`).
    - We keep the time the person marked it solved (and their note, if the API gives one) and show it in the history.
    - If the other system cannot be reached when the agent saves, the form is still saved and the send is retried
      automatically; the line shows "لم يُرسل بعد" until it goes through.
    - Personal data (customer number, summary) leaves our system here: the user confirms this is allowed.
    - **Proof of solving (user, 2026-11-16):** a forwarded problem counts as **confirmed solved** only when, after it
      was forwarded, there is a call from the office phone system (PBX) **to the customer's number** that:
      (a) the customer **answered**, (b) lasted **more than 60 seconds** of talk, and (c) got a satisfaction score
      **above 7** (1–10).
      Facts and limits (to settle with the user, open questions 8–10):
      - We only see calls made **through our PBX**. The forward person needs a PBX extension we know (their other-system
        id ↔ extension); a mobile or another company's line is invisible to us.
      - The only satisfaction score today is the AI's first estimate (`llm_score`), which was decided to be **never used
        in KPIs** until checked against the user's ratings (roadmap steps 5–6, not done). On AWS it needs the GPU server
        (not set up), and comes ≈ 35 min after the call.
      - Which call counts if there are several, and what happens if the score is 7 or less: questions 9–10.
    - The rest of part 4 (endpoints, login method, how "solved" comes back) is written when the API arrives.

13. **Priority filter:** the follow-ups page gets a "الأولوية" filter (الكل / عالية / متوسطة / منخفضة), API
    `priority=high|medium|low`. It filters the main list **and** the "تحتاج حل" section; counters keep counting all.
14. **Solve time by priority:** expected solve time ≤ 1 day from saving when عالية, ≤ 2 days otherwise (error
    "أقصى وقت لحل مشكلة عالية الأولوية يوم واحد"). Applies to the form and to the notes after it (the priority of the
    call is the one from the first form). The callback limit stays 2 days.
15. **Supervisor pipeline line:** on المتابعات, for supervisor/admin, each line of the main list and of "تحتاج حل"
    reads in one row: agent · customer number · problem (agent's text, cut to one line, full on hover) · priority ·
    current step · who must solve it (our user / العميل / forward person) · state (مفتوحة / حُلّت / حُلّت غير مؤكدة).
    Agents keep their current simpler view.
16. **Forward people ↔ extensions:** a config file `config/forward_people.yaml` maps the other system's person id to
    the PBX extension the user gives. A person without an extension can still be chosen, but their calls can never
    confirm a solve (shown as "لا توجد تحويلة").

17. **Tracking view ("مثل تتبع الشحنة", user 2026-11-16).** In the call's panel (agent and supervisor), a forwarded
    problem shows a vertical step list, like a delivery tracker: done steps with a ✓ and their time, the current step
    highlighted, future steps grey. The **expected** solve time is shown apart from what really happened (a forecast is
    not a fact). Steps:
    1. سُجّلت المشكلة — agent, time, priority
    2. حُوّلت إلى «name» — sent time (or "لم يُرسل بعد، نحاول مرة أخرى")
    3. استلمها المسؤول — only if the other system tells us (depends on the API)
    4. اتصل المسؤول بالعميل — each call: time, answered or not, duration, satisfaction score
    5. حُلّت ✓ (confirmed) — or the reason in red: "حُلّت (غير مؤكد): المكالمة أقل من 60 ثانية" / "لم يرد العميل" /
       "الرضا 7 أو أقل" / "بانتظار تقييم الرضا"
    Late (expected time passed and not solved): the current step turns red with "متأخر".
    Questions and answers (requirement 18) appear inside the list at their time. The supervisor's one-line view
    (requirement 15) shows the current step of this same list.
18. **Questions between the forward person and the agent.** The forward person sends a question from their system
    (through our API); it appears to the agent as a new item in "تحتاج حل" ("سؤال من «name»") and in the step list.
    The agent answers in the panel (≤ 500 characters); the answer is sent back to the other system. Questions and
    answers are append-only, kept with the call (erased with its content). An unanswered question shows on the agent's
    counters ("أسئلة تنتظر ردك"). Whether the answer time counts in a KPI: later, not in this spec.

19. **After the forward person finishes (confirmed solved):** new step `forward_solved` "حلّها «name» ✓".
    - It **leaves "تحتاج حل"**.
    - It stays in the main المتابعات list on the call's day with that step; the state filter "حُلّت" includes it.
    - The tracking view keeps the whole story (all steps, calls, questions) with the final ✓ and the time.
    - The supervisor's one-line view shows state "حُلّت" and who solved it.
    - The agent's counters get "حُلّت عند المسؤول اليوم" (click = those calls), so the agent learns it is done.
    - "غير مؤكد" (clicked solved but the last call fails a rule) is **not** finished: it stays in "تحتاج حل".
    - KPI for forward people: requirement 20.
20. **Who caused the problem: agent or forward person (user, 2026-11-16: "to see problem from agent or forward person
    and collect mistakes of forward person to take action with him").** Show-only, like the other follow-up numbers.
    - **Fair time split:** while a question from the forward person waits for the agent's answer, the clock counts on
      the **agent**, not on the forward person. So "late" is blamed on whoever held the problem at that moment.
    - **Forward people table (KPI page):** per person: problems received, solved & confirmed, solved on time %,
      average solve time (their own time only), and **mistakes** (count).
    - **Mistakes of a forward person** (each one a line with call, time and reason):
      late (expected solve time passed while it was with them) · clicked solved but the last call failed
      (no call / not answered / ≤ 60 s / satisfaction ≤ 7) · no call to the customer at all.
    - **Mistakes on the agent's side** in the same view: question answered late (after 23:00 of the question's day, or
      2 h when asked after 21:00, like the form rule), wrong person chosen (the forward person sent it back — only if
      the API supports "send back").
    - **Mistakes list:** clicking a mistakes count opens the list of those cases (call, date, reason, link to the
      tracking view), so the supervisor can sit with the person with the facts. Date range = the KPI page's من/إلى.
    - Supervisor/admin only; agents and forward people do not see this table.

21. **Customer says "not solved" after the forward person finished (user, 2026-11-16).**
    - The agent can **re-send** the problem to the **same** forward person, **one time only**, with a short reason
      ("لم تُحل حسب العميل"). It goes back into "تحتاج حل" and the tracking view adds "أعيد فتحها: العميل اتصل" with
      the time; the forward person gets it again with the reason and the full story.
    - The first "solved" counts as a **mistake** of the forward person ("أعاد العميل فتحها").
    - **Second round fails** (late again, or not confirmed, or the customer says again it is not solved) → the problem
      goes **straight to the supervisor**: new step `escalated` "عند المشرف", shown on top of the supervisor's
      "تحتاج حل" in red, with the whole story. The agent can no longer re-send.
    - **Second false "solved" (user, 2026-11-16):** after the re-send, the forward person marks it solved again but
      the customer calls again and says it is still not solved → the agent has a button **"تحويل للمشرف"** (instead
      of re-send) that sends it straight to the supervisor (`escalated`), with the reason and the customer's call
      linked. This counts as a **serious mistake** ("خطأ جسيم: أغلقها مرتين والعميل لم تُحل مشكلته") for the forward
      person: its own column in the forward people table, shown in red, listed first in the mistakes list.
    - Open questions 12–14 (when the agent may re-send, what the supervisor can do).

## Part 4: the other system's API (received 2026-11-16, "API خدمة العملاء - تحويل المكالمات.pdf")
**What it gives** (base `https://api4.sstli.com`, JSON; answers from the PC with a valid certificate, 403 without a key):
1. `POST /api/userinfo/login-secure` {userName, password} → `guid` + `token` (`Authorization: Bearer`). Alternative for
   systems without a user: header `X-Api-Key: sk_...` made on their "المراقبة الأمنية" screen, limited to chosen paths.
2. `GET /api/customer-service/forward-targets?userGuid=` → tree men/women → branches → employees
   (`guid`, `name`, `jobTitle`, `isBranchSupervisor`); 474 people today (312 men's branches, 162 women's).
3. `POST /api/customer-service/tickets` {userGuid, **accountGuid (required)**, callType 0 general / 1 study /
   2 complaint, notes ≤ 590 chars, priority 1 low / 2 normal / 3 high / 4 urgent, close, forwardToGuid} →
   {guid, number, message}. Errors `{message}`: 401 token/userGuid, 403 no rights, 400 missing customer / text / type,
   400 person has no customer-service rights.

**User decision (2026-11-16): forward only to supervisors** ("only i need to send to المشرفين not all employee"):
the drop-down shows only employees with `isBranchSupervisor = true` (about one per branch, 19 branches today),
grouped men/women → branch. So the PBX extensions (req 16) are few and the user can give them by hand (M6 not needed).

**Fits the spec:** the drop-down list (req 12, open question 5 → the API list, supervisors only, by group → branch) and sending
the problem (priority, text, forward person). Mapping: high → 3, medium → 2, low → 1 (4 "عاجلة" not used).

**Missing (asked from the other system's developer; parts that need them wait):**
- M1 **Customer `accountGuid` from the phone number** (required to create a ticket; we only know the number).
  Blocks every send.
- M2 **Ticket status** (closed / solved, when, by whom, closing note): a GET by ticket guid, or a call to our address
  when it changes. Blocks "solved" (req 12, 17 step 5, 19, 20, 21).
- M3 **Questions and answers** on a ticket (read new questions, post the agent's answer). Blocks req 18.
- M4 **Add text to a ticket / reopen it** (AI summary later, re-send reason). Blocks req 6 update, req 21.
- M5 (nice) "received / opened" time → tracker step 3.
- M6 not needed (supervisors only): the user gives each supervisor's PBX extension in `config/forward_people.yaml`
  (req 16); only mapped people can confirm a solve.
- M7 An **API key** for our server (paths: forward-targets, tickets + M1–M4), and whether `userGuid` is still needed
  with the key. callType for our problems (complaint needs `complainSource`: its values?).

### API version 2 (received 2026-11-16, "API خدمة العملاء - تحويل المكالمات (2).pdf"): M1–M4 and M7 solved
Base `https://api4.sstli.com/api/customer-service`. Auth: `X-Api-Key` (made on their "المراقبة الأمنية" screen, only
the 7 paths below, kept on our server only); every request carries `userGuid` = a dedicated "PBX account" user in their
system with "مشرف خدمة العملاء" rights (tickets are recorded under that account).
- M1 → `POST /customers/by-phone` {userGuid, phone, createIfMissing} → accountGuid (any Saudi format; not found +
  createIfMissing=true → customer created with the number as name; >1 match → newest; bad number → 400).
- `GET /forward-targets` (as before; we keep only `isBranchSupervisor`), `POST /tickets` (callType 0 "general" is their
  advice for us, notes ≤ 590 required, close=false) → {guid, number}.
- M2/M3 → `GET /tickets/{guid}?userGuid=` → `ticket.closedAt` (not null = solved; last `followup_close` holds the
  solution text) + `timeline` (events created/forwarded/priority/reopened/closed; `followup` items = questions and
  replies, `actorName` = writer). No push to us: we read it every few minutes.
- M4 → `POST /tickets/{guid}/followup` {note, close:false} (AI summary, agent's answer; a closed ticket takes no text
  before reopen), `POST /tickets/{guid}/reopen` {note} (same person, notified), `POST /tickets/{guid}/forward`
  {toUserGuid, note} (another person).
- Not given: M5 "opened" time → tracker step 3 dropped. M6 extensions: not in their system → `config/forward_people.yaml`
  by supervisor guid (user gives them).
- Needed from the user before the first real call: the API key and the PBX account's userGuid (into the server `.env`).

### User decisions on API version 2 (2026-11-16)
- **"Received" step stays in the tracker, always shown** (user: "always appear; if solved see in place, if still see in
  place"). The API has no "opened" time, so the step is ticked at the supervisor's **first action** on the ticket (first
  `followup` item written by them, or the close), with that time; until then it stays in its place as the waiting step.
- **Forward list = only the 7 supervisors in `config/forward_people.yaml`** for now (branch, name, extension 101, 201,
  301, 401, 501, 601, 901); more when the user sends them. Their `guid` is matched by branch + name when the API key
  arrives.
- **Supervisors' calls (user said "ok", 2026-11-16):** today only calls that touch team extensions (104, 116, 119, 114)
  are saved, so a supervisor's call from 101 to the customer is **not stored** → no proof of solving. Part 4 must also
  keep calls from these 7 extensions to customers who have an open forwarded problem (and their recordings, for the
  satisfaction score > 7). That means branch supervisors' calls get recorded, transcribed and scored here. **Allowed by the user.**

## Part 4 full design (2026-11-16, approved by the user the same day)
**Settings (server `.env`, never in the code or the browser):** `CS_API_URL=https://api4.sstli.com/api/customer-service`,
`CS_API_KEY` (X-Api-Key), `CS_USER_GUID` (the PBX account). Empty key = part 4 off (the form works as today).

**D1 — Forward and send**
- People: `config/forward_people.yaml` (7 supervisors). A daily job (and a CLI `sync-forward-people`) reads
  `forward-targets`, fills each person's `guid` by branch + name into a table `forward_person` (guid, name, branch,
  ext, active); a person missing from the API turns inactive (cannot be chosen, old problems keep their name).
- Form: "من يجب أن يحلّها؟" gets a new kind **"مشرف فرع"** + a drop-down of the active supervisors (branch — name);
  العميل stays; our own users / قسم آخر stay for problems that are not forwarded. Optional "ملاحظة للمسؤول" ≤ 500.
- Saved on the form (migration 0016): `forward_person_id`, `forward_note`, `forward_status`
  (`pending` / `sent` / `failed`), `forward_ticket_guid`, `forward_ticket_number`, `forwarded_at`, `forward_error`.
- Send (worker job, right after the save, then retried every 5 min, up to 24 h): `customers/by-phone`
  (createIfMissing=true) → `tickets` {callType 0, priority high→3 / medium→2 / low→1, close=false, forwardToGuid,
  notes ≤ 590: customer number · priority · expected solve time · agent's text · agent's note · agent name/ext ·
  call time · ticket link to our call}. A 400 "person not in the forward list" or bad number → `failed` with the
  message, the line shows it and the agent picks again; 401/403 → stop all sends and warn the admin (bad key).
- The agent cannot mark a forwarded problem solved (notes can still be written; their text is added to the ticket).
- Steps: `forwarded` "عند المسؤول" (in "تحتاج حل"); expected time passed → `forward_overdue` "تأخر عند المسؤول".

**D2 — Status back + proof**
- Every 5 min the worker reads `GET tickets/{guid}` for open forwarded problems; stores the timeline items
  (table `forward_event`: kind, type, note, at, actor; new ones only) and `closedAt`.
- Supervisors' calls: ingest also keeps an **outbound** call from a `forward_person.ext` when its number matches a
  customer with a problem forwarded to that person (after `forwarded_at`). Stored with team `forward`, so they never
  appear in team lists, KPIs, the live board or reports; only in the tracker. Recording + transcript + score as usual.
- Solved check (req 12, Q8–Q9): closedAt set **and** the latest call of that supervisor to the customer after the
  forward was answered, > 60 s talk, `llm_score` > 7 → `forward_solved` (leaves "تحتاج حل"). Otherwise
  "حُلّت (غير مؤكد)" with the reason (no call / not answered / ≤ 60 s / score ≤ 7 / waiting for the score), stays.
  Note: on AWS the score needs the GPU server (not set up yet) → until then such problems wait at "بانتظار تقييم الرضا".

**D3 — Tracker + questions**
- Call panel, vertical step list (req 17): سُجّلت · حُوّلت (or "لم يُرسل بعد") · استلمها المسؤول (ticked at the
  supervisor's first action) · each call · حُلّت ✓ / reason in red; "متأخر" in red when late.
- Questions (req 18): a timeline `followup` item written by anyone other than the PBX account = a message from the
  supervisor → "سؤال من «name»" in "تحتاج حل" + counter "أسئلة تنتظر ردك"; the agent's answer (≤ 500) is posted with
  `tickets/{guid}/followup` (close=false). Closed ticket: the answer waits until reopen (shown).
- AI summary (Q6): when the call's summary exists, it is added once to the ticket the same way.
- Supervisor one-line view (req 15): "who solves it" = supervisor name; state from the same steps.

**D4 — Re-send, escalation, KPI**
- Re-send (req 21, Q12–Q13): after the expected time passed, once, reason required → `tickets/{guid}/reopen`.
  Second failure → `escalated` "عند المشرف" for our supervisor, who closes it here, picks agent or forward person as
  the cause (+ note); the ticket is then closed there with `followup` close=true and that note.
- KPI page (req 20): forward people table + mistakes lists, agent's "أخطاء بقرار المشرف"; supervisor/admin only.

**Tests:** the other system is faked in tests (no real call); one manual check with the real key on the server
(sync people, one test ticket to a supervisor who knows it is a test) before going live.

## Build decisions (3-agent reviews per part, 2026-11-16)
- An **unchanged** expected solve time is not re-checked against the 2-day limit when a form is saved again (forms from
  before the 2-day rule may hold a later time); the 1-day limit of عالية is checked every time.
- The priority filter does not apply to the "لا تحتاج متابعة" view (those forms have no priority, the list would be
  empty under a non-zero counter).
- "تحتاج حل" is bounded in SQL: at most 2 × 500 open problems are loaded (by priority, oldest first), sorted by urgency,
  first 500 shown ("+" in the heading when cut). The priority filter runs in SQL.
- The side column is narrow, so each "تحتاج حل" line is a small stacked card, not one table row. The agent's name and
  the supervisor fields (problem, who solves it, state "مفتوحة") are shown to supervisors/admins only. `no_number`
  lines show the callback time; `note_needed` lines show the note deadline.

## Acceptance criteria
- [x] Tests: form without priority → 422; bad code → 422; each code saves and comes back with the right `priority_ar`;
      `no_followup` with a priority sent → stored NULL; note with `priority` → 422; supervisor edit can change it.
- [x] Tests: `/api/followups?state=no_followup` returns only those calls; `counts.no_followup` matches; agent sees only
      their own.
- [x] Tests: KPI `no_followup` and `no_followup_rate` per agent and total (e.g. 1 no-follow-up + 3 problem forms → 25 %);
      zero forms → rate null; existing KPI numbers unchanged on the old test data.
- [x] Tests: an unsolved call from 30 days ago is in `unsolved` for `from=to=today` and for any other dates; a call
      solved by a note leaves it; `no_followup` and first-call-solved calls are never in it; agent sees only their own,
      supervisor with `agent=` sees only that agent; order = priority, then urgency, then oldest.
- [x] PDF follow-up stages table has the two new columns (test on the HTML).
- [x] Migration 0015 up → down → up OK; old forms have NULL priority. → scratch DB, 1 old form: NULL after upgrade;
      check constraint refuses `urgent`.
- [x] Full pytest green (except the known `.env` test); web build OK. → 705 passed, 1 failed
      (`test_llm_config_from_env`, local `.env`), 9 skipped; `tsc -b && vite build` OK.

## Technical approach
- Backend: `models.py` (+column), migration `0015_followup_priority.py`, `followups.py` (PRIORITIES, validate, form_dict,
  history, kpi `_empty` + counting), `api/work.py` (`FollowupIn.priority`, `counts`, `STATES` + filter),
  `reports/pdf.py` (two cells).
- Web: `api.ts` types, `format.ts` labels/tones, `WorkPanels.tsx` (select in the first form only — `ProblemFields` is
  shared with the note, so the select is added next to it in the form, not inside it), `FollowupsPage.tsx`
  (counter, filter option, tag), `KpiPage.tsx` (two columns + one line in the explanation text).
- "تحتاج حل": first a small SQL query finds the call ids whose form is not solved and has no note marked solved (and is
  not `no_followup`), limited to the allowed extensions; then the existing `fu.rows(..., call_ids=...)` builds the
  items, so steps are computed exactly as in the main list. Cheap even after months (only open calls are loaded).
  Web: `FollowupsPage.tsx` side column + CSS (grid on wide screens, stacked on phones).
- Risk: old forms without priority — an edit of such a form now needs a priority (correct: the form is being saved
  again). Old rows still show and count normally.
- No new tools.

## Open questions (with recommendation)
1. Can the supervisor see/filter the list by priority now? **Recommend: not now** (scope out), add later if needed.
2. Should "عالية" change any deadline (e.g. solve within 1 day instead of 2)? **Recommend: no**, priority is
   information only for now.
3. Where does the forward person see the problem: in **their own system** (we send it through its API) or by logging
   in to our site? **Recommend: their own system** (that is why the codes are fixed).
4. How do we learn "solved": the other system calls our address when it is solved, or we ask it every few minutes?
   **Recommend: they call us** (instant); asking every 5 minutes if their API cannot. Depends on the API.
5. "من يجب أن يحلّها؟": only the forward list, or the forward list **plus** العميل / قسم آخر? **Recommend: forward list +
   العميل** (some problems wait on the customer, e.g. sending a document); drop "قسم آخر" and our own users, since the
   forward list covers them.
6. Complaint summary: the AI summary needs the call's transcript, which on AWS needs the GPU server (not set up yet) and
   comes ≈ 35 min after the call. **Recommend: send the agent's "ماذا حدث؟" text at once, and add the AI summary
   when it is ready** (sent as an update).
7. Do the 2-day solve limit and the optional callback stay for forwarded problems? **Recommend: yes**, both unchanged.
8. Proof call vs. the person's "solved" click: **Recommend: both.** The person marks solved in their system **and** our
   system checks the call. Click without a good call → step "حُلّت (غير مؤكد)", still listed in "تحتاج حل" with the
   reason (no call / not answered / under 60 s / score ≤ 7). Good call without a click → "تم الاتصال، بانتظار
   تأكيد المسؤول".
9. Several calls to the customer: **Recommend:** the latest answered call over 60 s after the forward time counts.
10. *(Answered: use the current AI score now, see "User decisions".)* Old recommendation was: build the call + 60 s check now; show the AI score next to it as "تقدير أولي"
   but do not block on it until the satisfaction model is checked (step 6). Then switch the > 7 rule on with one
   setting. Score ≤ 7 then → back to "تحتاج حل" as "العميل غير راضٍ" for the forward person to try again.
11. KPI of forward people: **yes** (user, 2026-11-16), with the mistakes list → requirement 20.
13. *(Answered 2026-11-16)* Re-send window = **2 days** after "solved". If the customer called (said not solved) and
    the agent did not re-send / send to the supervisor within those 2 days → **agent mistake**, lowers the agent's KPI.
    After 2 days the button is gone. The page shows a short note explaining this rule next to the button.
14. *(Answered 2026-11-16)* The supervisor does **not** send it to anyone else: the supervisor only **closes** it, and
    when closing picks **who made the mistake: the agent or the forward person** (+ a note). That choice counts in that
    person's KPI (agent: new column "أخطاء بقرار المشرف"; forward person: in the mistakes list).
12. *(Answered 2026-11-16: "re-send appears when time of solution is finished")* The re-send button (and later
    "تحويل للمشرف") appears only **after the expected solve time has passed**, no customer call needed; the 2-day window
    of question 13 starts then. A reason is required when re-sending (it is the only record of why).
    Old recommendation, not chosen: re-send needs proof the customer called? **Recommend: yes** — the button shows only when the same customer number
    called the team (answered) after the "solved" time, and that call is linked in the story. Stops re-sends that the
    customer never asked for.
