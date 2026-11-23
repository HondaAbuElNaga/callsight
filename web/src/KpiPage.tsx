import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, Unauthorized,
  type Agent, type ArchivedReport, type ForwardKpi, type KpiFilters, type KpiReport, type KpiSummary, type Me, type Status,
} from "./api";
import { Chart, type ChartOption } from "./Chart";
import { STATUS_LABEL, duration, formatDateTime, riyadhToday } from "./format";
import { Story, took } from "./ForwardsPage";

interface Props { me: Me; onCalls: () => void; onLogout: () => void; onExpired: () => void }

export const TEAM_LABEL: Record<string, string> = { customer_service: "خدمة العملاء", after_sales: "ما بعد البيع" };
const OK = "#1e7a46";
const BAD = "#b3261e";
const NA = "غير متاح";

const MAX_DAYS = 366;   // same limit as the server

// Sunday of the week that contains `day` (YYYY-MM-DD, no time zone involved): the default "from" date.
function weekStart(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() - dt.getUTCDay());
  return dt.toISOString().slice(0, 10);
}

function days(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;
}

const pct = (v: number | null) => (v === null ? "–" : `${(v * 100).toFixed(1)}%`);
const secs = (v: number | null) => (v === null ? "–" : duration(v));

type Kind = "count" | "rate" | "seconds";
interface TileDef { key: Exclude<keyof KpiSummary, "talk">; label: string; kind: Kind; goodUp: boolean }

const TEAM_TILES: TileDef[] = [
  { key: "calls", label: "المكالمات الواردة", kind: "count", goodUp: true },
  { key: "answered", label: "تم الرد", kind: "count", goodUp: true },
  { key: "unanswered", label: "لم يتم الرد", kind: "count", goodUp: false },
  { key: "answer_rate", label: "نسبة الرد", kind: "rate", goodUp: true },
  { key: "aht", label: "متوسط مدة المعالجة (AHT)", kind: "seconds", goodUp: false },
  { key: "avg_ring", label: "مدة الرنين قبل الرد", kind: "seconds", goodUp: false },
  { key: "outbound", label: "المكالمات الصادرة", kind: "count", goodUp: true },
  { key: "outbound_aht", label: "متوسط مدة الصادرة", kind: "seconds", goodUp: false },
  { key: "early_hangups", label: "قفل مبكر (أقل من 30 ث رنين)", kind: "count", goodUp: false },
  { key: "proper_rate", label: "محاولات صحيحة %", kind: "rate", goodUp: true },
  { key: "outbound_avg_ring", label: "متوسط ثواني الرنين (بلا رد)", kind: "seconds", goodUp: true },
  { key: "repeats", label: "متكررة خلال 72 ساعة", kind: "count", goodUp: false },
];
// Teams that only make calls (no incoming queue): their page shows outgoing numbers only.
// Same list on the server: callsight/kpi.py OUTBOUND_ONLY_TEAMS (the PDF follows it).
const OUTBOUND_ONLY_TEAMS = ["after_sales"];
const OUTBOUND_TILES: TileDef[] = [
  { key: "outbound", label: "المكالمات الصادرة", kind: "count", goodUp: true },
  { key: "outbound_answered", label: "الصادرة التي تم الرد عليها", kind: "count", goodUp: true },
  { key: "outbound_aht", label: "متوسط مدة الصادرة", kind: "seconds", goodUp: false },
  { key: "early_hangups", label: "قفل مبكر (أقل من 30 ث رنين)", kind: "count", goodUp: false },
  { key: "proper_rate", label: "محاولات صحيحة %", kind: "rate", goodUp: true },
  { key: "outbound_avg_ring", label: "متوسط ثواني الرنين (بلا رد)", kind: "seconds", goodUp: true },
];
const AGENT_TILES: TileDef[] = [
  { key: "calls", label: "مكالمات ردّ عليها", kind: "count", goodUp: true },
  { key: "unanswered", label: "رنات فائتة (حسب سجل السنترال)", kind: "count", goodUp: false },
  { key: "aht", label: "متوسط مدة المعالجة (AHT)", kind: "seconds", goodUp: false },
  { key: "outbound", label: "المكالمات الصادرة", kind: "count", goodUp: true },
  { key: "outbound_aht", label: "متوسط مدة الصادرة", kind: "seconds", goodUp: false },
  { key: "early_hangups", label: "قفل مبكر (أقل من 30 ث رنين)", kind: "count", goodUp: false },
  { key: "proper_rate", label: "محاولات صحيحة %", kind: "rate", goodUp: true },
  { key: "outbound_avg_ring", label: "متوسط ثواني الرنين (بلا رد)", kind: "seconds", goodUp: true },
  { key: "repeats", label: "متكررة منسوبة للموظف", kind: "count", goodUp: false },
];

