// Call-panel boxes of the agent-supervisor spec: follow-up form, agent summary, share with a note, delete content.
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  api, OPEN_CHAT, Refused, Unauthorized,
  type AgentSummary, type CallShares, type Forward, type ForwardOption, type FollowupIn, type FollowupView,
  type HistoryEntry, type Me, type NoteIn, type Person, type StudentLookup, type TrackerItem,
} from "./api";
import {
  CALLBACK_LABEL, HOP_LEVEL, HOP_RESULT, PROOF_REASON, PRIORITIES, PRIORITY_CLASS, PRIORITY_LABEL, STEP_CLASS, type Priority, STEP_LABEL, STEP_NEXT, earlyText, formatDateTime, ringText,
} from "./format";

export const CB_CLASS: Record<string, string> = { on_time: "ok", waiting: "off", late: "warn", missed: "bad", no_number: "off" };
export const FORM_CLASS: Record<string, string> = { on_time: "ok", missing: "off", late: "warn", overdue: "bad" };

function useFail(onExpired: () => void, setError: (m: string) => void) {
  return (e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else if (e instanceof Refused) setError(e.message);
    else setError("تعذر الاتصال بالخادم. أعد المحاولة.");
  };
}

// ---------- follow-up form + steps (agent-supervisor spec; steps and notes: follow-up stages spec) ----------

type Solved = "" | "yes" | "no";
// resp: "user:<id>" / "customer" / "other_department" / "forward:<id>" (form) / "forward" (note on a forwarded problem)
interface Problem { what: string; solved: Solved; resp: string; fnote: string }
const EMPTY: Problem = { what: "", solved: "", resp: "", fnote: "" };

/** Check the fields; returns the body part or an error text. `askPriority`: this is the first form, so the
 * priority is required and sent with it. No expected solve / callback time any more (user, 2026-11-23). */
function problemBody(p: Problem, priority = "", askPriority = false
): { body?: Omit<FollowupIn, "no_followup">; error?: string } {
  if (!p.what.trim()) return { error: "اكتب ما حدث في المكالمة" };
  if (askPriority && !priority) return { error: "اختر الأولوية" };
  if (!p.solved) return { error: "اختر: هل حُلّت المشكلة؟" };
  const prio = askPriority ? { priority } : {};
  if (p.solved === "yes") return { body: { what_happened: p.what.trim(), solved: true, ...prio } };
  if (!p.resp) return { error: "اختر من يجب أن يحلّها" };
  const [kind, uid] = p.resp.split(":");
  if (kind === "forward")
    return { body: { what_happened: p.what.trim(), solved: false, ...prio, responsible_kind: kind,
      ...(uid ? { forward_person_id: Number(uid), forward_note: p.fnote.trim() } : {}) } };
  return { body: { what_happened: p.what.trim(), solved: false, ...prio, responsible_kind: kind,
    responsible_user_id: uid ? Number(uid) : null } };
}

const WHAT_MAX = 500;      // = the textarea and the server (followups.validate)

/** «ماذا حدث؟» text made from the AI summary of the call: the summary, then the action points, cut to 500. */
export function summaryText(a: AgentSummary["analysis"]): string {
  if (!a || a.status !== "done") return "";
  const items = (a.action_items ?? []).map((x) => x.trim()).filter(Boolean).map((x) => `- ${x}`);
  return [(a.summary ?? "").trim(), ...items].filter(Boolean).join("\n");
}

/** Put `add` in the box: an empty box gets it, a box with the agent's own text keeps it and gets it below. */
export function fillWhat(current: string, add: string): string {
  const out = current.trim() ? `${current.trimEnd()}\n${add}` : add;
  return out.length <= WHAT_MAX ? out : out.slice(0, WHAT_MAX - 1) + "…";
}

/** Feature 1 (docs/followup-form-and-forward.md): fill «ماذا حدث؟» from the call summary. It only fills the text:
 *  the agent reads and fixes it, then «حفظ» saves (and sends it to the forward person, if one is picked). */
function FillFromSummary({ summary, what, onFill }:
  { summary: AgentSummary["analysis"] | undefined; what: string; onFill: (text: string) => void }) {
  const [filled, setFilled] = useState(false);
  if (!summary || summary.status === "error") return null;         // no analysis for this call: no button
  const text = summaryText(summary);
  if (summary.status !== "done" || !text)
    return <div className="fill-row"><button type="button" disabled>املأ من ملخص المكالمة</button>
      <span className="muted small">الملخص قيد التجهيز…</span></div>;
  return (
    <div className="fill-row">
      <button type="button" onClick={() => { onFill(fillWhat(what, text)); setFilled(true); }}>
        {what.trim() ? "أضف ملخص المكالمة للنص" : "املأ من ملخص المكالمة"}</button>
      {filled && <span className="muted small">راجع النص وعدّله قبل الحفظ: يُحفظ ويُرسل كما هو.</span>}
    </div>
  );
}

