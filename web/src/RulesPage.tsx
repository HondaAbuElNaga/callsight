// Rules page (spec 2026-11-17-rules-editor): word lists + topics. Admin edits, supervisor reads.
import { useCallback, useEffect, useState } from "react";
import { api, Refused, Unauthorized, type Me, type RuleChangeRow, type RuleHit, type RuleListName, type RulesPage as Rules } from "./api";
import { formatDateTime } from "./format";

interface Props { me: Me; onBack: () => void; onLogout: () => void; onExpired: () => void }

const LISTS: { name: RuleListName; title: string; help: string }[] = [
  { name: "bad_words", title: "كلمات مسيئة", help: "كلمة أو عبارة مسيئة في المكالمة." },
  { name: "escalation", title: "تصعيد وشكوى", help: "طلب المدير، شكوى رسمية، استرجاع المبلغ، غضب شديد." },
  { name: "competitor", title: "منافسون", help: "عبارات تدل على منافس (الأسماء التي لا تبدأ بـ «ال»)." },
  { name: "competitor_own", title: "اسمنا", help: "كلمة واحدة: «{w}» + هذه الكلمة = نحن، لا تنبيه." },
  { name: "competitor_ignore", title: "ليست اسم منافس", help: "كلمة واحدة تبدأ بـ «ال»: «{w}» + هذه الكلمة لا تعني منافسًا (اليوم، الرسوم…)." },
];
const LIST_TITLE: Record<string, string> = { ...Object.fromEntries(LISTS.map((l) => [l.name, l.title])), topics: "المواضيع" };
const KIND_TITLE: Record<string, string> = { bad_words: "كلمات مسيئة", escalation: "تصعيد وشكوى", competitor: "منافسون" };

export function RulesPage({ me, onBack, onLogout, onExpired }: Props) {
  const [rules, setRules] = useState<Rules | null>(null);
  const [history, setHistory] = useState<RuleChangeRow[]>([]);
  const [tab, setTab] = useState<"lists" | "topics" | "history">("lists");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else if (e instanceof Refused) setError(e.message);
    else setError("تعذر الاتصال بالخادم. أعد المحاولة.");
  }, [onExpired]);
  const reload = useCallback(() => {
    api.rules().then(setRules).catch(fail);
    api.rulesHistory().then(setHistory).catch(fail);
  }, [fail]);
  useEffect(() => { reload(); }, [reload]);

  const run = async (f: () => Promise<unknown>, done?: string) => {
    setError(""); setInfo("");
    try { await f(); if (done) setInfo(done); reload(); return true; } catch (e) { fail(e); return false; }
  };

  return (
    <div className="page">
      <header className="topbar">
        <h1>القواعد</h1>
        <div className="who">
          <button className="link" onClick={onBack}>سجل المكالمات</button>
          <span className="muted">{me.username}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>
      <p className="muted small">
        هذه القوائم تُطابَق على نص المكالمة (بدون تشكيل، والهمزات وة/ه وى/ي موحّدة؛ لذلك تظهر الكلمات بهذا الشكل).
        كل تنبيه يعني «تحتاج استماع» فقط. التغيير يُطبَّق على المكالمات الجديدة خلال دقيقة.
        {rules && !rules.can_edit && " (للعرض فقط: التعديل للمدير.)"}
      </p>
      {error && <div className="error">{error}</div>}
      {info && <div className="ok-msg">{info}</div>}
      <div className="chips" role="tablist">
        {([["lists", "قوائم الكلمات"], ["topics", "المواضيع"], ["history", "سجل التغييرات"]] as const).map(([k, t]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`chip${tab === k ? " on" : ""}`} onClick={() => setTab(k)}>{t}</button>
        ))}
      </div>
      {!rules ? <div className="center muted">…</div> : tab === "lists" ? (
        <>
          <SentenceTest onFail={fail} />
          {rules.can_edit && (
            <section className="panel">
              <h2>أعد الفحص</h2>
              <p className="muted small">يطابق القوائم الحالية من جديد على مكالمات آخر {rules.recheck_days} أيام (بدون الذكاء الاصطناعي). مرة كل 5 دقائق.</p>
              <button disabled={!rules.recheck_enabled} onClick={() => run(() => api.rulesRecheck(), "بدأ الفحص في الخلفية؛ تتحدث التنبيهات خلال دقائق.")}>
                أعد الفحص لآخر {rules.recheck_days} أيام
              </button>
            </section>
          )}
          {LISTS.map((l) => (
            <WordList key={l.name} name={l.name} title={l.title} help={l.help} items={rules.lists[l.name] ?? []}
              rules={rules} run={run} />
          ))}
        </>
      ) : tab === "topics" ? (
        <Topics rules={rules} run={run} />
      ) : (
        <History rows={history} />
      )}
    </div>
  );
}

