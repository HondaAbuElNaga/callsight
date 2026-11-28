// The forwards page (staff only; spec 2026-11-16-priority-no-followup, "Part 4: forwards page"): every forwarded
// problem kept after it closes, the full story of one problem, and the numbers for the chosen dates.
import { useEffect, useRef, useState } from "react";
import {
  api, Refused, Unauthorized,
  type ForwardLineItem, type ForwardNeed, type ForwardPersonStats, type ForwardsList, type ForwardStory, type Me,
} from "./api";
import { HOP_LEVEL, HOP_RESULT, PROOF_REASON, type Priority, formatDateTime, riyadhToday } from "./format";
import { PriorityTag } from "./WorkPanels";

interface Props { me: Me; onCalls: () => void; onLogout: () => void; onExpired: () => void }

const STATE_LABEL: Record<string, [string, string]> = {
  open: ["مفتوحة", "off"], solved: ["حُلّت", "ok"], unsolved: ["توقفت (تعذّر الإرسال)", "bad"],
  send_failed: ["لم تُرسل (فشل)", "bad"], sending: ["عالقة في الإرسال", "warn"],
};
// "needs action now": [key, label, tone, what the supervisor does]
const NEED: [ForwardNeed, string, string | undefined, string][] = [
  ["late", "متأخرة الآن (انتهت 24 ساعة)", "bad",
    "اتصل بمن عنده المشكلة. إن حُلّت فعلًا: افتح القصة واضغط «إغلاق: حلّها هذا الشخص»"],
  ["soon", "تنتهي مهلتها خلال 4 ساعات", "warn", "ذكّر من عنده المشكلة، وإلا تنتقل تلقائيًا لمن بعده"],
  ["send", "تعذّر الإرسال أو عالقة أو توقفت", "bad",
    "تعذّر الإرسال: اطلب من موظف المكالمة أن يعدّل النموذج ويختار من جديد. توقفت: اقرأ السبب في السطر"],
  ["erp_close", "تعذّر إغلاق التذكرة هناك", "bad",
    "تأكد أنها لم تُفتح من جديد، ثم أغلقها يدويًا في نظام خدمة العملاء واضغط «أغلقتها يدويًا هناك» في القصة"],
  ["high", "مفتوحة وأولويتها عالية", undefined, "تابعها أولًا"],
  ["questions", "أسئلة تنتظر رد موظف المكالمة", "warn",
    "اطلب من موظف المكالمة أن يرد من «المتابعات» ← «أسئلة تنتظر ردك»"],
];

/** "باقي 3 س 20 د" / "متأخرة 2 س 5 د": the holders' time left on their 24 h. */
export function timeLeft(due: string | null, now: number = Date.now()): [string, string] | null {
  if (!due) return null;
  const sec = (new Date(due).getTime() - now) / 1000;
  return sec <= 0 ? [`متأخرة ${took(-sec)}`, "bad"] : [`باقي ${took(sec)}`, sec <= 4 * 3600 ? "warn" : "off"];
}
const ITEM_TYPE: Record<string, string> = {
  created: "أُنشئت التذكرة", forwarded: "حُوّلت", priority: "تغيّرت الأولوية", reopened: "أُعيد فتحها",
  closed: "أُغلقت", followup: "كتب", followup_close: "أغلقها وكتب",
};

/** "3 س 20 د" / "2 يوم 4 س": time to solve. */
export function took(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "–";
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  if (h >= 24) return `${Math.floor(h / 24)} يوم ${h % 24} س`;
  return h ? `${h} س ${m} د` : `${m} د`;
}

function minusDays(day: string, n: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d - n));
  return t.toISOString().slice(0, 10);
}

