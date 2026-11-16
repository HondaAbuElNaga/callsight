// "المتابعات": agents see their own work (forms, callbacks, notes after a callback) and supervisor notes; staff see
// everyone's (agent-supervisor spec; steps, counters, "needs action" view: follow-up stages spec).
import { useCallback, useEffect, useState } from "react";
import { api, Unauthorized, type Agent, type FollowupItem, type FollowupList, type Me, type Share } from "./api";
import {
  CALLBACK_LABEL, PRIORITIES, PRIORITY_LABEL, STEP_CLASS, STEP_LABEL, earlyText, formatDateTime, riyadhToday, ringText,
} from "./format";
import { CB_CLASS, PriorityTag } from "./WorkPanels";
import { CallsPage } from "./CallsPage";

interface Props { me: Me; onCalls?: () => void;   // agents: no calls page (follow-ups is their only page)
  onLogout: () => void; onExpired: () => void }

const STATE_OPTIONS: [string, string][] = [
  ["action", "يحتاج عملاً"], ["forms", "نماذج مطلوبة"], ["notes", "ملاحظات مطلوبة"],
  ["callbacks_today", "معاودات اليوم"], ["missed", "معاودات فائتة"], ["open", "متابعات مفتوحة"], ["solved", "حُلّت"],
  ["came_back", "عاد العميل بعد الحل"], ["no_followup", "لا تحتاج متابعة"], ["", "الكل"],
];
const LOOKBACK_DAYS = 62;   // same as the server's look-back for unfinished work
const REFRESH_MS = 60_000;
const SOON_MS = 60 * 60_000;

function daysAgo(n: number): string {
  const [y, m, d] = riyadhToday().split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - n)).toISOString().slice(0, 10);
}