type Run = (f: () => Promise<unknown>, done?: string) => Promise<boolean>;

function WordList({ name, title, help, items, rules, run }: {
  name: RuleListName; title: string; help: string; items: string[]; rules: Rules; run: Run;
}) {
  const [remove, setRemove] = useState<Set<string>>(new Set());
  const [add, setAdd] = useState("");
  const [reason, setReason] = useState("");
  const newWords = add.split("\n").map((w) => w.trim()).filter(Boolean);
  const changed = newWords.length > 0 || remove.size > 0;
  const toggle = (w: string) => setRemove((r) => { const n = new Set(r); if (n.has(w)) n.delete(w); else n.add(w); return n; });
  const save = async () => {
    if (await run(() => api.changeRuleList(name, newWords, [...remove], reason.trim()), `حُفظت قائمة «${title}».`)) {
      setRemove(new Set()); setAdd(""); setReason("");
    }
  };
  return (
    <section className="panel">
      <h2>{title} <span className="muted small num">{items.length} / {rules.limits.items}</span></h2>
      <p className="muted small">{help.replace("{w}", rules.competitor_word ?? "")}{name === "competitor_own" || name === "competitor_ignore"
        ? ` يظهر التنبيه باسم «${rules.competitor_label ?? ""}».` : ""}</p>
      <div className="chips">
        {items.length === 0 && <span className="muted small">القائمة فارغة.</span>}
        {items.map((w) => rules.can_edit ? (
          <button key={w} className={`chip rule-word${remove.has(w) ? " removing" : ""}`} onClick={() => toggle(w)}
            title={remove.has(w) ? "تراجع عن الحذف" : "حذف عند الحفظ"} aria-pressed={remove.has(w)}>
            {w} <span aria-hidden="true">{remove.has(w) ? "↺" : "×"}</span>
          </button>
        ) : <span key={w} className="chip rule-word">{w}</span>)}
      </div>
      {rules.can_edit && (
        <form className="rule-edit" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <label>إضافة (كل سطر كلمة أو عبارة، حتى {rules.limits.letters} حرفًا)
            <textarea rows={2} value={add} onChange={(e) => setAdd(e.target.value)} /></label>
          <label>السبب (مطلوب)
            <input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} placeholder="مثال: طلب المدير" /></label>
          <div className="filter-actions">
            {remove.size > 0 && <span className="muted small">سيُحذف {remove.size}</span>}
            <button className="primary" disabled={!changed || reason.trim().length < 3}>حفظ</button>
          </div>
        </form>
      )}
    </section>
  );
}

function SentenceTest({ onFail }: { onFail: (e: unknown) => void }) {
  const [text, setText] = useState("");
  const [hits, setHits] = useState<RuleHit[] | null>(null);
  const test = () => api.testSentence(text.trim()).then((r) => setHits(r.hits)).catch(onFail);
  return (
    <section className="panel">
      <h2>اختبر جملة</h2>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); test(); }}>
        <label className="grow">الجملة <input value={text} maxLength={500} onChange={(e) => { setText(e.target.value); setHits(null); }}
          placeholder="اكتب جملة كما قد تُقال في مكالمة" /></label>
        <div className="filter-actions"><button disabled={!text.trim()}>اختبار</button></div>
      </form>
      {hits && (hits.length === 0 ? <p className="muted">لا تطابق أي قائمة.</p> : (
        <ul className="plain">
          {hits.map((h) => <li key={h.kind + h.phrase}><b>{KIND_TITLE[h.kind] ?? h.kind}</b>: {h.phrase}</li>)}
        </ul>
      ))}
      <p className="muted small">الجملة لا تُحفظ.</p>
    </section>
  );
}

