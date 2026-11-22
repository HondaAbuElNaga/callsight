# Test before going live (agent, supervisor, admin)

Pushing to `main` puts the code live at once. So every change is first tested on a **test copy on this PC**.
This page says how to start it, which accounts to use, and what to click for each role.

---

## 1. What the test copy is

| | Normal local copy | **Test copy** | Live site |
|---|---|---|---|
| Address | http://localhost:8080 | **http://localhost:8090** | https://\<SITE_NAME\> |
| Code | older code | **the code on disk now (with the new change)** | `main` |
| Database | local | **the same local database** | live |
| Sends to the ERP? | no | **no** (the link is off on this PC) | yes |

So: what you do on the test copy changes **only the local test database**. Nothing reaches the live site, the
customers or the ERP.

## 2. Start and stop it

Docker Desktop must be running, **and the normal local copy too** (`docker compose up -d`: the script reads its settings). In PowerShell, in the project folder:

```powershell
# start (takes 2-5 minutes: it builds the code)
powershell -ExecutionPolicy Bypass -File tools\local-preview.ps1

# stop it after testing
powershell -ExecutionPolicy Bypass -File tools\local-preview.ps1 -Stop
```

After every code change, run the start command again (it rebuilds).

## 3. The test accounts (this PC only)

| Username | Role | Extension |
|---|---|---|
| `test119` | agent | 119 (has 33 calls with a summary) |
| `testsup` | supervisor | – |
| `testadmin` | admin | – |

The passwords were given in the chat. Lost one? Make a new one:

```powershell
docker exec callsight-preview-api python -m callsight set-password testsup --generate
```

Tip: use **one browser window per role** (e.g. Chrome for the agent, a private window for the supervisor), so
you can see both sides at the same time.

---

## 4. Test as AGENT (`test119`)

| # | Do this | You should see |
|---|---|---|
| A1 | Log in | You land on «المتابعات». At the top only your name «test119 · تحويلة 119» and «خروج»: **no** links to other pages |
| A2 | Open any call of test119 that has a summary | Side panel opens and **fits**: no sideways scroll bar, the right edge is not cut |
| A3 | Look at «متابعة المكالمة» | «المطلوب منك: عبّئ نموذج المتابعة», the deadline, the form |
| A4 | Click **«املأ من ملخص المكالمة»** | «ماذا حدث؟» fills with the summary + action points; the note «راجع النص وعدّله قبل الحفظ» |
| A5 | Clear the box, type one word, click the button again | Button says «أضف ملخص المكالمة للنص»; your word stays, the summary is added below |
| A6 | Choose «هل حُلّت المشكلة؟ = لا» | Fields for who / expected time / callback appear, all inside the panel |
| A7 | Press «حفظ» with الأولوية empty | Red message «اختر الأولوية» (nothing saved) |
| A8 | Fill everything, press «حفظ» | «تم حفظ النموذج.» and the form shows in the history |
| A9 | Below the form | «ملخص المكالمة» box (summary only: **no** score, **no** flags) |
| A10 | Open a call with **no** summary yet | Button grey, with «الملخص قيد التجهيز…» next to it (or no button) |

## 5. Test as SUPERVISOR (`testsup`)

| # | Do this | You should see |
|---|---|---|
| S1 | Log in | You land on «سجل المكالمات». Links at the top: «المكالمات الحالية», «المتابعات», «المؤشرات», «التحويلات», «تقييم المكالمات»; **no** «القواعد», «حالة النظام», «المستخدمون» |
| S2 | «سجل المكالمات» → day 2026-11-14 → open the call the agent filled in A8 | «متابعة المكالمة» shows the agent's form (read only): **no** form, **no** «املأ من ملخص المكالمة» button, «المطلوب من الموظف: …» |
| S3 | Same panel | Full AI analysis (score, flags) and the transcript |
| S4 | Box «مشاركة المكالمة مع موظف» → إلى = `test119` → write a note → «إرسال» | The note appears: «لم يفتحها الموظف بعد» |
| S5 | Open a call nobody filled | «لم يعبّئ الموظف النموذج بعد.» |
| S6 | «المتابعات» page | All agents' forms, counters, filter by agent |

### The share round trip (supervisor ↔ agent)

| # | Who | Do this | You should see |
|---|---|---|---|
| R1 | agent | «المتابعات» → tab «ملاحظات المشرف» → open the call from S4 | «ملاحظة جديدة من testsup: اضغط لعرضها» |
| R2 | agent | Click it | The note text; the supervisor's side now says «فتحها الموظف …» |
| R3 | agent | Write a reply under 10 letters | Button stays off: «اكتب ردًا واضحًا (10 أحرف على الأقل).» |
| R4 | agent | Write a real reply → «إرسال الرد» | Reply saved; «إغلاق ونقل للأرشيف» appears |
| R5 | supervisor | Reload the call | «ردّ test119: …» |

## 6. Test as ADMIN (`testadmin`)

| # | Do this | You should see |
|---|---|---|
| D1 | Log in | Every supervisor link **plus** «النصوص المرجعية», «من يتكلم أولاً», «القواعد», «حالة النظام», «المستخدمون» |
| D2 | Open a call | Same as the supervisor (read-only form, share box, analysis) **plus** the red link «حذف التسجيل والنصوص لهذه المكالمة…» at the end (do **not** use it on the test unless you test deleting) |
| D3 | «المستخدمون» | The 3 test accounts; the settings (follow-up start 2026-11-01); «مفاتيح الأنظمة الأخرى» |
| D4 | «حالة النظام» | The health page loads (some jobs may show red on this PC: that is normal here) |

## 7. The access check (already run on 2026-11-22)

What the server answered for each role (200 = allowed, 403 = refused). Same as `docs/permissions.md`:

| Page / API | agent | supervisor | admin |
|---|---|---|---|
| call list `/api/calls` | 403 | 200 | 200 |
| one call's form `/api/calls/{id}/followup` | 200 (own) | 200 | 200 |
| forwards `/api/forwards` | 403 | 200 | 200 |
| rating `/api/rating/queue` | 403 | 200 | 200 |
| health `/api/health` | 403 | 403 | 200 |
| users `/api/admin/users` | 403 | 403 | 200 |
| rules `/api/rules` | 403 | 403 | 200 |
| shares `/api/shares` | 200 (own) | 200 | 200 |

## 8. What can NOT be tested on this PC (check on live after the push)

| What | Why | Check on live |
|---|---|---|
| Forward to the ERP (the «تحويل · <اسم الفرع>» names, «حفظ وتحويل إلى …», the ticket) | The ERP link is off on this PC (no key) | Open a call as an agent: the long list fits in the panel; pick a name: the button says «حفظ وتحويل إلى <name>» and the two time fields (وقت الحل المتوقع، وقت معاودة الاتصال) **disappear**; pick «العميل»: they come back |
| The training look-up box (student, suggested person) | Needs the training system | The box shows on a call from a student's mobile |
| New calls, live board | No PBX on this PC | «المكالمات الحالية» moves |

## 9. Go live

1. All the checks in 4, 5, 6 are OK.
2. Tell Claude **"ok push"** → it commits and pushes → the server deploys.
3. Do the checks in part 8 on the live site.
4. Stop the test copy (part 2).
