// "المتابعات": agents see their own work (forms, callbacks, notes after a callback) and supervisor notes; staff see
// everyone's (agent-supervisor spec; steps, counters, "needs action" view: follow-up stages spec).
import { useCallback, useEffect, useState } from "react";
import { api, Unauthorized, type Agent, type FollowupItem, type FollowupList, type Me } from "./api";
import {
  CALLBACK_LABEL, PRIORITIES, PRIORITY_LABEL, STEP_CLASS, STEP_LABEL, earlyText, formatDateTime, forwardWhere,
  riyadhToday, ringText,
} from "./format";
import { CB_CLASS, PriorityTag, RESP_LABEL } from "./WorkPanels";
import { CallsPage } from "./CallsPage";
import { SharesTab } from "./SharesTab";

interface Props { me: Me; onCalls?: () => void;   // agents: no calls page (follow-ups is their only page)
  onLogout: () => void; onExpired: () => void }

const STATE_OPTIONS: [string, string][] = [
  ["action", "يحتاج عملاً"], ["forms", "نماذج مطلوبة"], ["notes", "ملاحظات مطلوبة"],
  ["callbacks_today", "معاودات اليوم"], ["missed", "معاودات فائتة"], ["open", "متابعات مفتوحة"], ["solved", "حُلّت"],
  ["came_back", "عاد العميل بعد الحل"], ["no_followup", "لا تحتاج متابعة"],
  ["forward_solved_today", "حُلّت بالتحويل اليوم"], ["questions", "أسئلة تنتظر ردك"], ["", "الكل"],
];

/** D3: "سؤال من «name»" when the person holding the forwarded problem asked something. */
function Asked({ i }: { i: FollowupItem }) {
  const q = i.forward?.questions;
  if (!q?.length) return null;
  const who = q[q.length - 1].from;
  return <span className="state bad">سؤال من «{who ?? "المسؤول"}»{q.length > 1 ? ` (${q.length})` : ""}</span>;
}
const LOOKBACK_DAYS = 62;   // same as the server's look-back for unfinished work
const REFRESH_MS = 60_000;
const SOON_MS = 60 * 60_000;

function daysAgo(n: number): string {
  const [y, m, d] = riyadhToday().split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - n)).toISOString().slice(0, 10);
}

/** Row colour (fix E): red = missed / overdue, yellow = callback within the hour or a note to write. */
function urgency(i: FollowupItem, now: number): string {
  if (i.forward?.questions?.length) return "row-bad";       // D3: the person holding it waits for an answer
  if (i.step === "callback_missed" || i.step === "overdue" || i.step === "solution_overdue") return "row-bad";
  if (i.step === "waiting_solution" && i.solve_by && new Date(i.solve_by).getTime() - now <= SOON_MS) return "row-warn";
  if (i.step === "note_needed") return "row-warn";
  if (i.step === "waiting_callback" && i.callback && new Date(i.callback.at).getTime() - now <= SOON_MS) return "row-warn";
  return "";
}

// ---------- supervisor one-line view (requirement 15) + "تحتاج حل" (requirement 11) ----------

/** Who must solve it now: the latest entry's responsible person (only while not solved). */
function whoSolves(i: FollowupItem): string {
  const last = i.history[i.history.length - 1];
  if (!last || last.solved !== false) return "–";
  if (i.forward?.holders?.length && i.step !== "forward_solved") return i.forward.holders.join("، ");   // the chain
  return last.responsible ?? RESP_LABEL[last.responsible_kind ?? ""] ?? "–";
}

function caseState(i: FollowupItem): [string, string] {
  if (i.step === "solved_first" || i.step === "solved_after" || i.step === "forward_solved") return ["حُلّت", "ok"];
  if (i.step === "forward_unconfirmed") return ["حُلّت غير مؤكدة", "warn"];
  if (i.step === "no_followup") return ["لا تحتاج متابعة", "off"];
  if (i.step === "required" || i.step === "overdue") return ["لم يُعبّأ", i.step === "overdue" ? "bad" : "off"];
  return ["مفتوحة", "warn"];
}

/** The agent's text of the problem, cut to one line (full text on hover). */
function Problem({ i }: { i: FollowupItem }) {
  const t = i.form?.what_happened;
  return t ? <span className="problem" title={t}>{t}</span> : <>–</>;
}

function nextTime(i: FollowupItem): string {
  if (i.step === "waiting_callback" || i.step === "callback_missed" || i.step === "no_number") return i.callback ? `المعاودة: ${formatDateTime(i.callback.at)}` : "";
  if (i.solve_by) return `الحل: ${formatDateTime(i.solve_by)}`;
  if (i.step === "note_needed" && i.note_deadline) return `الملاحظة قبل: ${formatDateTime(i.note_deadline)}`;
  return "";
}

