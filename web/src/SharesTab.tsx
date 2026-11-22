// "ملاحظات المشرف" tab of the follow-ups page (spec 2026-11-22-shares-tab): calls a supervisor shared with an agent.
// Agent: open the note, reply within 24 h of it being sent (the KPI counts it), then close it -> their archive.
// Staff: every shared call + each reply; they close it into their own archive. Archive: من/إلى + agent filters.
import { useCallback, useEffect, useState } from "react";
import { api, Unauthorized, type Agent, type Share, type ShareList, type ShareReplyState } from "./api";
import { formatDateTime, riyadhToday } from "./format";

const STATE: Record<ShareReplyState, [string, string]> = {
  pending: ["لم يتم الرد بعد", "warn"], on_time: ["تم الرد في الوقت", "ok"], late: ["تم الرد متأخراً", "bad"],
  missing: ["لم يتم الرد خلال 24 ساعة", "bad"],
};

function monthAgo(): string {
  const [y, m, d] = riyadhToday().split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - 30)).toISOString().slice(0, 10);
}

function left(deadline: string, now: number): string {
  const ms = new Date(deadline).getTime() - now;
  if (ms <= 0) return "انتهت المهلة";
  const h = Math.floor(ms / 3_600_000), m = Math.floor((ms % 3_600_000) / 60_000);
  return h > 0 ? `باقي ${h} س ${m} د` : `باقي ${m} د`;
}

interface Props {
  staff: boolean; agents: Agent[]; tick: number;
  onOpenCall: (callId: number) => void; onExpired: () => void; onCount: (n: number) => void;
}

export function SharesTab({ staff, agents, tick, onOpenCall, onExpired, onCount }: Props) {
  const [box, setBox] = useState<"open" | "archive">("open");
  const [agent, setAgent] = useState("");          // staff: "" = all agents
  const [from, setFrom] = useState(monthAgo);
  const [to, setTo] = useState(riyadhToday);
  const [d, setD] = useState<ShareList | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setError(e instanceof Error && e.message ? e.message : "تعذر التحميل. أعد المحاولة.");
  }, [onExpired]);

  const load = useCallback(() => {
    let alive = true;     // filters apply at once: an older, slower answer must not win
    api.shares({ box, agent: staff ? agent : "", from, to })
      .then((r) => { if (alive) { setD(r); setError(""); if (!staff || !agent) onCount(r.open); } }).catch((e) => alive && fail(e));
    return () => { alive = false; };
  }, [box, agent, from, to, staff, fail, onCount]);
  useEffect(() => load(), [load, tick]);

  const open = (s: Share) => {   // agent: one click opens the call AND shows the note (that click = "opened")
    if (!staff && !s.seen_at) api.shareSeen(s.id).catch(fail).finally(() => { load(); onOpenCall(s.call_id); });
    else onOpenCall(s.call_id);
  };
  const close = async (s: Share) => {
    if (busy !== null) return;
    setBusy(s.id);
    try { await api.closeShare(s.id); load(); } catch (e) { fail(e); } finally { setBusy(null); }
  };

  const now = Date.now();
  const items = d?.items ?? [];
  return (
    <section className="panel">
      <div className="chips" role="tablist">
        {([["open", `مفتوحة${d && box === "open" ? ` (${d.open})` : ""}`], ["archive", "أرشيف الملاحظات"]] as const).map(([k, t]) => (
          <button key={k} type="button" role="tab" aria-selected={box === k} className={`chip${box === k ? " on" : ""}`}
            onClick={() => setBox(k)}>{t}</button>
        ))}
      </div>
      {(staff || box === "archive") && (
        <form className="filters" onSubmit={(e) => e.preventDefault()}>
          {box === "archive" && <>
            <label>من <input type="date" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>
            <label>إلى <input type="date" value={to} min={from} max={riyadhToday()} onChange={(e) => e.target.value && setTo(e.target.value)} /></label>
          </>}
          {staff && (
            <label>الموظف
              <select value={agent} onChange={(e) => setAgent(e.target.value)}>
                <option value="">كل الموظفين</option>
                {agents.map((a) => <option key={a.ext} value={a.ext}>{a.ext} · {a.name}</option>)}
              </select>
            </label>
          )}
        </form>
      )}
      {error && <div className="error">{error}</div>}
      <p className="muted small">
        {staff
          ? "للموظف 24 ساعة من وقت الإرسال ليرد، وإلا يُحتسب عليه في مؤشرات الأداء. «نقل للأرشيف» ينقلها من قائمتك فقط."
          : "اضغط المكالمة لعرض الملاحظة والمكالمة، واكتب ردّك خلال 24 ساعة من الإرسال (يُحتسب في تقييمك). بعد الرد اضغط «إغلاق ونقل للأرشيف»."}
        {box === "archive" && " الأرشيف حسب يوم إرسال الملاحظة."}
      </p>
      <div className="table-wrap"><table>
        <thead><tr>
          <th>المكالمة</th><th>الملاحظة</th><th>من ← إلى</th><th>أُرسلت</th>
          <th>{staff ? "فتحها الموظف" : "فتحتها"}</th><th>الرد</th><th>المهلة</th>{box === "open" && <th></th>}
        </tr></thead>
        <tbody>
          {items.map((s) => {
            const [label0, tone] = STATE[s.reply_state];
            const label = !staff && s.reply_state === "pending" ? "لم ترد بعد" : label0;
            return (
              <tr key={s.id} className={s.reply_state === "missing" ? "row-bad" : ""} onClick={() => open(s)}>
                <td className="nowrap">{s.call_start ? formatDateTime(s.call_start) : "–"}</td>
                <td>{s.note ?? <span className="state warn">ملاحظة جديدة: اضغط لعرضها</span>}</td>
                <td>{s.from} ← {s.to}</td>
                <td className="nowrap">{formatDateTime(s.created_at)}</td>
                <td>{s.seen_at ? formatDateTime(s.seen_at) : <span className="state off">{staff ? "لم يفتحها" : "لم تفتحها"}</span>}</td>
                <td>{s.reply ? <>{s.reply} <span className="muted small">· {formatDateTime(s.reply_at!)}</span></> : "–"}</td>
                <td className="nowrap"><span className={`state ${tone}`}>{label}</span>
                  {s.reply_state === "pending" && <div className="muted small">{left(s.deadline, now)}</div>}</td>
                {box === "open" && (
                  <td onClick={(e) => e.stopPropagation()}>
                    {staff || s.reply
                      ? <button type="button" className="small" disabled={busy === s.id} onClick={() => close(s)}>
                          {staff ? "نقل للأرشيف" : "إغلاق ونقل للأرشيف"}</button>
                      : <span className="muted small">اكتب ردّك أولاً</span>}
                  </td>
                )}
              </tr>
            );
          })}
          {d && items.length === 0 && (
            <tr><td colSpan={box === "open" ? 8 : 7} className="center muted">
              {box === "open" ? "لا توجد ملاحظات مفتوحة" : "لا يوجد شيء في الأرشيف لهذه الفترة"}</td></tr>
          )}
        </tbody>
      </table></div>
      {d?.truncated && <p className="muted small">تظهر أول 500 فقط: ضيّق الفترة.</p>}
    </section>
  );
}
