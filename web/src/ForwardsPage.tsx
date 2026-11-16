// The forwards page (staff only; spec 2026-11-16-priority-no-followup, "Part 4: forwards page"): every forwarded
// problem kept after it closes, the full story of one problem, and the numbers for the chosen dates.
import { useCallback, useEffect, useState } from "react";
import {
  api, Unauthorized,
  type ForwardLineItem, type ForwardPersonStats, type ForwardsList, type ForwardStory, type Me,
} from "./api";
import { HOP_LEVEL, HOP_RESULT, PROOF_REASON, type Priority, formatDateTime, riyadhToday } from "./format";
import { PriorityTag } from "./WorkPanels";

interface Props { me: Me; onCalls: () => void; onLogout: () => void; onExpired: () => void }

const STATE_LABEL: Record<string, [string, string]> = {
  open: ["مفتوحة", "off"], solved: ["حُلّت", "ok"], unsolved: ["لم تُحل", "bad"],
};
const ITEM_TYPE: Record<string, string> = {
  created: "أُنشئت التذكرة", forwarded: "حُوّلت", priority: "تغيّرت الأولوية", reopened: "أُعيد فتحها",
  closed: "أُغلقت", followup: "كتب", followup_close: "أغلقها وكتب",
};

/** "3 س 20 د" / "2 يوم 4 س": time to solve. */
function took(sec: number | null | undefined): string {
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
  const [f, setF] = useState({ from: minusDays(today, 29), to: today, state: "", level: "", branch: "", person: "" });
  const [data, setData] = useState<ForwardsList | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const set = (p: Partial<typeof f>) => setF((x) => ({ ...x, ...p }));

  const load = useCallback(() => {
    setError("");
    api.forwards(f).then(setData).catch((e) => {
      if (e instanceof Unauthorized) onExpired();
      else setError("تعذّر التحميل: تحقق من التواريخ (سنة على الأكثر)");
    });
  }, [f, onExpired]);
  useEffect(load, [load]);

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
          <select id="fw-state" value={f.state} onChange={(e) => set({ state: e.target.value })}>
            <option value="">الكل</option><option value="open">مفتوحة</option>
            <option value="solved">حُلّت</option><option value="unsolved">لم تُحل</option>
          </select>
        </label>
        <label>وصلت إلى
          <select id="fw-level" value={f.level} onChange={(e) => set({ level: e.target.value })}>
            <option value="">الكل</option><option value="1">الموظف فقط</option>
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

      {t && <section className="tiles">
        <Tile label="حُوّلت" v={t.forwarded} />
        <Tile label="حلّها الموظف" v={t.solved_1} tone="ok" />
        <Tile label="حلّها مشرف الفرع" v={t.solved_2} tone="ok" />
        <Tile label="حلّتها الإدارة" v={t.solved_3} tone="ok" />
        <Tile label="مفتوحة الآن" v={t.open} />
        <Tile label="لم تُحل" v={t.unsolved} tone={t.unsolved ? "bad" : undefined} />
        <Tile label="متوسط وقت الحل" v={took(t.avg_seconds_to_solve)} />
      </section>}

      {data && <>
        <People title="الموظفون" rows={data.insights.employees} kind="employee" />
        <People title="مشرفو الفروع" rows={data.insights.supervisors} kind="supervisor" />
        <People title="الإدارة" rows={data.insights.managers} kind="manager" />
        {data.insights.branches.length > 0 && <section className="panel">
          <h2>الفروع</h2>
          <div className="table-wrap"><table>
            <thead><tr><th>الفرع</th><th>استلم</th><th>حُلّت داخل الفرع</th><th>وصلت للإدارة</th></tr></thead>
            <tbody>{data.insights.branches.map((b) => <tr key={b.branch}>
              <td>{b.branch}</td><td className="num">{b.received}</td><td className="num">{b.solved_in_branch}</td>
              <td className={b.went_to_managers ? "num bad" : "num"}>{b.went_to_managers}</td></tr>)}</tbody>
          </table></div>
        </section>}

        <section className="panel">
          <h2>كل المشكلات المحوّلة{data.truncated ? " (أول 500)" : ""}</h2>
          {data.items.length === 0 ? <p className="muted">لا توجد تحويلات في هذه الفترة.</p> :
            <div className="table-wrap"><table>
              <thead><tr><th>الوقت</th><th>العميل</th><th>الموظف</th><th>الأولوية</th><th>حُوّلت إلى</th>
                <th>وصلت إلى</th><th>الحالة</th><th>حلّها</th><th>المدة</th></tr></thead>
              <tbody>{data.items.map((i) => <Line key={i.call_id} i={i} onOpen={() => setOpen(i.call_id)} />)}</tbody>
            </table></div>}
        </section>
      </>}

      {open !== null && <Story id={open} onClose={() => setOpen(null)} onExpired={onExpired} />}
    </div>
  );
}

function Tile({ label, v, tone }: { label: string; v: number | string; tone?: string }) {
  return <div className="tile"><span className="tile-label">{label}</span>
    <span className={`tile-value ${tone ?? ""}`}>{v}</span></div>;
}