function Unsolved({ items, truncated, staff, onOpen }:
  { items: FollowupItem[]; truncated?: boolean; staff: boolean; onOpen: (id: number) => void }) {
  return (
    <section className="panel unsolved">
      <h2>تحتاج حل ({items.length}{truncated ? "+" : ""})</h2>
      <p className="muted small">كل مشكلة لم تُحل، مهما كانت التواريخ، حتى تُكتب ملاحظة بأنها حُلّت.</p>
      {items.length === 0 ? <p className="muted">لا توجد مشاكل مفتوحة</p> : (
        <ul>
          {items.map((i) => (
            <li key={i.call_id} onClick={() => onOpen(i.call_id)}>
              <div className="line">
                <PriorityTag p={i.form?.priority} />
                <span className={`state ${STEP_CLASS[i.step]}`}>{STEP_LABEL[i.step]}</span>
                {(i.step === "forwarded" || i.step === "forward_overdue") && forwardWhere(i.forward)
                  && <span className="small">{forwardWhere(i.forward)}</span>}
                {i.forward?.status === "pending" && <span className="muted small">لم يُرسل بعد</span>}
                <Asked i={i} />
                <Number_ n={i.customer_number} />
              </div>
              <div className="line small">
                {staff && <span>{i.agent_ext} <span className="muted">{i.agent_name}</span></span>}
                <span className="muted">{formatDateTime(i.start_time)}</span>
              </div>
              {staff && <div className="line small"><span className="state warn">مفتوحة</span><Problem i={i} />
                <span className="muted">· يحلّها: {whoSolves(i)}</span></div>}
              {nextTime(i) && <div className="small muted">{nextTime(i)}</div>}
            </li>
          ))}
        </ul>
      )}
      {truncated && <p className="muted small">تظهر أول 500 فقط.</p>}
    </section>
  );
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
  const [tab, setTab] = useState<"work" | "shares">("work");   // spec 2026-11-22-shares-tab
  const [sharesOpen, setSharesOpen] = useState(0);
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
  useEffect(() => {   // the tab label shows how many shared calls are still open (also while on the other tab)
    if (tab === "shares") return;      // the tab itself reports the count
    api.shares({ box: "open" }).then((d) => setSharesOpen(d.open)).catch(fail);
  }, [tab, tick, fail]);

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

      <div className="chips" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "work"} className={`chip${tab === "work" ? " on" : ""}`}
          onClick={() => setTab("work")}>المتابعات</button>
        <button type="button" role="tab" aria-selected={tab === "shares"}
          className={`chip${tab === "shares" ? " on" : ""}${sharesOpen ? " st-warn" : ""}`} onClick={() => setTab("shares")}>
          {staff ? "المكالمات المشاركة مع الموظفين" : "ملاحظات المشرف"} {sharesOpen > 0 && <b>{sharesOpen}</b>}</button>
      </div>

      {tab === "shares" ? (
        <SharesTab staff={staff} agents={agents} tick={tick} onOpenCall={(id) => onOpenCall(id, true)}
          onExpired={onExpired} onCount={setSharesOpen} />
      ) : <>
      {data && !data.linked && (
        <p className="error">حسابك غير مرتبط بتحويلة في فريق خدمة العملاء أو ما بعد البيع، لذلك لا تظهر لك متابعات. اطلب من مدير النظام ربط تحويلتك.</p>
      )}

      {c && data?.settings.followup_start && data.linked && (
        <div className="counters">   {/* click = show only that kind; click again = back to "needs action" */}
          {([["forms", c.forms, "نماذج مطلوبة", "bad"], ["notes", c.notes, "ملاحظات مطلوبة", "warn"],
             ["callbacks_today", c.callbacks_today, "معاودات اليوم", ""], ["missed", c.missed, "معاودات فائتة", "bad"],
             ["no_followup", c.no_followup, "لا تحتاج متابعة", ""],
             ["forward_solved_today", c.forward_solved_today ?? 0, "حُلّت بالتحويل اليوم", "ok"],
             ["questions", c.questions ?? 0, staff ? "أسئلة تنتظر رد موظف المكالمة" : "أسئلة تنتظر ردك", "bad"]] as const)
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
        <div className="fu-layout">
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>الوقت</th><th>رقم العميل</th>{staff && <><th>الموظف</th><th>المشكلة</th></>}<th>المرحلة</th>
              {staff && <><th>يحلّها</th><th>الحالة</th></>}<th>معاودة الاتصال</th><th>آخر موعد</th><th>ملاحظتي</th>
            </tr></thead>
            <tbody>
              {data?.items.map((i) => (
                <tr key={i.call_id} className={urgency(i, now)} onClick={() => onOpenCall(i.call_id)}>
                  <td className="nowrap">{formatDateTime(i.start_time)}</td>
                  <td><Number_ n={i.customer_number} /></td>
                  {staff && <><td>{i.agent_ext} <span className="muted">{i.agent_name}</span></td><td><Problem i={i} /></td></>}
                  <td>
                    <span className={`state ${STEP_CLASS[i.step]}`}>{STEP_LABEL[i.step]}</span>
                    {i.forward?.status === "pending" && <span className="muted small"> · لم يُرسل بعد</span>}
                    {i.forward?.questions?.length ? <> <Asked i={i} /></> : null}
                    {i.form?.priority && <> <PriorityTag p={i.form.priority} /></>}
                    {i.round > 1 && <span className="muted small"> · جولة {i.round}</span>}
                    {i.came_back && <span className="tag">عاد العميل</span>}
                  </td>
                  {staff && <><td>{whoSolves(i)}</td><td><span className={`state ${caseState(i)[1]}`}>{caseState(i)[0]}</span></td></>}
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
                <tr><td colSpan={staff ? 10 : 6} className="center muted">
                  {state === "action" ? "لا يوجد عمل مطلوب الآن" : "لا يوجد شيء لهذا الاختيار"}</td></tr>
              )}
            </tbody>
          </table>
          {data?.truncated && <p className="muted small">تظهر أول 500 فقط: ضيّق الفترة.</p>}
        </div>
        {data && <Unsolved items={data.unsolved} truncated={data.unsolved_truncated} staff={staff} onOpen={(id) => onOpenCall(id)} />}
        </div>
      )}
      </>}
      {open !== null && (   /* the call opens in the side panel over this list; ✕ closes it and refreshes the list */
        <CallsPage key={`${open}-${fromShare}`} me={me} onLogout={onLogout} onExpired={onExpired} detailOnly embedded openCallId={open} fromShare={fromShare}
          onFollowups={() => { setOpen(null); setTick((n) => n + 1); }} />
      )}
    </div>
  );
}