function Topics({ rules, run }: { rules: Rules; run: Run }) {
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [remove, setRemove] = useState<Set<string>>(new Set());
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [reason, setReason] = useState("");
  useEffect(() => { setLabels(Object.fromEntries(rules.topics)); setRemove(new Set()); }, [rules]);
  const set: Record<string, string> = {};
  for (const [k, v] of rules.topics) if (!remove.has(k) && (labels[k] ?? v).trim() !== v) set[k] = (labels[k] ?? v).trim();
  const keyTaken = rules.topics.some(([k]) => k === key.trim());
  if (key.trim() && label.trim() && !keyTaken) set[key.trim()] = label.trim();
  const half = !!key.trim() !== !!label.trim();     // a new topic needs both key and label
  const changed = Object.keys(set).length > 0 || remove.size > 0;
  const save = async () => {
    if (await run(() => api.changeTopics(set, [...remove], reason.trim()), "حُفظت المواضيع.")) {
      setKey(""); setLabel(""); setReason("");
    }
  };
  return (
    <section className="panel">
      <h2>المواضيع <span className="muted small num">{rules.topics.length} / {rules.limits.topics}</span></h2>
      <p className="muted small">
        الذكاء الاصطناعي يختار موضوعًا واحدًا من هذه القائمة لكل مكالمة. أي تغيير يجعل المكالمات الجديدة تُحلَّل بالقائمة الجديدة؛
        المكالمات القديمة تحتفظ بموضوعها وتحليلها (لا يُعاد تحليلها تلقائيًا). «other» (أخرى) لا يُحذف. المفتاح لا يتغير بعد إنشائه.
      </p>
      <div className="table-wrap"><table>
        <thead><tr><th>المفتاح</th><th>الاسم الظاهر</th>{rules.can_edit && <th>حذف</th>}</tr></thead>
        <tbody>
          {rules.topics.map(([k, v]) => (
            <tr key={k}>
              <td dir="ltr" className="num">{k}</td>
              <td>{rules.can_edit
                ? <input value={labels[k] ?? v} maxLength={rules.limits.label} disabled={remove.has(k)}
                    onChange={(e) => setLabels({ ...labels, [k]: e.target.value })} />
                : v}</td>
              {rules.can_edit && <td>{k === "other" ? <span className="muted small">يبقى دائمًا</span> : (
                <input type="checkbox" checked={remove.has(k)} aria-label={`حذف ${v}`}
                  onChange={() => setRemove((r) => { const n = new Set(r); if (n.has(k)) n.delete(k); else n.add(k); return n; })} />
              )}</td>}
            </tr>
          ))}
        </tbody>
      </table></div>
      {rules.can_edit && (
        <form className="rule-edit" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <div className="filters">
            <label>موضوع جديد: المفتاح (a-z و _ فقط)
              <input dir="ltr" value={key} maxLength={30} pattern="[a-z_]{1,30}" placeholder="training"
                onChange={(e) => setKey(e.target.value.toLowerCase())} /></label>
            {keyTaken && <span className="error small">هذا المفتاح موجود؛ عدّل اسمه في الجدول.</span>}
            <label>الاسم الظاهر <input value={label} maxLength={rules.limits.label} onChange={(e) => setLabel(e.target.value)} /></label>
          </div>
          <label>السبب (مطلوب)
            <input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} /></label>
          <div className="filter-actions">
            {half && <span className="muted small">اكتب المفتاح والاسم معًا للموضوع الجديد.</span>}
            <button className="primary" disabled={!changed || half || keyTaken || reason.trim().length < 3}>حفظ</button>
          </div>
        </form>
      )}
    </section>
  );
}

function History({ rows }: { rows: RuleChangeRow[] }) {
  return (
    <section className="panel">
      <h2>سجل التغييرات</h2>
      <p className="muted small">لا يُحذف أي سطر من هذا السجل. آخر 200 تغيير.</p>
      <div className="table-wrap"><table>
        <thead><tr><th>الوقت</th><th>من</th><th>القائمة</th><th>أُضيف</th><th>حُذف</th><th>السبب</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="num nowrap">{formatDateTime(r.at)}</td>
              <td>{r.user ?? <span className="muted">ملف الإعداد</span>}</td>
              <td>{LIST_TITLE[r.list] ?? r.list}</td>
              <td>{r.user === null && r.added.length > 8 ? `${r.added.length} كلمة` : r.added.join("، ") || "–"}</td>
              <td>{r.removed.join("، ") || "–"}</td>
              <td>{r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </section>
  );
}
