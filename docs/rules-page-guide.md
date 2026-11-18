# The rules page (القواعد): a simple guide

## 1. What the page does

The page controls the **automatic warnings** on calls.

1. Every call is turned into text automatically (the transcript).
2. The system looks for certain **words** in that text.
3. When it finds one, the call gets a **warning** on its call page.
4. A warning means: **"a supervisor should listen to this call"**. It does not prove anything by itself.

This page is where you choose **which words** give a warning.
Before, only the developer could change the words. Now the admin can do it here, with no code.

## 2. How to open it

1. Open https://calls.mohanadyehia.com and log in as **admin**.
2. On the main page (سجل المكالمات), look at the links at the top.
3. Click **القواعد**. It is between «النصوص المرجعية» and «حالة النظام».
4. Only the **admin** sees this link. If you don't see it, press **Ctrl + F5** once to load the newest version of the site.

Who can do what:

| Role | Can see the page | Can change words |
|---|---|---|
| Admin | Yes | Yes |
| Supervisor | No | No |
| Agent | No | No |

## 3. The word lists (first tab: قوائم الكلمات)

| List on the page | What it catches | Example words |
|---|---|---|
| كلمات مسيئة | Insults, by the agent or the customer | غبي، حمار، قليل ادب |
| تصعيد وشكوى | Wants the manager, a complaint, a refund, strong anger | ابي المدير، بشتكي، ابي فلوسي |
| منافسون | The customer mentions another institute | معهد ثاني، معاهد اخرى |
| اسمنا | Words after «معهد» that mean **our** institute, so **no** warning | السعودي |
| ليست اسم منافس | Words after «معهد» that are not a name, so **no** warning. Must start with «ال» | اليوم، الرسوم |

The competitor list also catches «معهد» + any name that starts with «ال» automatically
(for example «معهد الخليج»), unless that word is in «اسمنا» or «ليست اسم منافس».

### Why some words look different

Words are shown the way the system reads the transcript:

- No tashkeel (harakat): «غَبِيّ» becomes «غبي».
- All alef forms are one: «أبي» becomes «ابي».
- «ة» becomes «ه»: «غبية» becomes «غبيه».

This is normal. You can type the word in any form; the page cleans it for you.

## 4. How to change a list

**Remove a word** (for example, it gives false warnings):

1. Click the word. It turns red and crossed out.
2. Write a short reason in «السبب», for example «تحذيرات خاطئة».
3. Click **حفظ** (save).

**Add words:**

1. Type the new words in the «إضافة» box, **one word or phrase per line**.
2. Write a short reason.
3. Click **حفظ**.

Rules:

- A reason is required for every save.
- The same word cannot be added twice.
- At most 500 words in one list, and at most 40 letters in one word.
- Letters and spaces only (no numbers or symbols).

**When it takes effect:** new calls use the new words **within 1 minute**.

## 5. Test a sentence (اختبر جملة)

Type a sentence the way a customer might say it, for example «ابي المدير الحين», and click **اختبار**.
The page shows which list would give a warning. Nothing is saved.

Use it before adding or removing a word, to see the effect.

## 6. Re-check old calls (أعد الفحص لآخر 7 أيام)

New words only apply to **new** calls. To apply them to the calls of the **last 7 days** too:

1. Click **أعد الفحص لآخر 7 أيام**.
2. Wait a few minutes. The warnings on those calls are updated.

You can click it once every 5 minutes. It does not use the AI and does not change anything else.

## 7. Topics (second tab: المواضيع)

The AI writes a short summary of each call and picks **one topic** from this list
(for example: استفسار، شكوى، طلب صيانة، فاتورة / دفع، متابعة طلب، أخرى).

You can:

- **Rename a topic:** change the name in the table.
- **Add a topic:** type a key in English letters (for example `registration`) and its Arabic name.
- **Remove a topic:** tick «حذف».

Rules:

- «other» (أخرى) can never be removed.
- The key of a topic never changes after it is made; only its name can change.
- Old calls keep their old topic. Only new calls use the new list.

## 8. History (third tab: سجل التغييرات)

Shows every change: when, who, which list, words added, words removed, and the reason.
Nothing in the history can be deleted. Rows marked «ملف الإعداد» are the starting lists copied from the old files.

## 9. What we need from you (one time)

1. Open the page and read each list.
2. Remove words that do not fit your customers (words that give many false warnings).
3. Add words your customers really say when they are angry, want the manager, or mention another institute.
4. If you change words, click **أعد الفحص لآخر 7 أيام**.
5. Tell Claude **"lists ok"** when you are done (or if the lists are fine as they are).
