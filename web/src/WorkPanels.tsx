// Call-panel boxes of the agent-supervisor spec: follow-up form, agent summary, share with a note, delete content.
import { useEffect, useState } from "react";
import {
  api, Refused, Unauthorized,
  type AgentSummary, type CallShares, type FollowupIn, type FollowupView, type Me,
} from "./api";
import { CALLBACK_LABEL, FORM_LABEL, formatDateTime, fromRiyadhInput, toRiyadhInput } from "./format";

export const CB_CLASS: Record<string, string> = { on_time: "ok", waiting: "off", late: "warn", missed: "bad", no_number: "off" };
export const FORM_CLASS: Record<string, string> = { on_time: "ok", missing: "off", late: "warn", overdue: "bad" };

function useFail(onExpired: () => void, setError: (m: string) => void) {
  return (e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else if (e instanceof Refused) setError(e.message);
    else setError("تعذر الاتصال بالخادم. أعد المحاولة.");
  };
}

// ---------- follow-up form ----------

export function FollowupPanel({ callId, onExpired }: { callId: number; onExpired: () => void }) {
  const [view, setView] = useState<FollowupView | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [what, setWhat] = useState("");
  const [solved, setSolved] = useState<"" | "yes" | "no">("");
  const [resp, setResp] = useState("");            // "user:12" | "customer" | "other_department"
  const [expected, setExpected] = useState("");
  const [callback, setCallback] = useState("");
  const fail = useFail(onExpired, setError);

  const load = (v: FollowupView) => {
    setView(v);
    if (!v.needed || !v.form) return;
    const f = v.form;
    setWhat(f.what_happened ?? "");
    setSolved(f.no_followup || f.solved === null ? "" : f.solved ? "yes" : "no");
    setResp(f.responsible_kind === "user" ? `user:${f.responsible_user_id}` : f.responsible_kind ?? "");
    setExpected(toRiyadhInput(f.expected_at));
    setCallback(toRiyadhInput(f.callback_at));
  };
  useEffect(() => { api.followup(callId).then(load).catch(fail); /* eslint-disable-next-line */ }, [callId]);

  if (!view || !view.needed) return null;
  const save = async (body: FollowupIn) => {
    setError(""); setSaved(false);
    try { load(await api.saveFollowup(callId, body)); setSaved(true); } catch (e) { fail(e); }
  };
  const submit = () => {
    if (!what.trim()) { setError("اكتب ما حدث في المكالمة"); return; }
    if (!solved) { setError("اختر: هل حُلّت المشكلة؟"); return; }
    if (solved === "yes") { save({ no_followup: false, what_happened: what.trim(), solved: true }); return; }
    if (!resp || !expected || !callback) { setError("حدد المسؤول ووقت الحل المتوقع ووقت معاودة الاتصال"); return; }
    const [kind, uid] = resp.split(":");
    save({ no_followup: false, what_happened: what.trim(), solved: false, responsible_kind: kind,
      responsible_user_id: uid ? Number(uid) : null, expected_at: fromRiyadhInput(expected),
      callback_at: fromRiyadhInput(callback) });
  };
  const ro = !view.can_edit;
  return (
    <section className="panel work">
      <h3>متابعة المكالمة <span className={`state ${FORM_CLASS[view.form_state]}`}>{FORM_LABEL[view.form_state]}</span></h3>
      <p className="muted small">آخر موعد للتعبئة: {formatDateTime(view.deadline)}</p>
      {view.form?.no_followup && <p>لا تحتاج متابعة.</p>}
      {view.callback && (
        <p>معاودة الاتصال: {formatDateTime(view.callback.at)}{" "}
          <span className={`state ${CB_CLASS[view.callback.status]}`}>{CALLBACK_LABEL[view.callback.status]}</span>
          {view.callback.by_ext && <span className="muted small"> · اتصل {view.callback.by_ext}</span>}
        </p>
      )}
      {view.came_back && <p className="tag">اتصل العميل مرة أخرى خلال 72 ساعة بعد الحل</p>}
      {ro && view.lock_reason && <p className="muted small">لا يمكنك التعديل: {lockText(view.lock_reason)}</p>}
      <fieldset disabled={ro} className="form">
        {!view.form && <button type="button" onClick={() => save({ no_followup: true })}>لا تحتاج متابعة (رقم خطأ، استفسار انتهى…)</button>}
        <label>ماذا حدث؟
          <textarea value={what} maxLength={500} rows={3} onChange={(e) => setWhat(e.target.value)} />
        </label>
        <div className="row">
          هل حُلّت المشكلة؟
          <label><input type="radio" checked={solved === "yes"} onChange={() => setSolved("yes")} /> نعم</label>
          <label><input type="radio" checked={solved === "no"} onChange={() => setSolved("no")} /> لا</label>
        </div>
        {solved === "no" && (
          <>
            <label>من يجب أن يحلّها؟
              <select value={resp} onChange={(e) => setResp(e.target.value)}>
                <option value="">اختر…</option>
                {view.options.map((o) => <option key={o.id} value={`user:${o.id}`}>{o.username}{o.ext ? ` · ${o.ext}` : ""}</option>)}
                <option value="customer">العميل</option>
                <option value="other_department">قسم آخر</option>
              </select>
            </label>
            <label>وقت الحل المتوقع <input type="datetime-local" value={expected} onChange={(e) => setExpected(e.target.value)} /></label>
            <label>وقت معاودة الاتصال بالعميل <input type="datetime-local" value={callback} onChange={(e) => setCallback(e.target.value)} /></label>
          </>
        )}
        <button type="button" className="primary" onClick={submit}>حفظ</button>
      </fieldset>
      {saved && <p className="muted small">تم الحفظ.</p>}
      {error && <div className="error">{error}</div>}
    </section>
  );
}

function lockText(reason: string): string {
  if (reason.startsWith("after the form deadline")) return "انتهى وقت التعبئة، يعدّلها المشرف فقط";
  if (reason.startsWith("the callback time")) return "مرّ وقت معاودة الاتصال، يعدّلها المشرف فقط";
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
          <p>{s.note}</p>
          <p className="muted small">
            {s.from} ← {s.to} · {formatDateTime(s.created_at)} · {s.seen_at ? `شوهدت ${formatDateTime(s.seen_at)}` : "لم تُشاهد بعد"}
            {staff && <button className="link" onClick={() => api.unshare(s.id).then(reload).catch(fail)}>سحب</button>}
            {!staff && !s.seen_at && <button className="link" onClick={() => api.shareSeen(s.id).then(reload).catch(fail)}>تمت المشاهدة</button>}
          </p>
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