export function ForwardsPage({ me, onCalls, onLogout, onExpired }: Props) {
  const today = riyadhToday();
  const [f, setF] = useState({ from: minusDays(today, 29), to: today, state: "", level: "", branch: "", person: "",
    need: "" });
  const [data, setData] = useState<ForwardsList | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const set = (p: Partial<typeof f>) => setF((x) => ({ ...x, ...p }));

  const expired = useRef(onExpired);
  expired.current = onExpired;
  useEffect(() => {
    let live = true;                       // a late answer for old filters never replaces a newer one
    setError("");
    api.forwards(f).then((d) => { if (live) setData(d); }).catch((e) => {
      if (!live) return;
      setData(null);
      if (e instanceof Unauthorized) expired.current();
      else setError("تعذّر التحميل: تحقق من التواريخ (سنة على الأكثر)");
    });
    return () => { live = false; };
  }, [f]);

  const [, setTick] = useState(0);                  // «باقي …» follows the clock; the numbers refresh too
  useEffect(() => {
    let n = 0;
    const id = window.setInterval(() => {
      n += 1;
      setTick(n);
      if (n % 5 === 0 && !document.hidden) setF((x) => ({ ...x }));
    }, 60_000);
    return () => window.clearInterval(id);
  }, []);
  const t = data?.insights.totals;
  return (
    <div className="page">
      <header className="topbar">
        <h1>التحويلات</h1>
        <div className="who">
          <button className="link" onClick={onCalls}>سجل المكالمات</button>
          <span className="muted">{me.username}{me.ext ? ` · تحويلة ${me.ext}` : ""}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>

      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>من <input id="fw-from" type="date" value={f.from} max={f.to} onChange={(e) => set({ from: e.target.value })} /></label>
        <label>إلى <input id="fw-to" type="date" value={f.to} min={f.from} onChange={(e) => set({ to: e.target.value })} /></label>
        <label>الحالة
          <select id="fw-state" value={f.state} disabled={!!f.need} onChange={(e) => set({ state: e.target.value })}>
            <option value="">الكل</option><option value="open">مفتوحة</option>
            <option value="solved">حُلّت</option><option value="unsolved">توقفت (تعذّر الإرسال)</option>
          </select>
        </label>
        <label>وصلت إلى
          <select id="fw-level" value={f.level} disabled={!!f.need} onChange={(e) => set({ level: e.target.value })}>
            <option value="">الكل</option><option value="1">موظف الفرع فقط</option>
            <option value="2">مشرف الفرع</option><option value="3">الإدارة</option>
          </select>
        </label>
        <label>الفرع
          <select id="fw-branch" value={f.branch} onChange={(e) => set({ branch: e.target.value })}>
            <option value="">الكل</option>
            {data?.filters.branches.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        <label>الشخص
          <select id="fw-person" value={f.person} onChange={(e) => set({ person: e.target.value })}>
            <option value="">الكل</option>
            {data?.filters.people.map((p) => <option key={p.id} value={String(p.id)}>{p.name} · {p.branch}</option>)}
          </select>
        </label>
      </form>
      {error && <p className="bad-text">{error}</p>}

      {data && <section className="panel attention">
        <h2>يحتاج متابعة الآن</h2>
        <p className="muted small">كل المشكلات التي تحتاج تدخلًا الآن، أيًا كانت التواريخ المختارة. اضغط رقمًا لعرضها في القائمة.</p>
        <div className="tiles">{NEED.map(([k, label, tone, todo]) => {
          const n = data.attention[k];
          return <button key={k} type="button" className={`tile action${f.need === k ? " active" : ""}`}
            title={todo} aria-pressed={f.need === k} onClick={() => set({ need: f.need === k ? "" : k })}>
            <span className="tile-label">{label}</span>
            <span className={`tile-value ${n ? tone ?? "" : ""}`}>{n}</span>
            {n > 0 && <span className="muted small">{todo}</span>}
          </button>;
        })}</div>
      </section>}

      {t && <section className="tiles">
        <Tile label="حُوّلت" v={t.forwarded} />
        <Tile label="حلّها موظف الفرع" v={t.solved_1} tone="ok" />
        <Tile label="حلّها مشرف الفرع" v={t.solved_2} tone="ok" />
        <Tile label="حلّتها الإدارة" v={t.solved_3} tone="ok" />
        <Tile label="مفتوحة الآن" v={t.open} />
        <Tile label="توقفت (تعذّر الإرسال)" v={t.unsolved} tone={t.unsolved ? "bad" : undefined} />
        <Tile label="متوسط وقت الحل" v={took(t.avg_seconds_to_solve)} />
      </section>}
      {t && <p className="muted small">الأرقام لكل الفترة المختارة (لا تتأثر بفلاتر الحالة والفرع والشخص). الوقت حتى إغلاق من حلّها.</p>}

      {data && <>
        <People title="موظفو الفروع" rows={data.insights.employees} kind="employee" />
        <People title="مشرفو الفروع" rows={data.insights.supervisors} kind="supervisor" />
        <People title="الإدارة" rows={data.insights.managers} kind="manager" />
        {data.insights.branches.length > 0 && <section className="panel">
          <h2>الفروع</h2>
          <div className="table-wrap"><table className="num-table">
            <thead><tr><th>الفرع</th><th>استلم</th><th>حُلّت داخل الفرع</th><th>وصلت للإدارة</th></tr></thead>
            <tbody>{data.insights.branches.map((b) => <tr key={b.branch}>
              <td>{b.branch}</td><td className="num">{b.received}</td><td className="num">{b.solved_in_branch}</td>
              <td className={b.went_to_managers ? "num bad" : "num"}>{b.went_to_managers}</td></tr>)}</tbody>
          </table></div>
        </section>}

        <section className="panel">
          {f.need ? <div className="line">
            <h2>{NEED.find((x) => x[0] === f.need)?.[1]}{data.truncated ? " (أول 500)" : ""}</h2>
            <button className="link" onClick={() => set({ need: "" })}>عرض كل المشكلات في الفترة</button>
          </div> : <h2>كل المشكلات المحوّلة{data.truncated ? " (أول 500)" : ""}</h2>}
          {f.need && <p className="muted small">الأكثر إلحاحًا أولًا. التواريخ والحالة و«وصلت إلى» لا تُطبَّق هنا، الفرع والشخص يُطبَّقان.</p>}
          {data.items.length === 0 ? <p className="muted">{f.need ? "لا شيء هنا الآن." : "لا توجد تحويلات في هذه الفترة."}</p> :
            <div className="table-wrap"><table>
              <thead><tr><th>الوقت</th><th>العميل</th><th>موظف المكالمة</th><th>الأولوية</th><th>حُوّلت إلى</th>
                <th>وصلت إلى</th><th>الحالة</th><th>الوقت المتبقي</th><th>حلّها</th><th>المدة</th></tr></thead>
              <tbody>{data.items.map((i) => <Line key={i.call_id} i={i} onOpen={() => setOpen(i.call_id)} />)}</tbody>
            </table></div>}
        </section>
      </>}

      {open !== null && <Story id={open} onClose={() => setOpen(null)} onExpired={onExpired}
        onChanged={() => setF((x) => ({ ...x }))} />}
    </div>
  );
}

function Tile({ label, v, tone }: { label: string; v: number | string; tone?: string }) {
  return <div className="tile"><span className="tile-label">{label}</span>
    <span className={`tile-value ${tone ?? ""}`}>{v}</span></div>;
}

function People({ title, rows, kind }: { title: string; rows: ForwardPersonStats[];
  kind: "employee" | "supervisor" | "manager" }) {
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
  if (rows.length === 0) return null;
  const words = q.trim().toLowerCase();
  const found = words ? rows.filter((r) => `${r.name ?? ""} ${r.branch ?? ""}`.toLowerCase().includes(words)) : rows;
  const shown = all ? found : found.slice(0, PEOPLE_SHOWN);
  return (
    <section className="panel">
      <div className="line"><h2>{title} <span className="muted small">({rows.length})</span></h2>
        {rows.length > PEOPLE_SHOWN && <input type="search" className="people-search" value={q}
          placeholder="ابحث بالاسم أو الفرع" aria-label={`بحث في ${title}`} onChange={(e) => setQ(e.target.value)} />}
      </div>
      {found.length === 0 && <p className="muted small">لا أحد بهذا الاسم أو الفرع.</p>}
      <div className="table-wrap"><table className="num-table two">
        <thead><tr><th>الاسم</th><th>الفرع</th><th>استلم</th><th>حلّ</th>
          {kind === "employee" && <th>لم يحلّها خلال 24 ساعة</th>}
          {kind === "supervisor" && <th>لم يحلّها خلال 24 ساعة – انتقلت للإدارة</th>}
          {kind === "supervisor" && <th title="أغلقها دون مكالمة جيدة للعميل (رد، أكثر من 60 ث، رضا أكثر من 7)">أغلقها دون مكالمة جيدة</th>}
          {kind === "manager" && <th>حلّها غيره</th>}
          <th>عنده الآن</th><th>متوسط وقت الحل</th></tr></thead>
        <tbody>{shown.map((r) => <tr key={r.person_id}>
          <td>{r.name ?? "–"}</td><td className="muted">{r.branch ?? "–"}</td>
          <td className="num">{r.received}</td><td className="num">{r.solved}</td>
          {kind !== "manager" && <td className={r.not_solved_24h ? "num bad" : "num"}>{r.not_solved_24h}</td>}
          {kind === "supervisor" && <td className={r.closed_no_good_call ? "num bad" : "num"}>{r.closed_no_good_call}</td>}
          {kind === "manager" && <td className="num">{r.closed_by_other}</td>}
          <td className="num">{r.open}</td><td className="num">{took(r.avg_seconds_to_solve)}</td>
        </tr>)}</tbody>
      </table></div>
      {found.length > PEOPLE_SHOWN && <button type="button" className="link" onClick={() => setAll(!all)}>
        {all ? `عرض أول ${PEOPLE_SHOWN} فقط` : `عرض الكل (${found.length})`}</button>}
    </section>
  );
}

const PEOPLE_SHOWN = 5;           // a long staff table starts short: the most received first, search or «عرض الكل»
// lines our own system writes on the ticket (created, sent, the call summary, the 24 h move): the story already
// shows the problem once at the top and who had it, so these only repeat it. "Us" = whoever created the ticket
// there (as the server's our_actors); before that item is read, the PBX account's usual name.
const SYSTEM_ACTOR = /callsight/i;
type StoryItem = ForwardStory["hops"][number]["items"][number];
function ourActors(st: ForwardStory): Set<string> {
  const all = [...st.hops.flatMap((h) => h.items), ...(st.other_items ?? [])];
  return new Set(all.filter((x) => x.type === "created" && x.actor).map((x) => x.actor as string));
}
function personItems(items: StoryItem[], us: Set<string>, solution?: string | null,
  closedAt?: string | null): StoryItem[] {
  const ours = (x: StoryItem) => (us.size ? !!x.actor && us.has(x.actor) : SYSTEM_ACTOR.test(x.actor ?? ""));
  return items.filter((x) => !ours(x) && x.type !== "created"
    && !(x.type === "escalated" && !x.actor)                // the ticket system's own deadline note
    // his close and its answer are shown once, in the answer box (the kept answer is cut at 1000 letters)
    && !(closedAt && x.type === "closed")
    && !(closedAt && solution && x.type === "followup_close"
      && (x.note ?? "").trim().slice(0, 1000) === solution.trim()));
}

function Line({ i, onOpen }: { i: ForwardLineItem; onOpen: () => void }) {
  const [label, tone] = STATE_LABEL[i.state] ?? [i.state, "off"];
  const sent = i.state !== "send_failed" && i.state !== "sending";     // no ticket yet: no story to open
  const left = timeLeft(i.due);
  const when = i.forwarded_at ?? i.saved_at;
  const chain = i.chain ?? [];
  return (
    <tr className={sent ? "clickable" : undefined} onClick={sent ? onOpen : undefined}>
      <td>{when ? formatDateTime(when) : "–"}</td>
      <td className="num">{i.customer_number ?? "–"}
        {i.repeat > 0 && <> <span className="state warn" title="مشكلات أخرى لنفس العميل حُوّلت خلال 30 يومًا قبلها">
          مكرر ×{i.repeat}</span></>}</td>
      <td>{i.agent_ext} <span className="muted">{i.agent_name}</span></td>
      <td><PriorityTag p={i.priority as Priority | null} /></td>
      <td>{i.first.name ?? "–"} <span className="muted">{i.first.branch}</span></td>
      <td>{i.level ? HOP_LEVEL[i.level] : "–"}</td>
      <td className="chain-cell"><div className="chain-step"><span className={`state ${tone}`}>{label}</span></div>
        {chain.map((c) => <div key={c.level} className={`chain-step${c.now ? " now" : c.result === "solved" ? " solved" : " past"}`}
          title={HOP_RESULT[c.result] ?? c.result}>
          {c.now ? "عند " : ""}{HOP_LEVEL[c.level]}: {c.names.join("، ") || "–"}{c.result === "solved" ? " ✓" : ""}</div>)}
        {i.send_error && <div className="small bad-text">{i.send_error}</div>}
        {i.needs.includes("erp_close") && <div className="small bad-text">تعذّر إغلاق التذكرة هناك</div>}
        {i.hop_error && <div className="small bad-text">{i.hop_error}</div>}
        {i.waiting_check && <div className="small muted">أغلقها، ننتظر فحص مكالمته للعميل (من جهتنا)</div>}
        {i.needs.includes("questions") && <div className="small">سؤال ينتظر رد موظف المكالمة</div>}</td>
      <td className="chain-cell">{chain.length === 0 ? (left ? <span className={`state ${left[1]}`}>{left[0]}</span> : "–")
        : [<div key="top" className="chain-step" />, ...chain.map((c) => {
          const l = c.now ? timeLeft(c.due) : null;
          return <div key={c.level} className={`chain-step${c.now ? " now" : c.result === "solved" ? " solved" : " past"}`}>
            {l ? <span className={`state ${l[1]}`}>{l[0]}</span>
              : c.seconds !== null ? `${c.result === "solved" ? "حلّها بعد" : "بقيت عنده"} ${took(c.seconds)}` : "–"}</div>;
        })]}</td>
      <td>{i.solved_by ? `${i.solved_by.name ?? "–"} (${HOP_LEVEL[i.solved_by.level]})` : "–"}</td>
      <td className="num">{took(i.seconds_to_solve)}</td>
    </tr>
  );
}

const ERP_CLOSE: Record<string, [string, string]> = {
  pending: ["تُغلق التذكرة في نظام خدمة العملاء…", "off"], done: ["أُغلقت التذكرة في نظام خدمة العملاء", "ok"],
  failed: ["تعذّر إغلاق التذكرة في نظام خدمة العملاء: أغلقها هناك يدويًا", "bad"],
  manual: ["أُغلقت التذكرة يدويًا في نظام خدمة العملاء", "ok"],
};

export function Story({ id, onClose, onExpired, onChanged }:
  { id: number; onClose: () => void; onExpired: () => void; onChanged?: () => void }) {
  const [st, setSt] = useState<ForwardStory | null>(null);
  const [error, setError] = useState("");
  const [ask, setAsk] = useState<number | null>(null);      // the hop whose «حلّها» waits for the second click
  const [askHand, setAskHand] = useState<number | null>(null);   // the hop whose «أغلقتها يدويًا» waits too
  const [busy, setBusy] = useState(false);
  const [decideError, setDecideError] = useState("");
  const shown = useRef(id);
  shown.current = id;
  const decide = (hopId: number, byHand = false) => {
    const forId = id;
    setBusy(true);
    setDecideError("");
    (byHand ? api.forwardClosedByHand(forId, hopId) : api.forwardDecide(forId, hopId)).then((d) => {
      onChanged?.();
      if (shown.current !== forId) return;              // another problem is open now: keep its story
      setSt(d);
      setAsk(null);
      setAskHand(null);
    }).catch((e) => {
      if (shown.current !== forId) return;
      if (e instanceof Unauthorized) expired.current();
      else setDecideError(e instanceof Refused ? e.message : "تعذّر الحفظ");
    }).finally(() => setBusy(false));
  };
  const expired = useRef(onExpired);
  expired.current = onExpired;
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    let live = true;
    setSt(null);
    setError("");
    setAsk(null);
    setAskHand(null);
    setDecideError("");
    api.forwardStory(id).then((d) => { if (live) setSt(d); }).catch((e) => {
      if (!live) return;
      if (e instanceof Unauthorized) expired.current(); else setError("تعذّر تحميل القصة");
    });
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") close.current(); };
    window.addEventListener("keydown", esc);
    return () => { live = false; window.removeEventListener("keydown", esc); };
  }, [id]);
  const us = st ? ourActors(st) : new Set<string>();
  const others = st ? personItems(st.other_items ?? [], us) : [];
  return (
    <aside className="side-panel story">
      <div className="line"><h2>قصة المشكلة · مكالمة {id}</h2><button className="link" onClick={onClose}>إغلاق</button></div>
      {error && <p className="bad-text">{error}</p>}
      {decideError && <p className="bad-text">{decideError}</p>}
      {st && <ol className="story-steps">
        <li>
          <b>موظف المكالمة {st.agent.ext} {st.agent.name} حوّلها</b>
          <span className="muted small"> · {st.agent.forwarded_at ? formatDateTime(st.agent.forwarded_at) : "–"}
            {st.agent.ticket_number ? ` · تذكرة رقم ${st.agent.ticket_number}` : ""}</span>
          <div className="row"><PriorityTag p={st.priority as Priority | null} /> <span className="muted small">العميل {st.customer_number}</span></div>
          <p>{st.agent.what_happened}</p>
          {st.agent.note && <p className="small">ملاحظة للمسؤول: {st.agent.note}</p>}
        </li>
        {st.hops.map((h, n) => { const items = personItems(h.items, us, h.solution, h.closed_at); return (
          <li key={n}>
            <b>{HOP_LEVEL[h.level]}: {h.name ?? "–"}</b> <span className="muted small">{h.branch}</span>
            <div className="small muted">{h.sent_at ? `استلمها ${formatDateTime(h.sent_at)}` : "لم تُرسل بعد"}
              {h.ticket_number ? ` · تذكرة ${h.ticket_number}` : ""}</div>
            {items.length > 0 ? <ul className="small">
              {items.map((x, k) => <li key={k}>{x.at ? formatDateTime(x.at) : ""} · {ITEM_TYPE[x.type] ?? x.type}
                {x.actor ? ` (${x.actor})` : ""}{x.note ? `: ${x.note}` : ""}</li>)}
            </ul> : !h.closed_at && <p className="small muted">لم يكتب شيئًا على التذكرة.</p>}
            {h.items_cut && <p className="small muted">(أول 200 فقط)</p>}
            {h.calls && <div className="small">
              <b>مكالماته للعميل:</b>{h.calls.length === 0 ? " لا توجد" : <ul>
                {h.calls.map((c) => <li key={c.call_id}>{formatDateTime(c.time)} · {c.answered
                  ? `ردّ العميل · ${c.talk_seconds} ث` : "لم يرد"}{c.score !== null ? ` · الرضا ${c.score}/10` : ""}</li>)}
              </ul>}
              {h.closed_at && h.proof && (h.proof.ok ? <span className="state ok">المكالمة مقبولة ✓</span>
                : <span className="state warn">المكالمة غير مقبولة: {PROOF_REASON[h.proof.reason ?? ""] ?? "–"}</span>)}
            </div>}
            {h.closed_at && <div className={`answer-box${h.result === "solved" ? " ok" : ""}`}>
              <div className="small">أغلقها {formatDateTime(h.closed_at)}{h.solution ? " · ردّه:" : ""}</div>
              {h.solution && <p>{h.solution}</p>}</div>}
            <span className={`state ${h.result === "solved" ? "ok" : h.result === "passed_on" || h.result === "failed" ? "bad" : "off"}`}>
              {HOP_RESULT[h.result] ?? h.result}</span>
            {h.result === "passed_on" && h.ended_at && <span className="muted small"> · انتقلت {formatDateTime(h.ended_at)}</span>}
            {h.decided_at && <span className="muted small"> · بقرار {h.decided_by ?? "المشرف"} {formatDateTime(h.decided_at)}</span>}
            {h.error && <span className="muted small"> ({h.error})</span>}
            {h.erp_close && <div className="small"><span className={`state ${ERP_CLOSE[h.erp_close][1]}`}>
              {ERP_CLOSE[h.erp_close][0]}</span></div>}
            {h.erp_close === "failed" && <div className="row small">
              {askHand === h.hop_id ? <>
                <span>هل أغلقت التذكرة {h.ticket_number ?? ""} بنفسك في نظام خدمة العملاء؟</span>
                <button disabled={busy} onClick={() => decide(h.hop_id, true)}>نعم، أغلقتها هناك</button>
                <button className="link" disabled={busy} onClick={() => setAskHand(null)}>إلغاء</button>
              </> : <button className="link" onClick={() => { setAskHand(h.hop_id); setAsk(null); setDecideError(""); }}>
                أغلقتها يدويًا هناك</button>}
            </div>}
            {h.can_decide && <div className="row small">
              {ask === h.hop_id ? <>
                <span>حلّها «{h.name ?? "–"}»؟ تُغلق المشكلة وتُحسب له، وتُغلق التذكرة في نظام خدمة العملاء.</span>
                <button disabled={busy} onClick={() => decide(h.hop_id)}>نعم، أغلقها</button>
                <button className="link" disabled={busy} onClick={() => setAsk(null)}>إلغاء</button>
              </> : <button className="link" onClick={() => { setAsk(h.hop_id); setAskHand(null); setDecideError(""); }}>
                إغلاق: حلّها هذا الشخص</button>}
            </div>}
          </li>
        ); })}
        {others.length > 0 && <li>
          <b>رسائل أخرى على التذكرة</b>
          <ul className="small">{others.map((x, k) => <li key={k}>{x.at ? formatDateTime(x.at) : ""} · {ITEM_TYPE[x.type] ?? x.type}
            {x.actor ? ` (${x.actor})` : ""}{x.note ? `: ${x.note}` : ""}</li>)}</ul>
        </li>}
      </ol>}
    </aside>
  );
}
