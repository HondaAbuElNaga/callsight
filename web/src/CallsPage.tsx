import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  api, BadRequest, Unauthorized,
  type Agent, type CallDetail, type CallPage, type CallRow, type Filters, type Me, type Status, type Summary,
} from "./api";
import { DIRECTION_LABEL, STATUS_LABEL, STATUS_ORDER, duration, formatDateTime, riyadhToday } from "./format";
import type { HangupBy } from "./api";

const HANGUP_LABEL: Record<HangupBy, string> = { agent: "الموظف", customer: "العميل", busy: "مشغول" };
import { TranscriptPanel } from "./TranscriptPanel";
import { AnalysisPanel } from "./AnalysisPanel";
import { AgentSummaryPanel, DeletePanel, FollowupPanel, SharePanel } from "./WorkPanels";

interface Props {
  me: Me; onLogout: () => void; onExpired: () => void; onGoldset?: () => void; onKpi?: () => void; onHealth?: () => void;
  onRating?: () => void;
  onLive?: () => void;
  onForwards?: () => void;      // the forwards page (staff)
  onFollowups?: () => void;
  onUsers?: () => void;
  onRules?: () => void;         // the rules page (admin only)
  onFirstSpeaker?: () => void;  // first-speaker check of the speaker split (admin only)
  openCallId?: number | null;      // open this call's panel at once (from the follow-ups page)
  onOpened?: () => void;           // called once openCallId was used (so it never reopens later)
  detailOnly?: boolean;            // agents: no call list, only the opened call (their home is the follow-ups page)
  embedded?: boolean;              // inside the follow-ups page: no page header, only the side panel (+ player)
  fromShare?: boolean;             // opened from the supervisor's shared calls: the note first, no follow-up form
}

// A function, not a constant: the page may stay open past midnight.
const emptyFilters = (): Filters => {
  const today = riyadhToday();
  return { from: today, to: today, agent: "", status: "", direction: "", q: "", words: "" };
};

// Same rules as the API (it re-checks after Arabic normalisation): 1-5 words, each 2+ characters, letters/digits.
function wordsOk(text: string): boolean {
  const t = text.trim();
  if (!t) return true;
  if (text.length > 100 || !/^[\p{L}\p{N}\p{M}\s]+$/u.test(t)) return false;
  const w = t.split(/\s+/);
  return w.length <= 5 && w.every((x) => x.length >= 2);
}

interface Seek { id: number; t: number; n: number }