function People({ title, rows, kind }: { title: string; rows: ForwardPersonStats[];
  kind: "employee" | "supervisor" | "manager" }) {
  if (rows.length === 0) return null;
  return (
    <section className="panel">
      <h2>{title}</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>الاسم</th><th>الفرع</th><th>استلم</th><th>حلّ</th>
          {kind !== "manager" && <th>لم يحلّها خلال 24 ساعة</th>}
          {kind === "supervisor" && <th title="أغلقها دون مكالمة جيدة للعميل (رد، أكثر من 60 ث، رضا أكثر من 7)">أغلقها دون مكالمة جيدة</th>}
          {kind === "manager" && <th>حلّها غيره</th>}
          <th>عنده الآن</th><th>متوسط وقت الحل</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.person_id}>
          <td>{r.name ?? "–"}</td><td className="muted">{r.branch ?? "–"}</td>
          <td className="num">{r.received}</td><td className="num">{r.solved}</td>
          {kind !== "manager" && <td className={r.not_solved_24h ? "num bad" : "num"}>{r.not_solved_24h}</td>}
          {kind === "supervisor" && <td className={r.closed_no_good_call ? "num bad" : "num"}>{r.closed_no_good_call}</td>}
          {kind === "manager" && <td className="num">{r.closed_by_other}</td>}
          <td className="num">{r.open}</td><td className="num">{took(r.avg_seconds_to_solve)}</td>
        </tr>)}</tbody>
      </table></div>
    </section>
  );
}

function Line({ i, onOpen }: { i: ForwardLineItem; onOpen: () => void }) {
  const [label, tone] = STATE_LABEL[i.state] ?? [i.state, "off"];
  return (
    <tr className="clickable" onClick={onOpen}>
      <td>{i.forwarded_at ? formatDateTime(i.forwarded_at) : "–"}</td>
      <td className="num">{i.customer_number ?? "–"}</td>
      <td>{i.agent_ext} <span className="muted">{i.agent_name}</span></td>
      <td><PriorityTag p={i.priority as Priority | null} /></td>
      <td>{i.first.name ?? "–"} <span className="muted">{i.first.branch}</span></td>
      <td>{i.level ? HOP_LEVEL[i.level] : "–"}</td>
      <td><span className={`state ${tone}`}>{label}</span>
        {i.state === "open" && i.holders.length > 0 && <span className="muted small"> عند {i.holders.join("، ")}</span>}</td>
      <td>{i.solved_by ? `${i.solved_by.name ?? "–"} (${HOP_LEVEL[i.solved_by.level]})` : "–"}</td>
      <td className="num">{took(i.seconds_to_solve)}</td>
    </tr>
  );
}

function Story({ id, onClose, onExpired }: { id: number; onClose: () => void; onExpired: () => void }) {
  const [st, setSt] = useState<ForwardStory | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setSt(null);
    api.forwardStory(id).then(setSt).catch((e) => {
      if (e instanceof Unauthorized) onExpired(); else setError("تعذّر تحميل القصة");
    });
  }, [id, onExpired]);
  return (
    <aside className="side-panel story">
      <div className="line"><h2>قصة المشكلة · مكالمة {id}</h2><button className="link" onClick={onClose}>إغلاق</button></div>
      {error && <p className="bad-text">{error}</p>}
      {st && <ol className="story-steps">
        <li>
          <b>الموظف {st.agent.ext} {st.agent.name} حوّلها</b>
          <span className="muted small"> · {st.agent.forwarded_at ? formatDateTime(st.agent.forwarded_at) : "–"}
            {st.agent.ticket_number ? ` · تذكرة رقم ${st.agent.ticket_number}` : ""}</span>
          <div className="row"><PriorityTag p={st.priority as Priority | null} /> <span className="muted small">العميل {st.customer_number}</span></div>
          <p>{st.agent.what_happened}</p>
          {st.agent.note && <p className="small">ملاحظة للمسؤول: {st.agent.note}</p>}
        </li>
        {st.hops.map((h, n) => (
          <li key={n}>
            <b>{HOP_LEVEL[h.level]}: {h.name ?? "–"}</b> <span className="muted small">{h.branch}</span>
            <div className="small muted">{h.sent_at ? `استلمها ${formatDateTime(h.sent_at)}` : "لم تُرسل بعد"}
              {h.ticket_number ? ` · تذكرة ${h.ticket_number}` : ""}</div>
            {h.items.length > 0 ? <ul className="small">
              {h.items.map((x, k) => <li key={k}>{x.at ? formatDateTime(x.at) : ""} · {ITEM_TYPE[x.type] ?? x.type}
                {x.actor ? ` (${x.actor})` : ""}{x.note ? `: ${x.note}` : ""}</li>)}
            </ul> : <p className="small muted">لم يكتب شيئًا على التذكرة.</p>}
            {h.calls && <div className="small">
              <b>مكالماته للعميل:</b>{h.calls.length === 0 ? " لا توجد" : <ul>
                {h.calls.map((c) => <li key={c.call_id}>{formatDateTime(c.time)} · {c.answered
                  ? `ردّ العميل · ${c.talk_seconds} ث` : "لم يرد"}{c.score !== null ? ` · الرضا ${c.score}/10` : ""}</li>)}
              </ul>}
              {h.closed_at && h.proof && (h.proof.ok ? <span className="state ok">المكالمة مقبولة ✓</span>
                : <span className="state warn">المكالمة غير مقبولة: {PROOF_REASON[h.proof.reason ?? ""] ?? "–"}</span>)}
            </div>}
            {h.closed_at && <p className="small">أغلقها {formatDateTime(h.closed_at)}{h.solution ? ` · الحل: ${h.solution}` : ""}</p>}
            <span className={`state ${h.result === "solved" ? "ok" : h.result === "passed_on" || h.result === "failed" ? "bad" : "off"}`}>
              {HOP_RESULT[h.result] ?? h.result}</span>
            {h.error && <span className="muted small"> ({h.error})</span>}
          </li>
        ))}
      </ol>}
    </aside>
  );
}