function ProblemFields({ p, set, options, label, afterText, forwardOptions = [], forwardedTo, summary }:
  { p: Problem; set: (p: Problem) => void; options: Person[]; label: string; afterText?: ReactNode;
    forwardOptions?: ForwardOption[];   // first form, link on: the branch supervisors
    forwardedTo?: Forward | null;       // a note on a forwarded problem: only the supervisor closes it
    summary?: AgentSummary["analysis"] }) {   // first form only: the «املأ من ملخص المكالمة» button
  const toSupervisor = p.resp.startsWith("forward:");
  const [fq, setFq] = useState("");          // search in the ~474 employees of the other system
  const q = fq.trim();
  const shown = forwardOptions.filter((o) => !q || o.name.includes(q) || o.branch.includes(q)
    || p.resp === `forward:${o.id}`);
  const branches = [...new Set(shown.map((o) => o.branch))];
  return (
    <>
      <label>{label}
        <textarea value={p.what} maxLength={WHAT_MAX} rows={3} onChange={(e) => set({ ...p, what: e.target.value })} />
      </label>
      {summary !== undefined && <FillFromSummary summary={summary} what={p.what} onFill={(what) => set({ ...p, what })} />}
      {afterText}
      {forwardedTo ? <p className="muted small">المشكلة محوّلة إلى {(forwardedTo.holders?.length ? forwardedTo.holders
        : [forwardedTo.name ?? ""]).join("، ")}: {(forwardedTo.holders?.length ?? 0) > 1 ? "هم من يغلقونها" : "هو من يغلقها"}.</p> : (
        <div className="row">
          هل حُلّت المشكلة؟
          <label><input type="radio" checked={p.solved === "yes"} onChange={() => set({ ...p, solved: "yes" })} /> نعم</label>
          <label><input type="radio" checked={p.solved === "no"} onChange={() => set({ ...p, solved: "no" })} /> لا</label>
        </div>
      )}
      {p.solved === "no" && (
        <>
          {!forwardedTo && forwardOptions.length > 0 && <label>بحث عن موظف للتحويل (الاسم أو الفرع)
            <input value={fq} maxLength={50} onChange={(e) => setFq(e.target.value)} />
          </label>}
          {!forwardedTo && <label>من يجب أن يحلّها؟
            <select value={p.resp} onChange={(e) => set({ ...p, resp: e.target.value })}>
              <option value="">اختر…</option>
              {options.map((o) => <option key={o.id} value={`user:${o.id}`}>{o.username}{o.ext ? ` · ${o.ext}` : ""}</option>)}
              <option value="customer">العميل</option>
              <option value="other_department">قسم آخر</option>
              {branches.map((b) => <optgroup key={b} label={`تحويل · ${b}`}>
                {shown.filter((o) => o.branch === b).map((o) => <option key={o.id} value={`forward:${o.id}`}>
                  {o.name}{o.supervisor ? " (مشرف الفرع)" : ""}{o.manager ? " (الإدارة)" : ""}</option>)}
              </optgroup>)}
            </select>
          </label>}
          {toSupervisor && (() => {
            const o = forwardOptions.find((x) => p.resp === `forward:${x.id}`);
            return <p className="muted small">{o?.manager ? "عنده 24 ساعة (الإدارة: آخر مستوى)."
              : o?.supervisor ? "عنده 24 ساعة ومكالمة جيدة للعميل، ثم تنتقل تلقائيًا للإدارة."
              : "عنده 24 ساعة، ثم تنتقل تلقائيًا لمشرف فرعه، ثم للإدارة."}</p>;
          })()}
          {toSupervisor && <label>ملاحظة للمسؤول (اختياري)
            <textarea value={p.fnote} maxLength={500} rows={2} onChange={(e) => set({ ...p, fnote: e.target.value })} />
          </label>}
        </>
      )}
    </>
  );
}

const NOTE_STEPS: string[] = ["note_needed", "waiting_solution", "solution_overdue", "forwarded", "forward_overdue",
  "forward_failed"];