export function CallsPage({ me, onLogout, onExpired, onGoldset, onKpi, onHealth, onRating, onLive, onFollowups, onUsers,
  onForwards, onRules, onFirstSpeaker,
  openCallId, onOpened, detailOnly = false, embedded = false, fromShare = false }: Props) {
  const [filters, setFilters] = useState<Filters>(emptyFilters); // applied as soon as a field changes
  const [qText, setQText] = useState("");                  // number box: applied after typing stops
  const [wordsText, setWordsText] = useState("");          // transcript words: same, never in the page URL
  const [seek, setSeek] = useState<Seek | null>(null);
  const [pos, setPos] = useState<{ id: number; t: number } | null>(null);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<CallPage | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState<CallRow | null>(null);
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const lastClicked = useRef<number | null>(null);
  const appliedWords = useRef("");   // read by fail(): the words message only when a words search is applied
  appliedWords.current = filters.words ?? "";

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else if (e instanceof BadRequest && appliedWords.current) setError("كلمات البحث غير صالحة: من 1 إلى 5 كلمات، كل كلمة حرفان على الأقل، حروف وأرقام فقط.");
    else setError("تعذر تحميل البيانات. تأكد أن الخدمة تعمل ثم أعد المحاولة.");
  }, [onExpired]);

  useEffect(() => { if (!detailOnly) api.agents().then(setAgents).catch(fail); }, [fail, detailOnly]);

  useEffect(() => {   // detailOnly (agents): no list, only the opened call
    if (detailOnly) return;
    let alive = true;
    setLoading(true);
    setError("");
    api.calls(filters, page)
      .then((c) => alive && setData(c))
      .catch((e) => alive && fail(e))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [filters, page, fail, detailOnly]);

  // The boxes on top ignore the chosen status, so every category keeps its number.
  const { status: _ignored, ...summaryKey } = filters;
  const summaryDeps = JSON.stringify(summaryKey);
  useEffect(() => {
    if (detailOnly) return;
    let alive = true;
    api.summary({ ...filters, status: "" }).then((s) => alive && setSummary(s)).catch((e) => alive && fail(e));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summaryDeps, fail, detailOnly]);

  const apply = (f: Filters) => { setFilters(f); setPage(1); };
  const set = (patch: Partial<Filters>) => apply({ ...filters, ...patch });
  const qValid = qText === "" || /^\+?\d{1,20}$/.test(qText);

  useEffect(() => {   // search by number 0.4 s after typing stops
    if (!qValid || qText === filters.q) return;
    // functional update: keeps any filter chosen while the timer was waiting
    const t = setTimeout(() => { setFilters((f) => ({ ...f, q: qText })); setPage(1); }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qText]);

  const canSearchText = me.role === "admin" || me.role === "supervisor";
  const wordsValid = wordsOk(wordsText);
  useEffect(() => {   // transcript words 0.4 s after typing stops
    const w = wordsText.trim().replace(/\s+/g, " ");
    if (!wordsValid) {   // never keep showing results for words that are no longer in the box
      if (filters.words) { setFilters((f) => ({ ...f, words: "" })); setPage(1); }
      return;
    }
    if (w === filters.words) return;
    const t = setTimeout(() => { setFilters((f) => ({ ...f, words: w })); setPage(1); }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wordsText]);

  const seekTo = (call: CallDetail, t: number) => {
    if (playing?.id !== call.id) setPlaying(call);
    setSeek({ id: call.id, t, n: Date.now() });
  };
  const onTime = useCallback((id: number, t: number) => setPos({ id, t }), []);

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  useEffect(() => {   // opened from the follow-ups page (a shared call is not in the agent's list)
    if (!openCallId) return;
    onOpened?.();
    lastClicked.current = openCallId;
    api.call(openCallId).then((d) => lastClicked.current === openCallId && setDetail(d)).catch(fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openCallId, fail]);

  async function openDetail(row: CallRow) {
    lastClicked.current = row.id;
    try {
      const d = await api.call(row.id);
      if (lastClicked.current === row.id) setDetail(d);   // ignore answers for an older click
    } catch (e) { fail(e); }
  }

  return (
    <div className={embedded ? "" : `page ${playing ? "with-player" : ""}`}>
      {!embedded && <header className="topbar">
        <h1>{detailOnly ? "تفاصيل المكالمة" : "سجل المكالمات"}</h1>
        <div className="who">
          {onLive && <button className="link" onClick={onLive}>المكالمات الحالية</button>}
          {onFollowups && <button className="link" onClick={onFollowups}>المتابعات</button>}
          {onKpi && <button className="link" onClick={onKpi}>المؤشرات</button>}
          {onForwards && <button className="link" onClick={onForwards}>التحويلات</button>}
          {onRating && <button className="link" onClick={onRating}>تقييم المكالمات</button>}
          {onGoldset && <button className="link" onClick={onGoldset}>النصوص المرجعية</button>}
          {onFirstSpeaker && <button className="link" onClick={onFirstSpeaker}>من يتكلم أولاً</button>}
          {onRules && <button className="link" onClick={onRules}>القواعد</button>}
          {onHealth && <button className="link" onClick={onHealth}>حالة النظام</button>}
          {onUsers && <button className="link" onClick={onUsers}>المستخدمون</button>}
          <span className="muted">{me.username}{me.ext ? ` · تحويلة ${me.ext}` : ""}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>}

      {!detailOnly && <>
      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>من <input type="date" value={filters.from} max={filters.to || undefined}
          onChange={(e) => e.target.value && set({ from: e.target.value, to: e.target.value > filters.to ? e.target.value : filters.to })} /></label>
        <label>إلى <input type="date" value={filters.to} min={filters.from || undefined}
          onChange={(e) => e.target.value && set({ to: e.target.value, from: e.target.value < filters.from ? e.target.value : filters.from })} /></label>
        {canSearchText && <label>الموظف
          <select value={filters.agent} onChange={(e) => set({ agent: e.target.value })}>
            <option value="">الكل</option>
            {agents.map((a) => <option key={a.ext} value={a.ext}>{a.ext} · {a.name}</option>)}
          </select>
        </label>}
        <label>الحالة
          <select value={filters.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="">الكل</option>
            {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
          </select>
        </label>
        <label>النوع
          <select value={filters.direction} onChange={(e) => set({ direction: e.target.value })}>
            <option value="">الكل</option>
            {Object.entries(DIRECTION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>رقم العميل
          <input value={qText} dir="ltr" inputMode="numeric" placeholder="055…" aria-invalid={!qValid}
            onChange={(e) => setQText(e.target.value.trim())} />
        </label>
        {canSearchText && (
          <label>كلمات في المكالمة
            <input value={wordsText} placeholder="مثال: الفاتورة" aria-invalid={!wordsValid} maxLength={100}
              title="من 1 إلى 5 كلمات، كل كلمة حرفان على الأقل" onChange={(e) => setWordsText(e.target.value)} />
          </label>
        )}
        <div className="filter-actions">
          <button type="button" onClick={() => { setQText(""); setWordsText(""); apply(emptyFilters()); }}>مسح الفلاتر</button>
        </div>
      </form>

      {summary && (
        <div className="chips">
          <button className={`chip ${filters.status === "" ? "on" : ""}`} onClick={() => apply({ ...filters, status: "" })}>
            الكل <b>{summary.total}</b>
          </button>
          {STATUS_ORDER.map((s: Status) => (
            <button key={s} className={`chip st-${s} ${filters.status === s ? "on" : ""}`}
              onClick={() => set({ status: filters.status === s ? "" : s })}>
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
                <td className="nowrap">
                  {formatDateTime(c.start_time)}
                  {c.transcript_match && <span className="tag match">مطابقة في التفريغ</span>}
                </td>
                <td>{DIRECTION_LABEL[c.direction]}</td>
                <td dir="ltr" className="num">{c.customer_number ?? "–"}</td>
                <td>{c.agent_ext ? <>{c.agent_ext} <span className="muted">{c.agent_name}</span>{!c.answered_by_agent && <span className="tag">لم يرد</span>}</> : "–"}</td>
                <td><span className={`badge st-${c.status}`}>{STATUS_LABEL[c.status]}</span></td>
                <td className="num">{duration(c.wait_seconds)}</td>
                <td className="num">{c.talk_seconds ? duration(c.talk_seconds) : "–"}</td>
                <td>
                  {c.has_recording && (
                    <button className={`play ${playing?.id === c.id ? "on" : ""}`} aria-label="تشغيل التسجيل"
                      onClick={(e) => { e.stopPropagation(); setSeek(null); setPlaying(c); }}>▶</button>
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
      </>}
      {detailOnly && error && <div className="error">{error}</div>}

      {detail && (
        <DetailPanel call={detail} onClose={() => { setDetail(null); if (detailOnly) onFollowups?.(); }} onPlay={() => { setSeek(null); setPlaying(detail); }}>
          {/* the follow-up box first: it says what the agent must do next (follow-up stages spec, fix I) */}
          {fromShare
            ? <SharePanel key={`sh${detail.id}`} callId={detail.id} me={me} onExpired={onExpired} />
            : <FollowupPanel key={`f${detail.id}`} callId={detail.id} me={me} onExpired={onExpired} />}
          {!canSearchText && <AgentSummaryPanel key={`s${detail.id}`} callId={detail.id} onExpired={onExpired} />}
          {!fromShare && <SharePanel key={`sh${detail.id}`} callId={detail.id} me={me} onExpired={onExpired} />}
          {canSearchText && (   /* admin + supervisor only: agents see the summary only (agent-supervisor decision 1) */
            <AnalysisPanel key={`a${detail.id}`} callId={detail.id} onExpired={onExpired}
              onSeek={detail.has_recording ? (t) => seekTo(detail, t) : undefined} />
          )}
          <TranscriptPanel key={detail.id} callId={detail.id} canSeek={detail.has_recording}
            time={playing?.id === detail.id && pos?.id === detail.id ? pos.t : null}
            onSeek={(t) => seekTo(detail, t)} onExpired={onExpired} />
          {me.role === "admin" && (
            <DeletePanel key={`d${detail.id}`} callId={detail.id} onExpired={onExpired}
              onDone={() => { if (playing?.id === detail.id) setPlaying(null); setDetail(null); setFilters((f) => ({ ...f })); }} />
          )}
        </DetailPanel>
      )}
      {playing && <Player call={playing} seek={seek} onTime={onTime} onClose={() => { setSeek(null); setPlaying(null); }} />}
    </div>
  );
}

function DetailPanel({ call, onClose, onPlay, children }:
  { call: CallDetail; onClose: () => void; onPlay: () => void; children?: ReactNode }) {
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
        {call.ring ? <>   {/* outbound: ring seconds + who ended it (spec 2026-11-15-ring-time) */}
          <dt>الرنين</dt><dd>رن <span className="num">{call.ring.ring_seconds}</span> ث
            {call.ring.hangup_by && <> · قفل: {HANGUP_LABEL[call.ring.hangup_by]}</>}
            {call.ring.early_hangup && <> <span className="state bad" title="أغلقها الموظف قبل 30 ثانية رنين: لا تُحسب محاولة اتصال">قفل مبكر</span></>}</dd>
        </> : <><dt>الانتظار</dt><dd className="num">{duration(call.wait_seconds)}</dd></>}
        <dt>المكالمة</dt><dd className="num">{duration(call.talk_seconds)}</dd>
        <dt>المدة الكلية</dt><dd className="num">{duration(call.total_seconds)}</dd>
      </dl>
      {call.has_recording && <button className="primary" onClick={onPlay}>▶ تشغيل التسجيل</button>}
      {children}
    </aside>
  );
}

interface PlayerProps {
  call: CallRow;
  seek: Seek | null;                          // a transcript line was clicked
  onTime: (callId: number, seconds: number) => void;
  onClose: () => void;
}

function Player({ call, seek, onTime, onClose }: PlayerProps) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => { ref.current?.play().catch(() => undefined); }, [call.id]);
  useEffect(() => {
    const a = ref.current;
    if (!a || !seek || seek.id !== call.id) return;
    const go = () => { a.currentTime = seek.t; a.play().catch(() => undefined); };
    if (a.readyState >= 1) { go(); return; }
    a.addEventListener("loadedmetadata", go, { once: true });   // player just opened: wait for the duration
    return () => a.removeEventListener("loadedmetadata", go);
  }, [seek, call.id]);
  return (
    <div className="player">
      <div className="player-info">
        <b>{formatDateTime(call.start_time)}</b>
        <span className="muted"> · <span dir="ltr">{call.customer_number}</span> · {call.agent_ext ?? ""} {call.agent_name ?? ""}</span>
      </div>
      <audio ref={ref} key={call.id} controls preload="metadata" src={api.recordingUrl(call.id)}
        onTimeUpdate={(e) => onTime(call.id, e.currentTarget.currentTime)} />
      <button className="link" onClick={onClose} aria-label="إغلاق المشغل">✕</button>
    </div>
  );
}
