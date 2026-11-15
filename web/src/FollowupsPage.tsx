// "المتابعات": agents see their own work (forms, callbacks, notes after a callback) and supervisor notes; staff see
// everyone's (agent-supervisor spec; steps, counters, "needs action" view: follow-up stages spec).
import { useCallback, useEffect, useState } from "react";
import { api, Unauthorized, type Agent, type FollowupItem, type FollowupList, type Me, type Share } from "./api";
import { CALLBACK_LABEL, STEP_CLASS, STEP_LABEL, formatDateTime, riyadhToday } from "./format";
import { CB_CLASS } from "./WorkPanels";
import { CallsPage } from "./CallsPage";

interface Props { me: Me; onCalls?: () => void;   // agents: no calls page (follow-ups is their only page)
  onLogout: () => void; onExpired: () => void }

const STATE_OPTIONS: [string, string][] = [
  ["action", "يحتاج عملاً"], ["open", "متابعات مفتوحة"], ["solved", "حُلّت"], ["came_back", "عاد العميل بعد الحل"],
  ["", "الكل"],
];
const REFRESH_MS = 60_000;
const SOON_MS = 60 * 60_000;

function weekAgo(): string {
  const [y, m, d] = riyadhToday().split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - 6)).toISOString().slice(0, 10);
}

/** Row colour (fix E): red = missed / overdue, yellow = callback within the hour or a note to write. */
function urgency(i: FollowupItem, now: number): string {
  if (i.step === "callback_missed" || i.step === "overdue") return "row-bad";
  if (i.step === "note_needed") return "row-warn";
  if (i.step === "waiting_callback" && i.callback && new Date(i.callback.at).getTime() - now <= SOON_MS) return "row-warn";
  return "";
}

function Number_({ n }: { n: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!n) return <>–</>;
  const copy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard?.writeText(n).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })
      .catch(() => undefined);
  };
  return (
    <span className="nowrap">
      <a dir="ltr" className="num" href={`tel:${n.replace(/[^0-9+]/g, "")}`} onClick={(e) => e.stopPropagation()}>{n}</a>
      <button type="button" className="link small" onClick={copy} title="نسخ الرقم">{copied ? "نُسخ" : "نسخ"}</button>
    </span>
  );
}

