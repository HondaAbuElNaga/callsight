import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, Unauthorized,
  type Agent, type CallDetail, type CallPage, type CallRow, type Filters, type Me, type Status, type Summary,
} from "./api";
import { DIRECTION_LABEL, LEG_LABEL, STATUS_LABEL, STATUS_ORDER, duration, formatDateTime, riyadhToday } from "./format";

interface Props { me: Me; onLogout: () => void; onExpired: () => void }

const today = riyadhToday();
const EMPTY: Filters = { from: today, to: today, agent: "", status: "", direction: "", q: "" };

export function CallsPage({ me, onLogout, onExpired }: Props) {
  const [draft, setDraft] = useState<Filters>(EMPTY);   // what is typed in the filter bar
  const [filters, setFilters] = useState<Filters>(EMPTY); // what is applied
  const [page, setPage] = useState(1);
  const [data, setData] = useState<CallPage | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState<CallRow | null>(null);
  const [detail, setDetail] = useState<CallDetail | null>(null);

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setError("تعذر تحميل البيانات. تأكد أن الخدمة تعمل ثم أعد المحاولة.");
  }, [onExpired]);

  useEffect(() => { api.agents().then(setAgents).catch(fail); }, [fail]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    Promise.all([api.calls(filters, page), api.summary(filters)])
      .then(([c, s]) => { if (alive) { setData(c); setSummary(s); } })
      .catch((e) => alive && fail(e))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [filters, page, fail]);

  const apply = (f: Filters) => { setFilters(f); setDraft(f); setPage(1); };
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const qValid = draft.q === "" || /^\+?\d{1,20}$/.test(draft.q);

  async function openDetail(row: CallRow) {
    try { setDetail(await api.call(row.id)); } catch (e) { fail(e); }
  }

  return (
    <div className={`page ${playing ? "with-player" : ""}`}>
      <header className="topbar">
        <h1>سجل المكالمات</h1>
        <div className="who">
          <span className="muted">{me.username}{me.ext ? ` · تحويلة ${me.ext}` : ""}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>

      <form className="filters" onSubmit={(e) => { e.preventDefault(); if (qValid) apply(draft); }}>
        <label>من <input type="date" value={draft.from} max={draft.to || undefined}
          onChange={(e) => setDraft({ ...draft, from: e.target.value })} /></label>
        <label>إلى <input type="date" value={draft.to} min={draft.from || undefined}
          onChange={(e) => setDraft({ ...draft, to: e.target.value })} /></label>
        <label>الموظف
          <select value={draft.agent} onChange={(e) => setDraft({ ...draft, agent: e.target.value })}>
            <option value="">الكل</option>
            {agents.map((a) => <option key={a.ext} value={a.ext}>{a.ext} · {a.name}</option>)}
          </select>
        </label>
        <label>الحالة
          <select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
            <option value="">الكل</option>
            {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
          </select>
        </label>
        <label>النوع
          <select value={draft.direction} onChange={(e) => setDraft({ ...draft, direction: e.target.value })}>
            <option value="">الكل</option>
            {Object.entries(DIRECTION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>رقم العميل
          <input value={draft.q} dir="ltr" inputMode="numeric" placeholder="055…" aria-invalid={!qValid}
            onChange={(e) => setDraft({ ...draft, q: e.target.value.trim() })} />
        </label>
        <div className="filter-actions">
          <button type="submit" className="primary" disabled={!qValid || !draft.from || !draft.to}>عرض</button>
          <button type="button" onClick={() => apply(EMPTY)}>اليوم</button>
        </div>
      </form>

      {summary && (
        <div className="chips">
          <button className={`chip ${filters.status === "" ? "on" : ""}`} onClick={() => apply({ ...filters, status: "" })}>
            الكل <b>{summary.total}</b>
          </button>
          {STATUS_ORDER.map((s: Status) => (
            <button key={s} className={`chip st-${s} ${filters.status === s ? "on" : ""}`}
              onClick={() => apply({ ...filters, status: filters.status === s ? "" : s })}>
              {STATUS_LABEL[s]} <b>{summary.by_status[s] ?? 0}</b>
            </button>
          ))}
        </div>
      )}

      {error && <div className="error">{error}</div>}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>الوقت</th><th>النوع</th><th>رقم العميل</th><th>الموظف</th><th>الحالة</th>
              <th>الانتظار</th><th>المكالمة</th><th>التسجيل</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((c) => (
              <tr key={c.id} className={detail?.id === c.id ? "sel" : ""} onClick={() => openDetail(c)}>
                <td className="nowrap">{formatDateTime(c.start_time)}</td>
                <td>{DIRECTION_LABEL[c.direction]}</td>
                <td dir="ltr" className="num">{c.customer_number ?? "–"}</td>
                <td>{c.agent_ext ? <>{c.agent_ext} <span className="muted">{c.agent_name}</span>{!c.answered_by_agent && <span className="tag">لم يرد</span>}</> : "–"}</td>
                <td><span className={`badge st-${c.status}`}>{STATUS_LABEL[c.status]}</span></td>
                <td className="num">{duration(c.wait_seconds)}</td>
                <td className="num">{c.talk_seconds ? duration(c.talk_seconds) : "–"}</td>
                <td>
                  {c.has_recording && (
                    <button className={`play ${playing?.id === c.id ? "on" : ""}`} aria-label="تشغيل التسجيل"
                      onClick={(e) => { e.stopPropagation(); setPlaying(c); }}>▶</button>
                  )}
                </td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr><td colSpan={8} className="center muted">لا توجد مكالمات لهذا الاختيار</td></tr>
            )}
          </tbody>
        </table>
        {loading && <div className="loading">جارٍ التحميل…</div>}
      </div>

      {data && data.total > data.page_size && (
        <div className="pager">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>السابق</button>
          <span>صفحة {page} من {pages} · {data.total} مكالمة</span>
          <button disabled={page >= pages} onClick={() => setPage(page + 1)}>التالي</button>
        </div>
      )}

      {detail && <DetailPanel call={detail} onClose={() => setDetail(null)} onPlay={() => setPlaying(detail)} />}
      {playing && <Player call={playing} onClose={() => setPlaying(null)} />}
    </div>
  );
}

function DetailPanel({ call, onClose, onPlay }: { call: CallDetail; onClose: () => void; onPlay: () => void }) {
  return (
    <aside className="detail" aria-label="تفاصيل المكالمة">
      <div className="detail-head">
        <h2>تفاصيل المكالمة</h2>
        <button className="link" onClick={onClose} aria-label="إغلاق">✕</button>
      </div>
      <dl>
        <dt>الوقت</dt><dd>{formatDateTime(call.start_time)}</dd>
        <dt>رقم العميل</dt><dd dir="ltr" className="num">{call.customer_number ?? "–"}</dd>
        <dt>الحالة</dt><dd><span className={`badge st-${call.status}`}>{STATUS_LABEL[call.status]}</span></dd>
        <dt>الموظف</dt><dd>{call.agent_ext ? `${call.agent_ext} · ${call.agent_name ?? ""}` : "–"}</dd>
        <dt>القائمة الصوتية</dt><dd className="num">{duration(call.ivr_seconds)}</dd>
        <dt>الانتظار</dt><dd className="num">{duration(call.wait_seconds)}</dd>
        <dt>المكالمة</dt><dd className="num">{duration(call.talk_seconds)}</dd>
        <dt>المدة الكلية</dt><dd className="num">{duration(call.total_seconds)}</dd>
      </dl>
      {call.has_recording && <button className="primary" onClick={onPlay}>▶ تشغيل التسجيل</button>}
      <h3>مسار المكالمة</h3>
      <ol className="legs">
        {call.legs.map((l) => (
          <li key={l.id}>
            <b>{LEG_LABEL[l.kind]}</b> <span dir="ltr" className="num">{l.to_number}</span>
            <div className="muted small">
              {l.disposition} · {duration(l.duration)}
              {l.ring_duration ? ` · رنين ${duration(l.ring_duration)}` : ""}
              {l.talk_duration ? ` · كلام ${duration(l.talk_duration)}` : ""}
            </div>
          </li>
        ))}
      </ol>
    </aside>
  );
}

function Player({ call, onClose }: { call: CallRow; onClose: () => void }) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => { ref.current?.play().catch(() => undefined); }, [call.id]);
  return (
    <div className="player">
      <div className="player-info">
        <b>{formatDateTime(call.start_time)}</b>
        <span className="muted"> · <span dir="ltr">{call.customer_number}</span> · {call.agent_ext ?? ""} {call.agent_name ?? ""}</span>
      </div>
      <audio ref={ref} key={call.id} controls preload="metadata" src={api.recordingUrl(call.id)} />
      <button className="link" onClick={onClose} aria-label="إغلاق المشغل">✕</button>
    </div>
  );
}