/** Row colour (fix E): red = missed / overdue, yellow = callback within the hour or a note to write. */
function urgency(i: FollowupItem, now: number): string {
  if (i.step === "callback_missed" || i.step === "overdue" || i.step === "solution_overdue") return "row-bad";
  if (i.step === "waiting_solution" && i.solve_by && new Date(i.solve_by).getTime() - now <= SOON_MS) return "row-warn";
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

/** "ملاحظتي": the viewer's own reminder on a call, edited in place; nobody else sees it (private-note spec). */
function MyNote({ callId, text, onSaved, onFail }:
  { callId: number; text: string | null | undefined; onSaved: () => void; onFail: (e: unknown) => void }) {
  const [edit, setEdit] = useState(false);
  const [value, setValue] = useState(text ?? "");
  const [busy, setBusy] = useState(false);
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();     // never opens the call
  if (!edit)
    return (
      <span onClick={stop}>
        {text ? <span className="my-note">{text}</span> : null}
        <button type="button" className="link small" onClick={() => { setValue(text ?? ""); setEdit(true); }}>
          {text ? "تعديل" : "+ ملاحظة"}</button>
      </span>
    );
  const save = async () => {
    if (busy) return;
    setBusy(true);
    try { await api.myNote(callId, value); setEdit(false); onSaved(); } catch (e) { onFail(e); } finally { setBusy(false); }
  };
  return (
    <span className="my-note-edit" onClick={stop}>
      <textarea value={value} maxLength={300} rows={2} autoFocus onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Escape") setEdit(false); }} />
      <button type="button" className="primary small" disabled={busy} onClick={save}>حفظ</button>
      <button type="button" className="link small" onClick={() => setEdit(false)}>إلغاء</button>
    </span>
  );
}

export function FollowupsPage({ me, onCalls, onLogout, onExpired }: Props) {
  const staff = me.role === "admin" || me.role === "supervisor";
  const [from, setFrom] = useState(riyadhToday);     // the page opens on today; من/إلى go back
  const [to, setTo] = useState(riyadhToday);
  const [agent, setAgent] = useState("");
  const [state, setState] = useState("action");
  const [priority, setPriority] = useState("");   // spec 2026-11-16: filters the list, the counters count all
  const [data, setData] = useState<FollowupList | null>(null);
  const [shares, setShares] = useState<Share[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [open, setOpen] = useState<number | null>(null);   // the call shown in the side panel, on this page
  const [fromShare, setFromShare] = useState(false);        // opened from a supervisor's note: no follow-up form
  const onOpenCall = (id: number, share = false) => { setFromShare(share); setOpen(id); };

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
    api.followups({ from, to, agent, state, priority })
      .then((d) => { if (alive) { setData(d); setError(""); } }).catch((e) => alive && fail(e));
    return () => { alive = false; };
  }, [from, to, agent, state, priority, fail, tick]);
  // staff: with an agent picked, only the calls shared with that agent
  const loadShares = useCallback(() => api.shares(staff ? agent : "").then((d) => setShares(d.items)).catch(fail),
    [fail, staff, agent]);
  useEffect(() => { loadShares(); }, [loadShares, tick]);

  const now = Date.now();
  const c = data?.counts;
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
        <div className="counters">   {/* click = show only that kind; click again = back to "needs action" */}
          {([["forms", c.forms, "نماذج مطلوبة", "bad"], ["notes", c.notes, "ملاحظات مطلوبة", "warn"],
             ["callbacks_today", c.callbacks_today, "معاودات اليوم", ""], ["missed", c.missed, "معاودات فائتة", "bad"],
             ["no_followup", c.no_followup, "لا تحتاج متابعة", ""]] as const)
            .map(([k, n, label, tone]) => (
              <button key={k} type="button" aria-pressed={state === k}
                className={`counter ${n ? tone : ""} ${state === k ? "active" : ""}`}
                onClick={() => setState(state === k ? "action" : k)}>
                <b>{n}</b> {label}</button>
            ))}
        </div>
      )}
      {data && data.older_action > 0 && (
        <p className="older">يوجد {data.older_action} عمل مطلوب في أيام قبل {from}.{" "}
          <button type="button" className="link" onClick={() => { setFrom(daysAgo(LOOKBACK_DAYS)); setState("action"); }}>
            عرضها</button></p>
      )}

      {(staff || shares.length > 0) && (   /* fix K: agents see this box only when there is a note */
        <section className="panel">
          <h2>{staff ? "المكالمات المشاركة مع الموظفين" : "مكالمات أرسلها لك المشرف"}</h2>
          {shares.length === 0 ? <p className="muted">لا توجد.</p> : (
            <div className="table-wrap"><table>
              <thead><tr><th>المكالمة</th><th>الملاحظة</th><th>من ← إلى</th><th>{staff ? "فتحها الموظف" : "فتحتها"}</th><th>الرد</th></tr></thead>
              <tbody>
                {shares.map((s) => (
                  <tr key={s.id} onClick={() => {   // agent: one click opens the call AND shows the note
                    if (!staff && !s.seen_at) api.shareSeen(s.id).then(loadShares).catch(fail).finally(() => onOpenCall(s.call_id, true));
                    else onOpenCall(s.call_id, true);
                  }}>
                    <td className="nowrap">{s.call_start ? formatDateTime(s.call_start) : "–"}</td>
                    <td>{s.note ?? <span className="state warn">ملاحظة جديدة: اضغط لعرضها</span>}</td>
                    <td>{s.from} ← {s.to}</td>
                    <td>{s.seen_at ? formatDateTime(s.seen_at) : <span className="state off">{staff ? "لم يفتحها" : "لم تفتحها"}</span>}</td>
                    <td>{s.reply ? <>{s.reply} <span className="muted small">· {formatDateTime(s.reply_at!)}</span></> : "–"}</td>
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
        <label>من <input type="date" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>
        <label>إلى <input type="date" value={to} min={from} max={riyadhToday()} onChange={(e) => e.target.value && setTo(e.target.value)} /></label>
        {(from !== riyadhToday() || to !== riyadhToday()) &&
          <button type="button" className="link" onClick={() => { setFrom(riyadhToday()); setTo(riyadhToday()); }}>اليوم</button>}
        <label>الأولوية
          <select value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">الكل</option>
            {PRIORITIES.map((k) => <option key={k} value={k}>{PRIORITY_LABEL[k]}</option>)}
          </select>
        </label>
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
              <th>الوقت</th><th>رقم العميل</th>{staff && <th>الموظف</th>}<th>المرحلة</th><th>معاودة الاتصال</th><th>آخر موعد</th><th>ملاحظتي</th>
            </tr></thead>
            <tbody>
              {data?.items.map((i) => (
                <tr key={i.call_id} className={urgency(i, now)} onClick={() => onOpenCall(i.call_id)}>
                  <td className="nowrap">{formatDateTime(i.start_time)}</td>
                  <td><Number_ n={i.customer_number} /></td>
                  {staff && <td>{i.agent_ext} <span className="muted">{i.agent_name}</span></td>}
                  <td>
                    <span className={`state ${STEP_CLASS[i.step]}`}>{STEP_LABEL[i.step]}</span>
                    {i.form?.priority && <> <PriorityTag p={i.form.priority} /></>}
                    {i.round > 1 && <span className="muted small"> · جولة {i.round}</span>}
                    {i.came_back && <span className="tag">عاد العميل</span>}
                  </td>
                  <td>{i.callback ? <>   {/* fix J: callback + "did the customer answer" in one cell */}
                    {formatDateTime(i.callback.at)} <span className={`state ${CB_CLASS[i.callback.status]}`}>{CALLBACK_LABEL[i.callback.status]}</span>
                    {i.callback.time && <span className="muted small"> · اتصل {i.callback.by_ext}
                      {i.callback.answered ? " · ردّ العميل" : " · لم يرد"}
                      {!i.callback.answered && ringText(i.callback.ring_seconds, i.callback.hangup_by, false)
                        ? " · " + ringText(i.callback.ring_seconds, i.callback.hangup_by, false) : ""}</span>}
                    {i.callback.early_tries && <div className="small bad-text">{earlyText(i.callback.early_tries)}</div>}
                  </> : "–"}</td>
                  <td className="nowrap muted small">
                    {i.step === "note_needed" && i.note_deadline ? formatDateTime(i.note_deadline)
                      : i.solve_by ? <>الحل: {formatDateTime(i.solve_by)}</>
                      : i.step === "required" || i.step === "overdue" ? formatDateTime(i.deadline) : "–"}
                  </td>
                  <td><MyNote callId={i.call_id} text={i.my_note} onSaved={() => setTick((n) => n + 1)} onFail={fail} /></td>
                </tr>
              ))}
              {data && data.items.length === 0 && (
                <tr><td colSpan={staff ? 7 : 6} className="center muted">
                  {state === "action" ? "لا يوجد عمل مطلوب الآن" : "لا يوجد شيء لهذا الاختيار"}</td></tr>
              )}
            </tbody>
          </table>
          {data?.truncated && <p className="muted small">تظهر أول 500 فقط: ضيّق الفترة.</p>}
        </div>
      )}
      {open !== null && (   /* the call opens in the side panel over this list; ✕ closes it and refreshes the list */
        <CallsPage key={`${open}-${fromShare}`} me={me} onLogout={onLogout} onExpired={onExpired} detailOnly embedded openCallId={open} fromShare={fromShare}
          onFollowups={() => { setOpen(null); setTick((n) => n + 1); }} />
      )}
    </div>
  );
}