/** Circle percentage (spec 2026-11-23-agent-kpi): green >= 90, orange >= 70, red below; grey ring = no data. */
function Ring({ value, size, label }: { value: number | null; size: number; label: string }) {
  const stroke = size > 100 ? 12 : 8;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = value === null ? 0 : Math.max(0, Math.min(100, value));
  const tone = value === null ? "none" : v >= 90 ? "ok" : v >= 70 ? "warn" : "bad";
  return (
    <figure className={`ring ring-${tone}`} style={{ width: size }} aria-label={`${label}: ${score(value)}`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle className="ring-track" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} />
        {value !== null && v > 0 && <circle className="ring-value" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke}
          strokeDasharray={`${(c * v) / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />}
        <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
          className={size > 100 ? "ring-num big" : "ring-num"}>{score(value)}</text>
      </svg>
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function score(v: number | null): string {
  return v === null ? "–" : `${Math.round(v * 10) / 10}%`;
}

function show(v: number | null, kind: Kind): string {
  if (kind === "rate") return pct(v);
  if (kind === "seconds") return secs(v);
  return v === null ? "–" : String(v);
}

function Change({ cur, prev, kind, goodUp }: { cur: number | null; prev: number | null; kind: Kind; goodUp: boolean }) {
  if (cur === null || prev === null) return <span className="muted">–</span>;
  const diff = cur - prev;
  if (Math.abs(diff) < 1e-9) return <span className="muted">= السابقة</span>;
  const text = kind === "rate" ? `${(Math.abs(diff) * 100).toFixed(1)} نقطة` : kind === "seconds" ? duration(Math.abs(diff)) : String(Math.abs(diff));
  return (
    <span className={(diff > 0) === goodUp ? "up" : "down"}>
      {diff > 0 ? "▲" : "▼"} <span dir="ltr" className="num">{text}</span>
    </span>
  );
}

export function KpiPage({ me, onCalls, onLogout, onExpired }: Props) {
  const [f, setF] = useState<KpiFilters>(() => { const today = riyadhToday(); return { from: weekStart(today), to: today, team: "", agent: "" }; });
  const [data, setData] = useState<KpiReport | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [archive, setArchive] = useState<ArchivedReport[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setError("تعذر تحميل المؤشرات. تأكد أن الخدمة تعمل ثم أعد المحاولة.");
  }, [onExpired]);

  const staff = me.role === "admin" || me.role === "supervisor";   // agents: own KPIs only (server enforces it)

  useEffect(() => {
    api.agents().then(setAgents).catch(fail);   // an agent gets only their own row (decides the outgoing-only view)
    if (!staff) return;
    api.reports().then(setArchive).catch(fail);
  }, [fail, staff]);

  const rangeError = !f.from || !f.to ? "اختر تاريخ البداية والنهاية"
    : f.to < f.from ? "تاريخ النهاية قبل تاريخ البداية"
    : days(f.from, f.to) > MAX_DAYS ? `أقصى مدة ${MAX_DAYS} يوماً` : "";

  useEffect(() => {   // filters apply at once (no "show" button)
    if (rangeError) return;
    let alive = true;
    setLoading(true);
    setError("");
    api.kpi(f).then((d) => alive && setData(d)).catch((e) => { if (alive) { setData(null); fail(e); } }).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [f, fail, rangeError]);

  const set = (patch: Partial<KpiFilters>) => setF((old) => ({ ...old, ...patch }));
  const teams = useMemo(() => [...new Set(agents.map((a) => a.team).filter((t): t is string => !!t))].sort(), [agents]);
  const teamAgents = agents.filter((a) => a.team && (!f.team || a.team === f.team));
  const isAgent = !!data?.scope.agent;

  const dayOption = useMemo<ChartOption>(() => {
    const days = data?.days ?? [];
    return {
      tooltip: { trigger: "axis" },
      legend: { top: 0, right: 0 },
      grid: { top: 36, left: 40, right: 16, bottom: 28 },
      // RTL: first day on the right, values on the right side
      xAxis: { type: "category", inverse: true, data: days.map((d) => d.day.slice(5).split("-").reverse().join("/")) },
      yAxis: { type: "value", position: "right", minInterval: 1 },
      series: [
        { name: "تم الرد", type: "bar", stack: "c", color: OK, data: days.map((d) => d.answered) },
        { name: isAgent ? "رنات فائتة" : "لم يتم الرد", type: "bar", stack: "c", color: BAD, data: days.map((d) => d.unanswered) },
      ],
    };
  }, [data, isAgent]);

  const statusOption = useMemo<ChartOption>(() => {
    const entries = Object.entries(data?.by_status ?? {}) as [Status, number][];
    return {
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      grid: { top: 8, left: 16, right: 150, bottom: 20 },
      xAxis: { type: "value", inverse: true, minInterval: 1 },
      yAxis: { type: "category", position: "right", inverse: true, data: entries.map(([s]) => STATUS_LABEL[s]) },
      series: [{ type: "bar", data: entries.map(([s, n]) => ({ value: n, itemStyle: { color: s === "answered" ? OK : BAD } })) }],
    };
  }, [data]);

  const viewTeam = data?.scope.agent ? agents.find((a) => a.ext === data.scope.agent)?.team : data?.scope.team;
  const outboundOnly = !!viewTeam && OUTBOUND_ONLY_TEAMS.includes(viewTeam);
  const tiles = outboundOnly ? OUTBOUND_TILES : isAgent ? AGENT_TILES : TEAM_TILES;

  return (
    <div className="page">
      <header className="topbar">
        <h1>المؤشرات</h1>
        <div className="who">
          <button className="link" onClick={onCalls}>سجل المكالمات</button>
          <span className="muted">{me.username}{me.ext ? ` · تحويلة ${me.ext}` : ""}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>

      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>من
          <input type="date" value={f.from} max={f.to || undefined} onChange={(e) => set({ from: e.target.value })} />
        </label>
        <label>إلى
          <input type="date" value={f.to} min={f.from || undefined} onChange={(e) => set({ to: e.target.value })} />
        </label>
        {staff && <>
        <label>الفريق
          <select value={f.team} onChange={(e) => set({ team: e.target.value, agent: "" })}>
            <option value="">كل الفرق</option>
            {teams.map((t) => <option key={t} value={t}>{TEAM_LABEL[t] ?? t}</option>)}
          </select>
        </label>
        <label>الموظف
          <select value={f.agent} onChange={(e) => set({ agent: e.target.value })}>
            <option value="">الكل</option>
            {teamAgents.map((a) => <option key={a.ext} value={a.ext}>{a.ext} · {a.name}</option>)}
          </select>
        </label>
        <div className="filter-actions">
          {rangeError ? <button type="button" className="primary" disabled>تصدير PDF</button>
            : <a className="button primary" href={api.reportPdfUrl(f)} download>تصدير PDF</a>}
        </div>
        </>}
      </form>

      {rangeError && <div className="error">{rangeError}</div>}
      {error && !rangeError && <div className="error">{error}</div>}
      {data && !data.trusted && <div className="banner">الأرقام غير موثقة بعد: لم تكتمل 7 مطابقات ليلية ناجحة</div>}

      {data && !rangeError && (
        <div className={loading ? "kpi dim" : "kpi"}>
          <p className="period-line">
            من <span dir="ltr" className="num">{data.period.start}</span> إلى <span dir="ltr" className="num">{data.period.end}</span>
            {data.period.partial && <span className="tag">جزئي</span>}
            <span className="muted"> · مقارنة مع <span dir="ltr" className="num">{data.previous_period.start}</span> – <span dir="ltr" className="num">{data.previous_period.end}</span></span>
          </p>
          {outboundOnly && <p className="muted small">ما بعد البيع: مكالمات صادرة فقط (لا تستقبل مكالمات واردة).</p>}

          <div className="tiles">
            {tiles.map((t) => (
              <div className="tile" key={t.key}>
                <div className="tile-label">{t.label}</div>
                <div className="tile-value num" dir="ltr">{show(data.summary[t.key], t.kind)}</div>
                <div className="tile-change small"><Change cur={data.summary[t.key]} prev={data.previous[t.key]} kind={t.kind} goodUp={t.goodUp} /></div>
              </div>
            ))}
            <div className="tile">
              <div className="tile-label">متوسط الرضا</div>
              <div className="tile-value muted">{NA}</div>
              <div className="tile-change small muted">بعد تفعيل تقييم المكالمات</div>
            </div>
          </div>

          {data.agent_score && Object.keys(data.agent_score.agents).length > 0 && (
            <section className="panel">   {/* spec 2026-11-23-agent-kpi */}
              <h2>نسبة KPI لموظف خدمة العملاء</h2>
              <div className="score-cards">
                {Object.entries(data.agent_score.agents).map(([ext, v]) => (
                  <div className="score-card" key={ext}>
                    <div className="score-name">{ext} {data.agents.find((a) => a.ext === ext)?.name ?? ""}</div>
                    <Ring value={v.kpi} size={132} label="KPI النهائي" />
                    <div className="score-parts">
                      <div><Ring value={v.answer_score} size={74} label="الرد" />
                        <span className="small muted">ردّ {v.answered} · فائتة {v.missed} · عاود {v.recalled}</span></div>
                      <div><Ring value={v.forms_score} size={74} label="النماذج" />
                        <span className="small muted">في وقتها {v.forms_on_time} من {v.forms_due}</span></div>
                      <div><Ring value={v.aht_score} size={74} label="AHT" />
                        <span className="small muted">متوسط {secs(v.aht)}</span></div>
                    </div>
                  </div>
                ))}
              </div>
              <p className="muted small">
                يوم العمل ينتهي 21:30: المكالمة بعد 21:30 تُحتسب في اليوم التالي.
                درجة الرد = (ردّ + فائتة عاود أحد الفريق الاتصال بها في نفس يوم العمل) ÷ (ردّ + فائتة)، مقرّبة لأعلى لأقرب 10
                (مثال: 85% ← 90). «فائتة» = رنّت عنده ولم يرد عليها أحد؛ إن ردّ عليها زميل فلا تُحتسب عليه، والمكالمة بلا رقم لا تُحتسب.
                درجة النماذج = النماذج المعبّأة في وقتها ÷ المطلوبة، مقرّبة لأعلى لأقرب 10؛ النموذج المتأخر لا يُحتسب.
                درجة AHT: حتى 7 دقائق = 100، و7–10 = 90، و10–13 = 80، وهكذا (−10 لكل 3 دقائق).
                KPI النهائي = متوسط الدرجات الموجودة («–» = لا توجد بيانات لهذا الجزء). لموظفي خدمة العملاء فقط.
              </p>
            </section>
          )}

          {!outboundOnly && <div className="panels">
            <section className="panel wide">
              <h2>المكالمات الواردة يومياً</h2>
              <Chart option={dayOption} label="المكالمات يومياً" />
            </section>
            {data.by_status && (
              <section className="panel">
                <h2>حالات المكالمات الواردة</h2>
                <Chart option={statusOption} height={220} label="حالات المكالمات" />
              </section>
            )}
          </div>}

          <section className="panel">
            <h2>الموظفون</h2>
            <div className="table-wrap">
              <table>
                {outboundOnly ? <>
                <thead>
                  <tr><th>الموظف</th><th>الفريق</th><th>الصادرة</th><th>تم الرد</th><th>متوسط مدة الصادرة</th>
                    <th title="مكالمة لم يُرد عليها وأغلقها الموظف قبل 30 ثانية رنين: لا تُحسب محاولة">قفل مبكر (&lt;30 ث)</th>
                    <th>محاولات صحيحة</th><th>متوسط الرنين</th></tr>
                </thead>
                <tbody>
                  {data.agents.map((a) => (
                    <tr key={a.ext} onClick={() => set({ agent: a.ext, team: f.team && a.team })}>
                      <td>{a.ext} <span className="muted">{a.name}</span></td>
                      <td>{TEAM_LABEL[a.team] ?? a.team}</td>
                      <td className="num">{a.outbound}</td>
                      <td className="num">{a.outbound_answered}</td>
                      <td className="num">{secs(a.outbound_aht)}</td>
                      <td className={a.early_hangups ? "num bad" : "num"}>{a.early_hangups}</td>
                      <td className="num">{pct(a.proper_rate)}</td>
                      <td className="num">{secs(a.outbound_avg_ring)}</td>
                    </tr>
                  ))}
                </tbody>
                </> : <>
                <thead>
                  <tr>
                    <th>الموظف</th><th>الفريق</th><th>مكالمات ردّ عليها</th><th>AHT</th><th>رنات فائتة</th>
                    <th>الصادرة (تم الرد)</th><th>AHT الصادرة</th><th>قفل مبكر</th><th>محاولات صحيحة</th>
                    <th>متوسط الرنين</th><th>متكررة</th>
                  </tr>
                </thead>
                <tbody>
                  {data.agents.map((a) => (
                    <tr key={a.ext} onClick={staff ? () => set({ agent: a.ext, team: f.team && a.team }) : undefined}>
                      <td>{a.ext} <span className="muted">{a.name}</span></td>
                      <td>{TEAM_LABEL[a.team] ?? a.team}</td>
                      <td className="num">{a.calls}</td>
                      <td className="num">{secs(a.aht)}</td>
                      <td className="num">{a.missed_rings}</td>
                      <td className="num">{a.outbound} ({a.outbound_answered})</td>
                      <td className="num">{secs(a.outbound_aht)}</td>
                      <td className={a.early_hangups ? "num bad" : "num"}>{a.early_hangups}</td>
                      <td className="num">{pct(a.proper_rate)}</td>
                      <td className="num">{secs(a.outbound_avg_ring)}</td>
                      <td className="num">{a.repeats}</td>
                    </tr>
                  ))}
                </tbody>
                </>}
              </table>
            </div>
            {!isAgent && data.summary.repeats_unattributed ? (
              <p className="muted small">مكالمات متكررة غير منسوبة لموظف (لم يُرد على الاتصال السابق): {data.summary.repeats_unattributed}</p>
            ) : null}
          </section>

          {data.summary.talk && (
            <section className="panel">
              <h2>الكلام والمقاطعة <span className="tag">آلي — للعرض فقط</span></h2>
              <p className="muted small">من المكالمات التي فُصل فيها صوت الموظف عن العميل ({data.summary.talk.calls} مكالمة). لا يدخل في أي تقييم.</p>
              <div className="table-wrap"><table>
                <thead><tr><th>الموظف</th><th>مكالمات</th><th>كلام الموظف</th><th>قاطع العميل (متوسط)</th>
                  <th>قاطعه العميل (متوسط)</th><th>أطول انتظار بعد العميل</th></tr></thead>
                <tbody>
                  {[{ key: "all", label: "الكل", t: data.summary.talk },
                    ...data.agents.filter((a) => a.talk).map((a) => ({ key: a.ext, label: `${a.ext} ${a.name}`, t: a.talk! }))]
                    .map(({ key, label, t }) => (
                      <tr key={key}>
                        <td>{label}</td>
                        <td className="num">{t.calls}</td>
                        <td className="num">{pct(t.ratio)}</td>
                        <td className="num">{t.interruptions_agent === null ? "–" : t.interruptions_agent.toFixed(1)}</td>
                        <td className="num">{t.interruptions_customer === null ? "–" : t.interruptions_customer.toFixed(1)}</td>
                        <td className="num">{secs(t.longest_wait_s)}</td>
                      </tr>
                    ))}
                </tbody>
              </table></div>
            </section>
          )}

          {data.shares && (
            <section className="panel">   {/* spec 2026-11-22-shares-tab */}
              <h2>الرد على ملاحظات المشرف (خلال {data.shares.hours} ساعة)</h2>
              {data.shares.summary.due === 0 ? <p className="muted">لا توجد ملاحظات مستحقة في هذه الفترة.</p> : (
              <div className="table-wrap">
                <table>
                  <thead><tr>
                    <th>الموظف</th><th>ملاحظات مستحقة</th><th>تم الرد في الوقت</th><th>تم الرد متأخراً</th><th>لم يتم الرد</th><th>نسبة الرد في الوقت</th>
                  </tr></thead>
                  <tbody>
                    {[...Object.entries(data.shares.agents).filter(([, v]) => v.due > 0)
                        .map(([ext, v]) => ({ key: ext, label: `${ext} ${data.agents.find((a) => a.ext === ext)?.name ?? ""}`, v })),
                      ...(isAgent ? [] : [{ key: "all", label: "الإجمالي", v: data.shares.summary }])].map(({ key, label, v }) => (
                      <tr key={key}>
                        <td>{label}</td><td className="num">{v.due}</td><td className="num">{v.on_time}</td>
                        <td className="num">{v.late}</td><td className="num">{v.missing}</td><td className="num">{pct(v.rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              )}
              <p className="muted small">
                حسب يوم إرسال الملاحظة{data.shares.since ? `، من ${data.shares.since}` : ""}. للموظف {data.shares.hours} ساعة من وقت الإرسال ليرد؛ تُحتسب الملاحظة بعد انتهاء المهلة أو بعد الرد. الرد 10 أحرف على الأقل؛ تعديله خلال المهلة لا يغيّر وقت الرد الأول، وتعديله بعدها يُحتسب ردًا متأخرًا. تُحتسب فقط لموظفي الفرق (تحويلة مسجلة في الفرق).
              </p>
            </section>
          )}

          {data.followup?.enabled && (
            <section className="panel">
              <h2>المتابعة {!data.followup.active && <span className="tag">للعرض فقط — لا تدخل في التقارير بعد</span>}</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>الموظف</th><th>نماذج مطلوبة</th><th>في وقتها</th><th>نسبة النماذج</th>
                      <th>عاد العميل بعد الحل</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...Object.entries(data.followup.agents).map(([ext, v]) => ({ key: ext, label: `${ext} ${data.agents.find((a) => a.ext === ext)?.name ?? ""}`, v })),
                      ...(isAgent ? [] : [{ key: "all", label: "الإجمالي", v: data.followup.summary }])].map(({ key, label, v }) => (
                      <tr key={key}>
                        <td>{label}</td>
                        <td className="num">{v.forms_due}</td><td className="num">{v.forms_on_time}</td><td className="num">{pct(v.forms_rate)}</td>
                        <td className="num">{v.came_back}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <h3>مراحل المتابعة</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>الموظف</th><th>نماذج بها مشكلة</th><th>حُلّت من أول مكالمة</th><th>نسبة الحل من أول مكالمة</th>
                      <th>حُلّت بعد المتابعة</th><th>متوسط جولات المعاودة</th><th>ما زالت مفتوحة</th>
                      <th>ملاحظات بعد المعاودة</th><th>في وقتها</th><th>نسبة الملاحظات</th>
                      <th>لا تحتاج متابعة</th><th>نسبتها</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...Object.entries(data.followup.agents).map(([ext, v]) => ({ key: ext, label: `${ext} ${data.agents.find((a) => a.ext === ext)?.name ?? ""}`, v })),
                      ...(isAgent ? [] : [{ key: "all", label: "الإجمالي", v: data.followup.summary }])].map(({ key, label, v }) => (
                      <tr key={key}>
                        <td>{label}</td>
                        <td className="num">{v.answered_forms}</td><td className="num">{v.solved_first}</td><td className="num">{pct(v.first_rate)}</td>
                        <td className="num">{v.solved_after}</td><td className="num">{v.avg_rounds == null ? "–" : v.avg_rounds.toFixed(1)}</td>
                        <td className="num">{v.open}</td>
                        <td className="num">{v.notes_due}</td><td className="num">{v.notes_on_time}</td><td className="num">{pct(v.notes_rate)}</td>
                        <td className="num">{v.no_followup}</td><td className="num">{pct(v.no_followup_rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small">
                تُحتسب من {data.followup.since ?? data.followup.start}. النموذج في وقته = قبل 21:30 يوم المكالمة (مكالمة بعد 21:30: قبل 21:30 اليوم التالي؛ المكالمات قبل 24/11 بالقاعدة القديمة 23:00).
                المعاودات تُحتسب في كل جولة. الملاحظة بعد المعاودة في وقتها = قبل 21:30 يوم مكالمة المعاودة (أو 21:30 اليوم التالي إن كانت بعد 21:30).
                «نماذج بها مشكلة» = النماذج المعبّأة عدا «لا تحتاج متابعة». «ما زالت مفتوحة» = بانتظار معاودة أو معاودة فائتة أو تحتاج ملاحظة.
                «لا تحتاج متابعة» = نماذج عُلّمت بلا متابعة، و«نسبتها» = حصتها من كل النماذج المعبّأة (نسبة عالية تستحق المراجعة).
                «عاد العميل بعد الحل» للعرض فقط.
              </p>
            </section>
          )}

          {staff && <ForwardPeople from={f.from} to={f.to} agent={f.agent} onExpired={onExpired} />}

          <div className="panels">
            <section className="panel">
              <h2>أكثر الأرقام تكراراً</h2>
              {data.top_repeaters.length === 0 ? <p className="muted">لا توجد مكالمات متكررة.</p> : (
                <ul className="plain">
                  {data.top_repeaters.map((r, i) => (
                    <li key={i}><span dir="ltr" className="num">{r.number}</span> · {r.repeats}</li>
                  ))}
                </ul>
              )}
            </section>
            {staff && <section className="panel wide">
              <h2>التقارير المحفوظة</h2>
              {archive.length === 0 ? <p className="muted">لا توجد تقارير محفوظة بعد (تُنشأ الأحد 7:00 وأول كل شهر 7:00).</p> : (
                <ul className="plain">
                  {archive.map((r) => (
                    <li key={r.id}>
                      <a href={api.archivedPdfUrl(r.id)} download>
                        {r.period_type === "week" ? "أسبوعي" : "شهري"} · <span dir="ltr" className="num">{r.period_start} – {r.period_end}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </section>}
          </div>

          <ol className="notes muted small">
            <li>متوسط مدة المعالجة = مدة الكلام فقط (سجل السنترال لا يحتوي على مدة التعليق)، والمكالمات المحوّلة تحسب أول من ردّ.</li>
            <li>مكالمات الموظف = الواردة التي ردّ عليها، لذلك مجموع الموظفين لا يساوي إجمالي الفريق (مثل مكالمات ردّ عليها المشرف).</li>
            <li>مدة الرنين قبل الرد: وقت رنين هاتف الموظف، وليست مدة الانتظار في الطابور. "لم يتم الرد" يشمل البريد الصوتي.</li>
            <li>المكالمة المتكررة: واردة تم الرد عليها وسبقها اتصال من/إلى نفس الرقم مع نفس الفريق خلال 72 ساعة؛ تُنسب لموظف آخر مكالمة سابقة تم الرد عليها.</li>
          </ol>
        </div>
      )}
      {!data && loading && <div className="center muted">جارٍ التحميل…</div>}
    </div>
  );
}

// ---------- D4 (req 20): the people problems were forwarded to (show-only) ----------

const LEVEL_TITLE: Record<number, string> = { 1: "موظفو الفروع", 2: "مشرفو الفروع", 3: "الإدارة" };

/** Who solves forwarded problems in their 24 h, who lets them run out (each case one line: open it to see the
 *  whole story), and how fast our agents answer the questions asked on the ticket. By the من/إلى above. */
function ForwardPeople({ from, to, agent, onExpired }: { from: string; to: string; agent: string;
  onExpired: () => void }) {
  const [d, setD] = useState<ForwardKpi | null>(null);
  const [open, setOpen] = useState<string | null>(null);      // "p:<level>:<id>" / "a:<ext>": its mistakes list
  const [story, setStory] = useState<number | null>(null);
  const [reload, setReload] = useState(0);     // a supervisor's close in the story: read the numbers again
  const [error, setError] = useState("");
  const expired = useRef(onExpired);       // a new function each render must not refetch
  expired.current = onExpired;
  useEffect(() => {
    if (!from || !to || to < from) return;
    let live = true;
    const t = setTimeout(() => {          // typing a date: one request when it settles
      setError("");
      api.forwardKpi({ from, to, agent: agent || undefined }).then((x) => { if (live) setD(x); }).catch((e) => {
        if (!live) return;
        if (e instanceof Unauthorized) expired.current(); else setError("تعذر تحميل أرقام التحويلات.");
      });
    }, 400);
    return () => { live = false; clearTimeout(t); };
  }, [from, to, agent, reload]);
  if (error) return <section className="panel"><p className="error">{error}</p></section>;
  if (!d || (d.people.length === 0 && d.agents.length === 0)) return null;
  const toggle = (k: string) => setOpen(open === k ? null : k);
  return (
    <section className="panel wide">
      <h2>التحويلات: من حلّ في وقته ومن تأخر</h2>
      {[1, 2, 3].map((lvl) => {
        const rows = d.people.filter((r) => r.level === lvl);
        if (rows.length === 0) return null;
        return <div key={lvl}>
          <h3>{LEVEL_TITLE[lvl]}</h3>
          <div className="table-wrap"><table>
            <thead><tr><th>الاسم</th><th>الفرع</th><th>استلم</th><th>حلّ</th><th>حلّ خلال 24 ساعة</th>
              <th>نسبة الحل خلال 24 ساعة</th><th>متوسط وقت الحل (من استلامه)</th><th>عنده الآن</th>
              <th>تأخر (أخطاء)</th>
              <th title="تجاوز 24 ساعة على الساعة، لكن التأخير كان بانتظار رد موظف المكالمة أو تقييم الرضا عندنا">تأخر ليس بسببه</th>
            </tr></thead>
            <tbody>{rows.map((r) => {
              const k = `p:${r.level}:${r.person_id}`;
              return [<tr key={k}>
                <td>{r.name ?? "–"}</td><td className="muted">{r.branch ?? "–"}</td>
                <td className="num">{r.received}</td><td className="num">{r.solved}</td>
                <td className="num">{r.solved_in_24h}</td><td className="num">{pct(r.in_24h_rate)}</td>
                <td className="num">{took(r.avg_seconds_to_solve)}</td><td className="num">{r.open}</td>
                <td className="num">{r.late ? <button type="button" className="link bad-text" aria-expanded={open === k}
                  onClick={() => toggle(k)}>{r.late}</button> : 0}</td>
                <td className="num muted">{r.excused}</td>
              </tr>,
              open === k && <tr key={`${k}:list`}><td colSpan={10}><Mistakes items={r.mistakes.map((m) => ({
                call_id: m.call_id, at: m.sent_at, reason: m.reason,
                extra: [`العميل ${m.customer_number ?? "–"}`, m.due ? `الموعد ${formatDateTime(m.due)}` : "",
                  m.ended_at ? `انتهت ${formatDateTime(m.ended_at)}` : "ما زالت عنده",
                  m.agent_wait_seconds ? `منها ${took(m.agent_wait_seconds)} بانتظار رد موظف المكالمة (لا تُحسب عليه)` : "",
                  m.ticket_number ? `تذكرة ${m.ticket_number}` : "", `موظف المكالمة: ${m.agent ?? "–"}`]
                  .filter(Boolean).join(" · ") }))}
                total={r.late} onOpen={setStory} /></td></tr>];
            })}</tbody>
          </table></div>
        </div>;
      })}
      {d.agents.length > 0 && <>
        <h3>ردود موظفي المكالمات على أسئلة التذكرة</h3>
        <div className="table-wrap"><table>
          <thead><tr><th>موظف المكالمة</th><th>أسئلة</th><th>رد في وقته</th><th>تأخر (أخطاء)</th><th>أسئلة تنتظر الرد</th></tr></thead>
          <tbody>{d.agents.map((a) => {
            const k = `a:${a.ext}`;
            return [<tr key={k}>
              <td>{a.ext} <span className="muted">{a.name}</span></td><td className="num">{a.questions}</td>
              <td className="num">{a.on_time}</td>
              <td className="num">{a.late ? <button type="button" className="link bad-text" aria-expanded={open === k}
                onClick={() => toggle(k)}>{a.late}</button> : 0}</td>
              <td className="num">{a.waiting}</td>
            </tr>,
            open === k && <tr key={`${k}:list`}><td colSpan={5}><Mistakes items={a.mistakes.map((m) => ({
              call_id: m.call_id, at: m.asked_at, reason: m.reason,
              extra: `سؤال من «${m.from ?? "–"}» · الموعد ${m.due ? formatDateTime(m.due) : "–"}${m.answered_at ? ` · ردّ ${formatDateTime(m.answered_at)}` : ""}` }))}
              total={a.late} onOpen={setStory} /></td></tr>];
          })}</tbody>
        </table></div>
      </>}
      {story !== null && <Story id={story} onClose={() => setStory(null)} onExpired={onExpired}
        onChanged={() => setReload((n) => n + 1)} />}
      <p className="muted small">
        بحسب يوم استلام كل شخص للمشكلة. كل شخص له 24 ساعة من وقته هو: الوقت الذي ينتظر فيه سؤالُه رد موظف المكالمة
        لا يُحسب عليه. «تأخر» = تجاوز وقته 24 ساعة دون أن يحلّها (انتقلت لمن بعده، أو ما زالت عنده، أو حلّها بعدها).
        مشرف الفرع الذي أغلقها دون مكالمة جيدة للعميل يُذكر السبب؛ وإن كان ينتظر تقييم الرضا عندنا فلا يُحسب عليه.
        النسبة من المشاكل التي انتهت مهلتها فقط. اضغط عدد الأخطاء لرؤية الحالات، ثم الحالة لرؤية قصتها كاملة.
        ردود موظفي المكالمات: في وقتها = قبل 21:30 يوم السؤال (سؤال بعد 21:30: قبل 21:30 اليوم التالي)؛ «اطّلعت، لا يحتاج
        رداً» يُحتسب رداً؛ إن حُلّت المشكلة قبل الموعد فلا يُحتسب السؤال. فلتر «الموظف» أعلاه يغيّر جدول موظفي المكالمات
        فقط. للعرض فقط.
      </p>
    </section>
  );
}

function Mistakes({ items, total, onOpen }: { total: number; onOpen: (id: number) => void;
  items: { call_id: number; at: string | null; reason: string; extra: string }[] }) {
  return <>
    <ul className="mistakes">
      {items.map((m, i) => <li key={i}>
        <button type="button" className="link" onClick={() => onOpen(m.call_id)}>
          استلمها {m.at ? formatDateTime(m.at) : "–"}</button> · <span className="bad-text">{m.reason}</span>
        <span className="muted small"> · {m.extra}</span>
      </li>)}
    </ul>
    {total > items.length && <p className="muted small">تظهر أول {items.length} من {total}.</p>}
  </>;
}