export const RESP_LABEL: Record<string, string> = { customer: "العميل", other_department: "قسم آخر", forward: "تحويل" };

/** Part 4: where the forwarded problem is (ticket made / still trying / refused by the other system). */
function ForwardLine({ f }: { f: Forward }) {
  const who = `${f.name ?? "المسؤول"}${f.branch ? ` (${f.branch})` : ""}`;
  const sup = f.hops?.filter((h) => h.level === 2 && h.result !== "pending").pop();
  if (f.tracker?.length)
    return <>
      <p>حُوّلت إلى {who}{f.ticket_number ? ` · تذكرة رقم ${f.ticket_number}` : ""}</p>
      <Tracker items={f.tracker} />
      {f.due_at && <p className="small muted">المهلة عند {f.holders?.length ? f.holders.join("، ") : "من يحملها"}:
        حتى {formatDateTime(f.due_at)} (24 ساعة لكل شخص)</p>}
      {f.status === "failed" && <p className="bad-text">عدّل النموذج واختر من جديد.</p>}
    </>;
  if (f.status === "sent")
    return <>
      <p>حُوّلت إلى {who}{f.ticket_number ? ` · تذكرة رقم ${f.ticket_number}` : ""}
        {f.forwarded_at && <span className="muted small"> · {formatDateTime(f.forwarded_at)}</span>}</p>
      {f.hops && f.hops.length > 0 && <ol className="small">
        {f.hops.map((h, i) => <li key={i}>{HOP_LEVEL[h.level]}: {h.name ?? "–"}
          {h.sent_at && <span className="muted"> · من {formatDateTime(h.sent_at)}</span>}
          {" · "}<span className={h.result === "solved" ? "state ok" : h.result === "passed_on" || h.result === "failed"
            ? "state bad" : "state off"}>{HOP_RESULT[h.result] ?? h.result}</span>
          {h.error && <span className="muted"> ({h.error})</span>}</li>)}
      </ol>}
      {f.due_at && <p className="small">المهلة حتى {formatDateTime(f.due_at)}</p>}
      {sup?.closed_at && f.proof && <p>أغلقها مشرف الفرع {formatDateTime(sup.closed_at)}{" "}
        {f.proof.ok ? <span className="state ok">مؤكد ✓</span>
          : <span className="state warn">غير مؤكد: {PROOF_REASON[f.proof.reason ?? ""] ?? "–"}</span>}</p>}
      {f.solution && <p className="small">الحل: {f.solution}</p>}
      {f.proof && f.proof.calls.length > 0 && <ul className="small muted">
        {f.proof.calls.map((c) => <li key={c.call_id}>اتصل المشرف {formatDateTime(c.time)} · {c.answered
          ? `ردّ العميل · ${c.talk_seconds} ث` : "لم يرد"}{c.score !== null ? ` · الرضا ${c.score}/10` : ""}</li>)}
      </ul>}
    </>;
  if (f.status === "failed")
    return <p className="bad-text">لم تُحوَّل إلى {who}: {f.error ?? "رفض النظام الآخر"}. عدّل النموذج واختر من جديد.</p>;
  return <p className="muted">لم يُرسل بعد إلى {who}، نحاول مرة أخرى{f.error ? ` (${f.error})` : ""}.</p>;
}

const TK_ICON: Record<TrackerItem["state"], string> = { done: "✓", current: "●", late: "!", bad: "✗", future: "○" };
const MSG_STATUS: Record<string, string> = { pending: "بانتظار الإرسال", waiting: "بانتظار إعادة فتح التذكرة",
  failed: "لم يُرسل" };

/** Part 4 D3 (req 17): the forwarded problem step by step, like a delivery tracker. Questions and what we wrote
 *  on the ticket sit at their time; the current step is highlighted, red with "متأخر" after the 24 h. */
