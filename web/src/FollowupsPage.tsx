// "المتابعات": agents see their own forms to fill, callbacks and notes; staff see everyone's (agent-supervisor spec).
import { useCallback, useEffect, useState } from "react";
import { api, Unauthorized, type Agent, type FollowupList, type Me, type Share } from "./api";
import { CALLBACK_LABEL, FORM_LABEL, formatDateTime, riyadhToday } from "./format";
import { CB_CLASS, FORM_CLASS } from "./WorkPanels";

interface Props { me: Me; onCalls?: () => void;   // agents: no calls page (follow-ups is their only page)
  onOpenCall: (id: number) => void; onLogout: () => void; onExpired: () => void }

const STATE_OPTIONS: [string, string][] = [
  ["todo", "نماذج لم تُعبّأ"], ["open_callbacks", "معاودات مفتوحة أو فائتة"], ["callbacks", "كل المعاودات"],
  ["came_back", "عاد العميل بعد الحل"], ["", "الكل"],
];

function weekAgo(): string {
  const [y, m, d] = riyadhToday().split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - 6)).toISOString().slice(0, 10);
}

export function FollowupsPage({ me, onCalls, onOpenCall, onLogout, onExpired }: Props) {
  const staff = me.role === "admin" || me.role === "supervisor";
  const [from, setFrom] = useState(weekAgo);
  const [to, setTo] = useState(riyadhToday);
  const [agent, setAgent] = useState("");
  const [state, setState] = useState("todo");
  const [data, setData] = useState<FollowupList | null>(null);
  const [shares, setShares] = useState<Share[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState("");

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setError("تعذر تحميل المتابعات. أعد المحاولة.");
  }, [onExpired]);

  useEffect(() => { if (staff) api.agents().then(setAgents).catch(fail); }, [staff, fail]);
  useEffect(() => {
    let alive = true;                 // filters apply at once: an older, slower answer must not win
    setError("");
    api.followups({ from, to, agent, state }).then((d) => alive && setData(d)).catch((e) => alive && fail(e));
    return () => { alive = false; };
  }, [from, to, agent, state, fail]);
  const loadShares = useCallback(() => api.shares().then((d) => setShares(d.items)).catch(fail), [fail]);
  useEffect(() => { loadShares(); }, [loadShares]);

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
                  <td>{s.seen_at ? formatDateTime(s.seen_at) : <span className="state off">لم تُشاهد</span>}
                    {!staff && !s.seen_at && (
                      <button className="link" onClick={(e) => { e.stopPropagation(); api.shareSeen(s.id).then(loadShares).catch(fail); }}>تمت المشاهدة</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </section>

      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>من <input type="date" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>
        <label>إلى <input type="date" value={to} min={from} onChange={(e) => e.target.value && setTo(e.target.value)} /></label>
        {staff && (
          <label>الموظف
            <select value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">الكل</option>
              {agents.map((a) => <option key={a.ext} value={a.ext}>{a.ext} · {a.name}</option>)}
            </select>
          </label>
        )}
        <label>العرض
          <select value={state} onChange={(e) => setState(e.target.value)}>
            {STATE_OPTIONS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </form>

      {data && !data.settings.followup_start ? (
        <p className="muted panel">نماذج المتابعة غير مفعّلة بعد. يفعّلها مدير النظام من صفحة المستخدمين (تاريخ البدء).</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>الوقت</th><th>رقم العميل</th><th>الموظف</th><th>النموذج</th><th>معاودة الاتصال</th><th>رد العميل؟</th><th>آخر موعد للنموذج</th></tr></thead>
            <tbody>
              {data?.items.map((i) => (
                <tr key={i.call_id} onClick={() => onOpenCall(i.call_id)}>
                  <td className="nowrap">{formatDateTime(i.start_time)}</td>
                  <td dir="ltr" className="num">{i.customer_number ?? "–"}</td>
                  <td>{i.agent_ext} <span className="muted">{i.agent_name}</span></td>
                  <td>
                    <span className={`state ${FORM_CLASS[i.form_state]}`}>{FORM_LABEL[i.form_state]}</span>
                    {i.form?.no_followup && <span className="muted small"> · لا تحتاج متابعة</span>}
                    {i.form?.solved === true && <span className="muted small"> · حُلّت</span>}
                    {i.came_back && <span className="tag">عاد العميل</span>}
                  </td>
                  <td>{i.callback ? <>
                    {formatDateTime(i.callback.at)} <span className={`state ${CB_CLASS[i.callback.status]}`}>{CALLBACK_LABEL[i.callback.status]}</span>
                    {i.callback.by_ext && <span className="muted small"> · {i.callback.by_ext}</span>}
                  </> : "–"}</td>
                  <td>{i.callback?.answered == null ? <span className="muted">–</span>
                    : i.callback.answered ? <span className="state ok">ردّ</span>
                    : <span className="state warn">لم يرد</span>}</td>
                  <td className="nowrap muted small">{formatDateTime(i.deadline)}</td>
                </tr>
              ))}
              {data && data.items.length === 0 && <tr><td colSpan={7} className="center muted">لا يوجد شيء لهذا الاختيار</td></tr>}
            </tbody>
          </table>
          {data?.truncated && <p className="muted small">تظهر أول 500 فقط: ضيّق الفترة.</p>}
        </div>
      )}
    </div>
  );
}