export function FollowupsPage({ me, onCalls, onLogout, onExpired }: Props) {
  const staff = me.role === "admin" || me.role === "supervisor";
  const [from, setFrom] = useState(weekAgo);
  const [to, setTo] = useState(riyadhToday);
  const [agent, setAgent] = useState("");
  const [state, setState] = useState("action");
  const [data, setData] = useState<FollowupList | null>(null);
  const [shares, setShares] = useState<Share[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [open, setOpen] = useState<number | null>(null);   // the call shown in the side panel, on this page
  const onOpenCall = (id: number) => setOpen(id);

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setError("تعذر تحميل المتابعات. أعد المحاولة.");
  }, [onExpired]);

  useEffect(() => {   // fix F: reload every minute while the tab is visible
    const t = setInterval(() => { if (document.visibilityState === "visible") setTick((n) => n + 1); }, REFRESH_MS);
    const back = () => { if (document.visibilityState === "visible") setTick((n) => n + 1); };
    document.addEventListener("visibilitychange", back);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", back); };
  }, []);
  useEffect(() => { if (staff) api.agents().then(setAgents).catch(fail); }, [staff, fail]);
  useEffect(() => {
    let alive = true;                 // filters apply at once: an older, slower answer must not win
    api.followups({ from, to, agent, state })
      .then((d) => { if (alive) { setData(d); setError(""); } }).catch((e) => alive && fail(e));
    return () => { alive = false; };
  }, [from, to, agent, state, fail, tick]);
  const loadShares = useCallback(() => api.shares().then((d) => setShares(d.items)).catch(fail), [fail]);
  useEffect(() => { loadShares(); }, [loadShares, tick]);

  const now = Date.now();
  const c = data?.counts;
  const dates = state !== "action";      // "needs action" looks back 62 days by itself (fix C)
  return (
    <div className="page">
      <header className="topbar">
        <h1>المتابعات</h1>
        <div className="who">
          {onCalls && <button className="link" onClick={onCalls}>سجل المكالمات</button>}
          <span className="muted">{me.username}{me.ext ? ` · تحويلة ${me.ext}` : ""}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>
      {error && <div className="error">{error}</div>}

      {data && !data.linked && (
        <p className="error">حسابك غير مرتبط بتحويلة في فريق خدمة العملاء أو ما بعد البيع، لذلك لا تظهر لك متابعات. اطلب من مدير النظام ربط تحويلتك.</p>
      )}

      {c && data?.settings.followup_start && data.linked && (
        <div className="counters">   {/* fix D */}
          <button type="button" className={`counter ${c.forms ? "bad" : ""}`} onClick={() => setState("action")}>
            <b>{c.forms}</b> نماذج مطلوبة</button>
          <button type="button" className={`counter ${c.notes ? "warn" : ""}`} onClick={() => setState("action")}>
            <b>{c.notes}</b> ملاحظات بعد المعاودة</button>
          <button type="button" className="counter" onClick={() => setState("action")}>
            <b>{c.callbacks_today}</b> معاودات اليوم</button>
          <button type="button" className={`counter ${c.missed ? "bad" : ""}`} onClick={() => setState("action")}>
            <b>{c.missed}</b> معاودات فائتة</button>
        </div>
      )}

      {(staff || shares.length > 0) && (   /* fix K: agents see this box only when there is a note */
        <section className="panel">
          <h2>{staff ? "المكالمات المشاركة مع الموظفين" : "مكالمات أرسلها لك المشرف"}</h2>
          {shares.length === 0 ? <p className="muted">لا توجد.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>المكالمة</th><th>الملاحظة</th><th>من ← إلى</th><th>شوهدت</th></tr></thead>
              <tbody>
                {shares.map((s) => (
                  <tr key={s.id} onClick={() => onOpenCall(s.call_id)}>
                    <td className="nowrap">{s.call_start ? formatDateTime(s.call_start) : "–"}</td>
                    <td>{s.note}</td>
                    <td>{s.from} ← {s.to}</td>
                    <td>{s.seen_at ? formatDateTime(s.seen_at) : <span className="state off">لم تُشاهد</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </section>
      )}

      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>العرض
          <select value={state} onChange={(e) => setState(e.target.value)}>
            {STATE_OPTIONS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        {dates ? <>
          <label>من <input type="date" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>
          <label>إلى <input type="date" value={to} min={from} onChange={(e) => e.target.value && setTo(e.target.value)} /></label>
        </> : <span className="muted small">آخر 62 يومًا، الأهم أولًا</span>}
        {staff && (
          <label>الموظف
            <select value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">الكل</option>
              {agents.map((a) => <option key={a.ext} value={a.ext}>{a.ext} · {a.name}</option>)}
            </select>
          </label>
        )}
      </form>

      {data && !data.settings.followup_start ? (
        <p className="muted panel">نماذج المتابعة غير مفعّلة بعد. يفعّلها مدير النظام من صفحة المستخدمين (تاريخ البدء).</p>
      ) : data && !data.linked ? null : (
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>الوقت</th><th>رقم العميل</th>{staff && <th>الموظف</th>}<th>المرحلة</th><th>معاودة الاتصال</th><th>آخر موعد</th>
            </tr></thead>
            <tbody>
              {data?.items.map((i) => (
                <tr key={i.call_id} className={urgency(i, now)} onClick={() => onOpenCall(i.call_id)}>
                  <td className="nowrap">{formatDateTime(i.start_time)}</td>
                  <td><Number_ n={i.customer_number} /></td>
                  {staff && <td>{i.agent_ext} <span className="muted">{i.agent_name}</span></td>}
                  <td>
                    <span className={`state ${STEP_CLASS[i.step]}`}>{STEP_LABEL[i.step]}</span>
                    {i.round > 1 && <span className="muted small"> · جولة {i.round}</span>}
                    {i.came_back && <span className="tag">عاد العميل</span>}
                  </td>
                  <td>{i.callback ? <>   {/* fix J: callback + "did the customer answer" in one cell */}
                    {formatDateTime(i.callback.at)} <span className={`state ${CB_CLASS[i.callback.status]}`}>{CALLBACK_LABEL[i.callback.status]}</span>
                    {i.callback.time && <span className="muted small"> · اتصل {i.callback.by_ext}
                      {i.callback.answered ? " · ردّ العميل" : " · لم يرد"}</span>}
                  </> : "–"}</td>
                  <td className="nowrap muted small">
                    {i.step === "note_needed" && i.note_deadline ? formatDateTime(i.note_deadline)
                      : i.step === "required" || i.step === "overdue" ? formatDateTime(i.deadline) : "–"}
                  </td>
                </tr>
              ))}
              {data && data.items.length === 0 && (
                <tr><td colSpan={staff ? 6 : 5} className="center muted">
                  {state === "action" ? "لا يوجد عمل مطلوب الآن" : "لا يوجد شيء لهذا الاختيار"}</td></tr>
              )}
            </tbody>
          </table>
          {data?.truncated && <p className="muted small">تظهر أول 500 فقط: ضيّق الفترة.</p>}
        </div>
      )}
      {open !== null && (   /* the call opens in the side panel over this list; ✕ closes it and refreshes the list */
        <CallsPage key={open} me={me} onLogout={onLogout} onExpired={onExpired} detailOnly embedded openCallId={open}
          onFollowups={() => { setOpen(null); setTick((n) => n + 1); }} />
      )}
    </div>
  );
}
