// Call-panel boxes of the agent-supervisor spec: follow-up form, agent summary, share with a note, delete content.
import { useEffect, useState } from "react";
import {
  api, Refused, Unauthorized,
  type AgentSummary, type CallShares, type FollowupIn, type FollowupView, type HistoryEntry, type Me, type NoteIn,
  type Person, type Share,
} from "./api";
import {
  CALLBACK_LABEL, STEP_CLASS, STEP_LABEL, STEP_NEXT, earlyText, formatDateTime, fromRiyadhInput, ringText, toRiyadhInput,
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
const MAX_SOLVE_MS = 2 * 24 * 3600_000;     // a problem not solved: expected solve time within 2 days (server checks too)
const nowIn = () => toRiyadhInput(new Date().toISOString());
const maxIn = () => toRiyadhInput(new Date(Date.now() + MAX_SOLVE_MS).toISOString());
interface Problem { what: string; solved: Solved; resp: string; expected: string; callback: string }
const EMPTY: Problem = { what: "", solved: "", resp: "", expected: "", callback: "" };

/** Check the fields; returns the body part or an error text. */
function problemBody(p: Problem, keepExpected = ""): { body?: Omit<FollowupIn, "no_followup">; error?: string } {
  if (!p.what.trim()) return { error: "اكتب ما حدث في المكالمة" };
  if (!p.solved) return { error: "اختر: هل حُلّت المشكلة؟" };
  if (p.solved === "yes") return { body: { what_happened: p.what.trim(), solved: true } };
  if (!p.resp || !p.expected) return { error: "حدد المسؤول ووقت الحل المتوقع (خلال يومين)" };
  const left = (v: string) => new Date(fromRiyadhInput(v)).getTime() - Date.now();
  if (p.expected !== keepExpected && left(p.expected) > MAX_SOLVE_MS) return { error: "أقصى وقت لحل المشكلة يومان" };
  if (p.callback && left(p.callback) <= 0) return { error: "وقت معاودة الاتصال يجب أن يكون في المستقبل" };
  if (p.callback && left(p.callback) > MAX_SOLVE_MS) return { error: "وقت معاودة الاتصال خلال يومين على الأكثر" };
  const [kind, uid] = p.resp.split(":");
  return { body: { what_happened: p.what.trim(), solved: false, responsible_kind: kind,
    responsible_user_id: uid ? Number(uid) : null, expected_at: fromRiyadhInput(p.expected),
    ...(p.callback ? { callback_at: fromRiyadhInput(p.callback) } : {}) } };
}

function ProblemFields({ p, set, options, label }:
  { p: Problem; set: (p: Problem) => void; options: Person[]; label: string }) {
  return (
    <>
      <label>{label}
        <textarea value={p.what} maxLength={500} rows={3} onChange={(e) => set({ ...p, what: e.target.value })} />
      </label>
      <div className="row">
        هل حُلّت المشكلة؟
        <label><input type="radio" checked={p.solved === "yes"} onChange={() => set({ ...p, solved: "yes" })} /> نعم</label>
        <label><input type="radio" checked={p.solved === "no"} onChange={() => set({ ...p, solved: "no" })} /> لا</label>
      </div>
      {p.solved === "no" && (
        <>
          <label>من يجب أن يحلّها؟
            <select value={p.resp} onChange={(e) => set({ ...p, resp: e.target.value })}>
              <option value="">اختر…</option>
              {options.map((o) => <option key={o.id} value={`user:${o.id}`}>{o.username}{o.ext ? ` · ${o.ext}` : ""}</option>)}
              <option value="customer">العميل</option>
              <option value="other_department">قسم آخر</option>
            </select>
          </label>
          <label>وقت الحل المتوقع (خلال يومين على الأكثر) <input type="datetime-local" value={p.expected} min={nowIn()} max={maxIn()} onChange={(e) => set({ ...p, expected: e.target.value })} /></label>
          <label>وقت معاودة الاتصال بالعميل (اختياري) <input type="datetime-local" value={p.callback} min={nowIn()} max={maxIn()} onChange={(e) => set({ ...p, callback: e.target.value })} /></label>
        </>
      )}
    </>
  );
}

const NOTE_STEPS: string[] = ["note_needed", "waiting_solution", "solution_overdue"];
const RESP_LABEL: Record<string, string> = { customer: "العميل", other_department: "قسم آخر" };

function History({ items }: { items: HistoryEntry[] }) {
  if (items.length === 0) return null;
  return (
    <ol className="history">
      {items.map((h) => (
        <li key={h.round}>
          <b>{h.round === 1 ? "النموذج" : `ملاحظة متابعة ${h.round - 1}`}</b>
          <span className="muted small"> · {formatDateTime(h.at)}{h.by ? ` · ${h.by}` : ""}</span>
          {h.no_followup ? <p>لا تحتاج متابعة.</p> : <>
            <p>{h.what_happened}</p>
            <p className="small">
              {h.solved ? <span className="state ok">حُلّت</span> : <span className="state warn">لم تُحل</span>}
              {h.solved === false && <> · المسؤول: {h.responsible ?? RESP_LABEL[h.responsible_kind ?? ""] ?? "–"}</>}
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

export function FollowupPanel({ callId, me, onExpired }: { callId: number; me: Me; onExpired: () => void }) {
  // supervisors/admins only watch: the agent fills the form and the notes (no form, no note box, no "your task")
  const staff = me.role === "admin" || me.role === "supervisor";
  const [view, setView] = useState<FollowupView | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);          // no double save (double click)
  const [form, setForm] = useState<Problem>(EMPTY);
  const [note, setNote] = useState<Problem>(EMPTY);
  const fail = useFail(onExpired, setError);

  const load = (v: FollowupView) => {
    setView(v);
    setNote(EMPTY);
    if (!v.needed || !v.form) return;
    const f = v.form;
    setForm({ what: f.what_happened ?? "", solved: f.no_followup || f.solved === null ? "" : f.solved ? "yes" : "no",
      resp: f.responsible_kind === "user" ? `user:${f.responsible_user_id}` : f.responsible_kind ?? "",
      expected: toRiyadhInput(f.expected_at), callback: toRiyadhInput(f.callback_at) });
  };
  useEffect(() => { api.followup(callId).then(load).catch(fail); /* eslint-disable-next-line */ }, [callId]);

  if (!view || !view.needed) return null;
  const run = async (call: () => Promise<FollowupView>, done: string) => {
    if (busy) return;
    setError(""); setSaved(""); setBusy(true);
    try { load(await call()); setSaved(done); } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const saveForm = (body: FollowupIn) => run(() => api.saveFollowup(callId, body), "تم حفظ النموذج.");
  const submitForm = () => {
    const r = problemBody(form, toRiyadhInput(view.form?.expected_at));   // an unchanged old time stays valid
    if (r.error) { setError(r.error); return; }
    saveForm({ no_followup: false, ...r.body });
  };
  const submitNote = () => {
    const r = problemBody(note);
    if (r.error) { setError(r.error); return; }
    run(() => api.addNote(callId, { ...(r.body as Omit<NoteIn, "round">), round: view.round + 1 }), "تم حفظ الملاحظة.");
  };
  const ro = !view.can_edit || busy;
  const noteStep = !staff && NOTE_STEPS.includes(view.step);
  // round 1 without a callback: the form stays editable (deadline rules on the server) next to the update box
  const formOpen = !staff && view.round === 1 && view.history.length <= 1 && view.step !== "note_needed";
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

      {noteStep && (
        <fieldset disabled={busy} className="form note-box">
          <legend>{view.step === "note_needed"
            ? `ملاحظة بعد مكالمة المعاودة${view.callback?.by_ext ? ` (اتصل ${view.callback.by_ext}${view.callback.answered ? "، ردّ العميل" : "، لم يرد"})` : ""}`
            : "تحديث المتابعة"}</legend>
          <ProblemFields p={note} set={setNote} options={view.options}
            label={view.step === "note_needed" ? "ماذا حدث في مكالمة المعاودة؟" : "ماذا حدث في المشكلة؟"} />
          <button type="button" className="primary" onClick={submitNote}>حفظ الملاحظة</button>
        </fieldset>
      )}

      <History items={view.history} />

      {formOpen && <>
        {ro && view.lock_reason && <p className="muted small">لا يمكنك التعديل: {lockText(view.lock_reason)}</p>}
        <fieldset disabled={ro} className="form">
          {view.form && <legend>تعديل النموذج</legend>}
          {!view.form && <button type="button" onClick={() => saveForm({ no_followup: true })}>لا تحتاج متابعة (رقم خطأ، استفسار انتهى…)</button>}
          <ProblemFields p={form} set={setForm} options={view.options} label="ماذا حدث؟" />
          <button type="button" className="primary" onClick={submitForm}>حفظ</button>
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
  const [d, setD] = useState<CallShares | null>(null);
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const fail = useFail(onExpired, setError);
  const staff = me.role === "admin" || me.role === "supervisor";
  const reload = () => api.callShares(callId).then(setD).catch(fail);
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [callId]);
  if (!d || (!staff && d.items.length === 0)) return null;
  const send = async () => {
    setError("");
    if (!to || !note.trim()) { setError("اختر الموظف واكتب الملاحظة"); return; }
    try { await api.share(callId, Number(to), note.trim()); setNote(""); setTo(""); reload(); } catch (e) { fail(e); }
  };
  return (
    <section className="panel work">
      <h3>{staff ? "مشاركة المكالمة مع موظف" : "ملاحظة المشرف"}</h3>
      {d.items.map((s) => (
        <div key={s.id} className="share">
          {!staff && !s.seen_at ? (
            /* the note stays hidden until the agent clicks it; the click is what the supervisor sees as "opened" */
            <button type="button" className="primary" onClick={() => api.shareSeen(s.id).then(reload).catch(fail)}>
              ملاحظة جديدة من {s.from}: اضغط لعرضها</button>
          ) : <p>{s.note}</p>}
          <p className="muted small">
            {s.from} ← {s.to} · {formatDateTime(s.created_at)} · {s.seen_at ? `فتحها الموظف ${formatDateTime(s.seen_at)}` : "لم يفتحها الموظف بعد"}
            {staff && <button className="link" onClick={() => api.unshare(s.id).then(reload).catch(fail)}>سحب</button>}
          </p>
          {staff && s.reply && <p className="reply"><b>ردّ {s.to}:</b> {s.reply} <span className="muted small">· {formatDateTime(s.reply_at!)}</span></p>}
          {!staff && s.seen_at && <ReplyBox share={s} onSaved={reload} onFail={fail} />}
        </div>
      ))}
      {staff && (
        d.targets.length === 0 ? <p className="muted small">لا يوجد موظف بحساب نشط في فريق هذه المكالمة.</p> : (
          <div className="form">
            <label>إلى
              <select value={to} onChange={(e) => setTo(e.target.value)}>
                <option value="">اختر…</option>
                {d.targets.map((t) => <option key={t.id} value={t.id}>{t.username}{t.ext ? ` · ${t.ext}` : ""}</option>)}
              </select>
            </label>
            <label>الملاحظة <textarea value={note} maxLength={1000} rows={2} onChange={(e) => setNote(e.target.value)} /></label>
            <button type="button" onClick={send}>إرسال</button>
          </div>
        )
      )}
      {error && <div className="error">{error}</div>}
    </section>
  );
}

function ReplyBox({ share, onSaved, onFail }: { share: Share; onSaved: () => void; onFail: (e: unknown) => void }) {
  const [text, setText] = useState(share.reply ?? "");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (busy) return;
    setBusy(true);
    try { await api.shareReply(share.id, text.trim()); onSaved(); } catch (e) { onFail(e); } finally { setBusy(false); }
  };
  return (
    <div className="form">
      {share.reply && <p className="muted small">ردّك ({formatDateTime(share.reply_at!)}): {share.reply}</p>}
      <label>{share.reply ? "تعديل ردّك على المشرف" : "ردّك على المشرف"}
        <textarea value={text} maxLength={1000} rows={2} onChange={(e) => setText(e.target.value)} /></label>
      <button type="button" disabled={busy || text.trim() === (share.reply ?? "")} onClick={save}>إرسال الرد</button>
    </div>
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