function Tracker({ items }: { items: TrackerItem[] }) {
  return (
    <ol className="tracker">
      {items.map((t, i) => (
        <li key={i} className={`tk-${t.state} tk-${t.kind}`} aria-current={t.state === "current" || t.state === "late" ? "step" : undefined}>
          <span className="tk-dot" aria-hidden="true">{t.kind === "question" ? "؟" : TK_ICON[t.state]}</span>
          <div>
            <b>{t.label}</b>{t.at && <span className="muted small"> · {formatDateTime(t.at)}</span>}
            {t.status && MSG_STATUS[t.status] && <span className={`small ${t.status === "failed" ? "bad-text" : "muted"}`}>
              {" · "}{MSG_STATUS[t.status]}{t.error ? ` (${t.error})` : ""}</span>}
            {t.detail && <p className="small">{t.detail}</p>}
            {t.solution && <p className="small">الحل: {t.solution}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Part 4 D3 (req 18): the agent answers the person holding the forwarded problem (written on the ticket), or
 *  marks the question seen when no answer is needed. */
function AnswerBox({ f, busy, onSend }: { f: Forward; busy: boolean;
  onSend: (kind: "answer" | "seen", text?: string) => Promise<boolean> }) {
  const [text, setText] = useState("");
  const qs = f.questions ?? [];
  if (qs.length === 0) return null;
  return (
    <fieldset disabled={busy} className="form answer-box">
      <legend>سؤال ينتظر ردك</legend>
      {qs.map((q) => <p key={q.id} className="question"><b>{q.from ?? "المسؤول"}</b>
        {q.at && <span className="muted small"> · {formatDateTime(q.at)}</span>}<br />{q.text}</p>)}
      <label>ردّك (يُكتب في التذكرة عند {f.holders?.length ? f.holders.join("، ") : f.name ?? "المسؤول"})
        <textarea value={text} maxLength={500} rows={3} onChange={(e) => setText(e.target.value)} /></label>
      <span className="muted small">{text.length}/500</span>
      <div className="row">
        <button type="button" className="primary" disabled={!text.trim()}
          onClick={async () => { if (await onSend("answer", text.trim())) setText(""); }}>إرسال الرد</button>
        <button type="button" onClick={() => onSend("seen")}>اطّلعت، لا يحتاج رداً</button>
      </div>
    </fieldset>
  );
}

function History({ items }: { items: HistoryEntry[] }) {
  if (items.length === 0) return null;
  return (
    <ol className="history">
      {items.map((h) => (
        <li key={h.round}>
          <b>{h.round === 1 ? "النموذج" : `ملاحظة متابعة ${h.round - 1}`}</b>
          {h.priority && <> <PriorityTag p={h.priority} /></>}
          <span className="muted small"> · {formatDateTime(h.at)}{h.by ? ` · ${h.by}` : ""}</span>
          {h.no_followup ? <p>لا تحتاج متابعة.</p> : <>
            <p>{h.what_happened}</p>
            <p className="small">
              {h.solved ? <span className="state ok">حُلّت</span> : <span className="state warn">لم تُحل</span>}
              {h.solved === false && <> · المسؤول: {h.responsible ?? RESP_LABEL[h.responsible_kind ?? ""] ?? "–"}
                {h.responsible_kind === "forward" && h.responsible ? " (تحويل)" : ""}</>}
              {h.solved === false && h.expected_at && <> · الحل المتوقع: {formatDateTime(h.expected_at)}</>}
              {h.callback_at && <> · معاودة الاتصال: {formatDateTime(h.callback_at)}</>}
              {h.callback_status && <> <span className={`state ${CB_CLASS[h.callback_status]}`}>{CALLBACK_LABEL[h.callback_status]}</span></>}
              {h.callback_call && <span className="muted"> · اتصل {h.callback_call.by_ext ?? "–"} {formatDateTime(h.callback_call.time)}
                {h.callback_call.answered === true ? " · ردّ العميل" : h.callback_call.answered === false ? " · لم يرد" : ""}
                {h.callback_call.answered !== true && ringText(h.callback_call.ring_seconds, h.callback_call.hangup_by, !!h.callback_call.call_answered)
                  ? " · " + ringText(h.callback_call.ring_seconds, h.callback_call.hangup_by, !!h.callback_call.call_answered) : ""}</span>}
              {h.early_tries && <span className="bad-text"> · {earlyText(h.early_tries)}</span>}
            </p>
          </>}
        </li>
      ))}
    </ol>
  );
}

/** The training look-up (spec "Part 4: the training lookup"): who the caller is in the training system. */
function StudentBox({ d, picked, searched, onSearch }: {
  d: StudentLookup; picked: boolean; searched: boolean; onSearch: (q: string) => Promise<void>;
}) {
  // the caller's number finds nobody (another phone, a landline): the agent asks the student for the ID and searches
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const search = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || !q.trim()) return;
    setBusy(true);
    try { await onSearch(q.trim()); } finally { setBusy(false); }
  };
  const box = searched || (d.students.length === 0 && d.error !== "unavailable");
  const searchBox = box && (
    <form className="form student-search" onSubmit={search}>
      <label>ابحث عن الطالب برقم الهوية أو جواله المسجل
        <input value={q} onChange={(e) => setQ(e.target.value)} inputMode="numeric" maxLength={20}
          placeholder="رقم الهوية / الإقامة أو 05xxxxxxxx" disabled={busy} />
      </label>
      <button type="submit" disabled={busy || !q.trim()}>{busy ? "جارٍ البحث…" : "بحث"}</button>
    </form>
  );
  let msg = "";
  if (d.error === "unavailable") msg = "نظام التدريب لا يرد الآن: اختر المسؤول بنفسك.";
  else if (d.error === "bad_query") msg = "اكتب رقم الهوية أو الإقامة (10 أرقام) أو جوال الطالب (05xxxxxxxx).";
  else if (d.error === "not_mobile") msg = "رقم المتصل ليس جوالًا: اسأل الطالب عن رقم هويته وابحث به.";
  else if (d.students.length === 0) msg = searched ? "لا يوجد طالب بهذا الرقم في نظام التدريب."
    : "رقم المتصل غير مسجل لطالب في نظام التدريب: اسأل الطالب عن رقم هويته وابحث به.";
  if (d.students.length === 0) return <div className="student-box"><p className="muted small">{msg}</p>{searchBox}</div>;
  return (
    <div className="student-box">
      {d.students.map((st, i) => <div key={i}>
        <b>{st.student || "طالب"}</b>
        {st.placements.map((p, j) => <p key={j} className="small">
          {[p.diploma, p.level && `المستوى ${p.level}`, p.section, p.branch, p.status].filter(Boolean).join(" · ")}
          <br />المدرب: {p.trainers.length ? p.trainers.join("، ") : "غير محدد بعد"}
        </p>)}
      </div>)}
      {picked && d.suggestion && <p className="small">المقترح للتحويل: <b>{d.suggestion.name}</b>
        {d.suggestion.kind === "trainer" ? " (المدرب)" : " (مشرف فرع الطالب)"}. يمكنك تغييره.</p>}
      {searchBox}
    </div>
  );
}

export function PriorityTag({ p }: { p: Priority | null | undefined }) {
  if (!p) return null;
  return <span className={`state prio ${PRIORITY_CLASS[p]}`} title="الأولوية">{PRIORITY_LABEL[p]}</span>;
}

export function FollowupPanel({ callId, me, onExpired }: { callId: number; me: Me; onExpired: () => void }) {
  // supervisors/admins only watch: the agent fills the form and the notes (no form, no note box, no "your task")
  const staff = me.role === "admin" || me.role === "supervisor";
  const [view, setView] = useState<FollowupView | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);          // no double save (double click)
  const [form, setForm] = useState<Problem>(EMPTY);
  const [prio, setPrio] = useState("");             // first form only (set once)
  const [note, setNote] = useState<Problem>(EMPTY);
  const [student, setStudent] = useState<StudentLookup | null>(null);
  const [summary, setSummary] = useState<AgentSummary["analysis"]>(null);
  const fail = useFail(onExpired, setError);

  const load = (v: FollowupView) => {
    setView(v);
    // a note on a forwarded problem: never "solved", the person stays (the server checks both)
    setNote(v.needed && v.forward && v.forward.status !== "failed" ? { ...EMPTY, solved: "no", resp: "forward" } : EMPTY);
    if (!v.needed || !v.form) { setPrio(""); return; }
    const f = v.form;
    setPrio(f.priority ?? "");
    setForm({ what: f.what_happened ?? "", solved: f.no_followup || f.solved === null ? "" : f.solved ? "yes" : "no",
      resp: f.responsible_kind === "user" ? `user:${f.responsible_user_id}`
        : f.responsible_kind === "forward" ? `forward:${f.forward_person_id}` : f.responsible_kind ?? "",
      fnote: f.forward_note ?? "" });
  };
  useEffect(() => { api.followup(callId).then(load).catch(fail); /* eslint-disable-next-line */ }, [callId]);
  // the agent's own open first form (or one re-opened after a failed forward): staff never see the box
  const lookUp = !staff && !!view?.needed && !!view.training_enabled && view.can_edit
    && (!view.form || view.step === "forward_failed");
  const [searched, setSearched] = useState(false);  // the box shows what the agent searched, not the caller's number
  useEffect(() => {     // the box only: an error here never blocks the form
    setStudent(null); setSearched(false);
    if (lookUp) api.student(callId).then(setStudent).catch((e) => { if (e instanceof Unauthorized) onExpired(); });
    /* eslint-disable-next-line */ }, [callId, lookUp]);
  const searchStudent = async (q: string) => {
    try { setStudent(await api.studentSearch(callId, q)); setSearched(true); }
    catch (e) { if (e instanceof Unauthorized) onExpired(); }
  };
  // the call summary for «املأ من ملخص المكالمة»: only while the agent can fill the form; an error only hides the button
  const wantSummary = !staff && !!view?.needed && view.can_edit;
  useEffect(() => {
    setSummary(null);
    if (wantSummary) api.agentSummary(callId).then((d) => setSummary(d.analysis))
      .catch((e) => { if (e instanceof Unauthorized) onExpired(); });
    /* eslint-disable-next-line */ }, [callId, wantSummary]);
  // first form, "not solved", nobody picked yet: pre-pick the suggested person (the agent can change it)
  const sug = student?.suggestion;
  const sugOk = !!sug && !!view?.needed && !view.form && !!view.forward_enabled
    && (view.forward_options ?? []).some((o) => o.id === sug.person_id);
  useEffect(() => {
    if (sugOk && form.solved === "no" && !form.resp) setForm({ ...form, resp: `forward:${sug!.person_id}` });
    /* eslint-disable-next-line */ }, [sugOk, form.solved]);

  if (!view || !view.needed) return null;
  const run = async (call: () => Promise<FollowupView>, done: string) => {
    if (busy) return;
    setError(""); setSaved(""); setBusy(true);
    try { load(await call()); setSaved(done); } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const saveForm = (body: FollowupIn) => run(() => api.saveFollowup(callId, body), "تم حفظ النموذج.");
  const submitForm = () => {
    const r = problemBody(form, prio, true);
    if (r.error) { setError(r.error); return; }
    saveForm({ no_followup: false, ...r.body });
  };
  const submitNote = () => {
    const r = problemBody(note);
    if (r.error) { setError(r.error); return; }
    run(() => api.addNote(callId, { ...(r.body as Omit<NoteIn, "round">), round: view.round + 1 }), "تم حفظ الملاحظة.");
  };
  const sendAnswer = async (kind: "answer" | "seen", text?: string): Promise<boolean> => {
    if (busy) return false;
    setError(""); setSaved(""); setBusy(true);
    try {
      const upto = Math.max(0, ...(view.forward?.questions ?? []).map((q) => q.id));   // what is on screen
      load(await api.forwardMessage(callId, kind === "answer" ? { kind, text, upto } : { kind, upto }));
      setSaved(kind === "answer" ? "حُفظ الرد، ويُكتب في التذكرة خلال دقائق." : "تم.");
      return true;
    } catch (e) { fail(e); return false; } finally { setBusy(false); }
  };
  const ro = !view.can_edit || busy;
  const noteStep = !staff && NOTE_STEPS.includes(view.step);
  // round 1 without a callback: the form stays editable (deadline rules on the server) next to the update box
  // a failed forward: the form opens again so the agent picks another way (review D1)
  const formOpen = !staff && (view.step === "forward_failed"
    || (view.round === 1 && view.history.length <= 1 && view.step !== "note_needed"));
  const next = STEP_NEXT[view.step];
  return (
    <section className={`panel work step-${STEP_CLASS[view.step]}`}>
      <h3>متابعة المكالمة <span className={`state ${STEP_CLASS[view.step]}`}>{STEP_LABEL[view.step]}</span>
        {view.round > 1 && <span className="muted small"> · الجولة {view.round}</span>}</h3>
      {next && <p className="next-action">{staff ? "المطلوب من الموظف" : "المطلوب منك"}: <b>{next}</b></p>}
      {staff && view.history.length === 0 && <p className="muted small">لم يعبّئ الموظف النموذج بعد.</p>}
      {(view.step === "required" || view.step === "overdue") &&
        <p className="muted small">آخر موعد للتعبئة: {formatDateTime(view.deadline)}</p>}
      {view.solve_by && <p className="muted small">موعد الحل المتوقع: {formatDateTime(view.solve_by)}</p>}
      {view.step === "note_needed" && view.note_deadline &&
        <p className="muted small">آخر موعد للملاحظة: {formatDateTime(view.note_deadline)}</p>}
      {view.callback && view.step !== "note_needed" && (
        <p>معاودة الاتصال: {formatDateTime(view.callback.at)}{" "}
          <span className={`state ${CB_CLASS[view.callback.status]}`}>{CALLBACK_LABEL[view.callback.status]}</span>
        </p>
      )}
      {view.came_back && <p className="tag">اتصل العميل مرة أخرى خلال 72 ساعة بعد الحل</p>}
      {view.forward && <ForwardLine f={view.forward} />}
      {view.forward && !staff && <AnswerBox f={view.forward} busy={busy} onSend={sendAnswer} />}
      {view.forward && staff && (view.forward.questions?.length ?? 0) > 0 &&
        <p className="bad-text">سؤال من «{view.forward.questions![view.forward.questions!.length - 1].from ?? "المسؤول"}» ينتظر رد موظف المكالمة.</p>}

      {noteStep && (
        <fieldset disabled={busy} className="form note-box">
          <legend>{view.step === "note_needed"
            ? `ملاحظة بعد مكالمة المعاودة${view.callback?.by_ext ? ` (اتصل ${view.callback.by_ext}${view.callback.answered ? "، ردّ العميل" : "، لم يرد"})` : ""}`
            : "تحديث المتابعة"}</legend>
          {view.forward?.status === "sent" && <p className="muted small">تُكتب هذه الملاحظة أيضاً في التذكرة في نظام
            خدمة العملاء، ويراها {view.forward.holders?.length ? view.forward.holders.join("، ") : view.forward.name ?? "المسؤول"}.</p>}
          <ProblemFields p={note} set={setNote} options={view.options}
            forwardedTo={view.forward}
            label={view.step === "note_needed" ? "ماذا حدث في مكالمة المعاودة؟" : "ماذا حدث في المشكلة؟"} />
          <button type="button" className="primary" onClick={submitNote}>حفظ الملاحظة</button>
        </fieldset>
      )}

      <History items={view.history} />

      {formOpen && student && <StudentBox d={student} picked={sugOk} searched={searched} onSearch={searchStudent} />}
      {formOpen && <>
        {ro && view.lock_reason && <p className="muted small">لا يمكنك التعديل: {lockText(view.lock_reason)}</p>}
        <fieldset disabled={ro} className="form">
          {view.form && <legend>تعديل النموذج</legend>}
          {!view.form && <button type="button" onClick={() => saveForm({ no_followup: true })}>لا تحتاج متابعة (رقم خطأ، استفسار انتهى…)</button>}
          <ProblemFields p={form} set={setForm} options={view.options} label="ماذا حدث؟"
            forwardOptions={view.forward_enabled ? view.forward_options : []} summary={summary}
            afterText={<label>الأولوية
              <select value={prio} required onChange={(e) => setPrio(e.target.value)}>
                <option value="">اختر…</option>
                {PRIORITIES.map((k) => <option key={k} value={k}>{PRIORITY_LABEL[k]}</option>)}
              </select>
            </label>} />
          {/* «حفظ» also sends the problem when a forward person is picked: the label says so */}
          <button type="button" className="primary" onClick={submitForm}>{(() => {
            const to = form.solved === "no" && form.resp.startsWith("forward:")
              ? (view.forward_options ?? []).find((o) => form.resp === `forward:${o.id}`) : undefined;
            return to ? `حفظ وتحويل إلى ${to.name}` : "حفظ";
          })()}</button>
          {form.solved === "no" && form.resp.startsWith("forward:") &&
            <p className="muted small">عند الحفظ تُرسل المشكلة إلى نظام خدمة العملاء (تذكرة) ولا يمكن تعديل النموذج بعدها.</p>}
        </fieldset>
      </>}
      {saved && <p className="muted small">{saved}</p>}
      {error && <div className="error">{error}</div>}
    </section>
  );
}

function lockText(reason: string): string {
  if (reason.startsWith("after the form deadline")) return "انتهى وقت التعبئة";
  if (reason.startsWith("the callback time")) return "مرّ وقت معاودة الاتصال";
  if (reason.startsWith("forwarded to the branch supervisor")) return "حُوّلت المشكلة لمشرف الفرع";
  return "ليست مكالمتك";
}

// ---------- agent summary (summary only, never the estimate) ----------

export function AgentSummaryPanel({ callId, onExpired }: { callId: number; onExpired: () => void }) {
  const [d, setD] = useState<AgentSummary | null>(null);
  const [, setError] = useState("");
  const fail = useFail(onExpired, setError);
  useEffect(() => { api.agentSummary(callId).then(setD).catch(fail); /* eslint-disable-next-line */ }, [callId]);
  const a = d?.analysis;
  if (!a) return null;
  if (a.status !== "done") return <section className="panel work"><h3>ملخص المكالمة</h3><p className="muted">الملخص قيد التجهيز…</p></section>;
  return (
    <section className="panel work">
      <h3>ملخص المكالمة {a.topic_label && <span className="muted small">· {a.topic_label}</span>}</h3>
      <p>{a.summary}</p>
      {a.action_items && a.action_items.length > 0 && <ul>{a.action_items.map((x, i) => <li key={i}>{x}</li>)}</ul>}
    </section>
  );
}

// ---------- shares ----------

export function SharePanel({ callId, me, onExpired }: { callId: number; me: Me; onExpired: () => void }) {
  // spec 2026-11-25-share-chat: staff start a chat with an agent here; the chat itself runs in the popup (ChatDock)
  const [d, setD] = useState<CallShares | null>(null);
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const fail = useFail(onExpired, setError);
  const staff = me.role === "admin" || me.role === "supervisor";
  const reload = () => api.callShares(callId).then(setD).catch(fail);
  useEffect(() => { if (staff) reload(); /* eslint-disable-next-line */ }, [callId, staff]);
  if (!staff || !d) return null;     // agents: the chat pops up by itself
  const openChat = (id: number) => window.dispatchEvent(new CustomEvent(OPEN_CHAT, { detail: id }));
  const send = async () => {
    setError("");
    if (!to || !note.trim()) { setError("اختر الموظف واكتب الملاحظة"); return; }
    try {
      const sh = await api.share(callId, Number(to), note.trim());
      setNote(""); setTo(""); reload(); openChat(sh.id);
    } catch (e) { fail(e); }
  };
  return (
    <section className="panel work">
      <h3>مشاركة المكالمة مع موظف</h3>
      {d.items.map((s) => (
        <div key={s.id} className="share">
          <p className="muted small">
            إلى {s.to} · {formatDateTime(s.created_at)} · {s.status === "archived" ? "في الأرشيف" : "مفتوحة"}
            {s.unread > 0 && <> · <b>{s.unread} رسالة جديدة</b></>}
            <button type="button" className="link" onClick={() => openChat(s.id)}>فتح المحادثة</button>
            <button type="button" className="link danger" title="للمشاركة بالخطأ فقط: تُحذف ولا تُحتسب في مؤشرات الأداء. لإنهاء المحادثة استخدم «إغلاق المحادثة»."
              onClick={() => api.unshare(s.id).then(reload).catch(fail)}>حذف (أُرسلت بالخطأ)</button>
          </p>
        </div>
      ))}
      {d.targets.length === 0 ? <p className="muted small">لا يوجد موظف بحساب نشط في فريق هذه المكالمة.</p> : (
        <div className="form">
          <label>إلى
            <select value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">اختر…</option>
              {d.targets.map((t) => <option key={t.id} value={t.id}>{t.username}{t.ext ? ` · ${t.ext}` : ""}</option>)}
            </select>
          </label>
          <label>الرسالة <textarea value={note} maxLength={1000} rows={2} onChange={(e) => setNote(e.target.value)} /></label>
          <button type="button" onClick={send}>بدء محادثة</button>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </section>
  );
}

// ---------- delete content (admin) ----------

export function DeletePanel({ callId, onDone, onExpired }: { callId: number; onDone: () => void; onExpired: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const fail = useFail(onExpired, setError);
  if (!open) return <button className="link danger" onClick={() => setOpen(true)}>حذف التسجيل والنصوص لهذه المكالمة…</button>;
  const go = async () => {
    setError("");
    try { await api.deleteContent(callId, reason.trim(), Number(confirm)); onDone(); } catch (e) { fail(e); }
  };
  return (
    <section className="panel work danger">
      <h3>حذف محتوى المكالمة</h3>
      <p className="small">يُحذف التسجيل والتفريغ والتحليل والتقييم ونموذج المتابعة والمشاركات نهائيًا. تبقى بيانات المكالمة (الوقت، المدة) للمؤشرات، ويُسجَّل من حذف ومتى ولماذا.</p>
      <label>السبب <input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} /></label>
      <label>اكتب رقم المكالمة <b dir="ltr">{callId}</b> للتأكيد <input dir="ltr" value={confirm} onChange={(e) => setConfirm(e.target.value.trim())} /></label>
      <div className="row">
        <button type="button" className="danger" disabled={reason.trim().length < 5 || confirm !== String(callId)} onClick={go}>حذف نهائي</button>
        <button type="button" onClick={() => setOpen(false)}>إلغاء</button>
      </div>
      {error && <div className="error">{error}</div>}
    </section>
  );
}
