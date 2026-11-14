import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api, Unauthorized,
  type Agent, type ArchivedReport, type KpiFilters, type KpiReport, type KpiSummary, type Me, type Status,
} from "./api";
import { Chart, type ChartOption } from "./Chart";
import { STATUS_LABEL, duration, riyadhToday } from "./format";

interface Props { me: Me; onCalls: () => void; onLogout: () => void; onExpired: () => void }

export const TEAM_LABEL: Record<string, string> = { customer_service: "خدمة العملاء", after_sales: "ما بعد البيع" };
const OK = "#1e7a46";
const BAD = "#b3261e";
const NA = "غير متاح";

// Move the chosen day one week / one month back or forward (dates as YYYY-MM-DD, no time zone involved).
function shift(f: KpiFilters, step: number): string {
  const [y, m, d] = f.start.split("-").map(Number);
  const dt = f.period === "week" ? new Date(Date.UTC(y, m - 1, d + 7 * step)) : new Date(Date.UTC(y, m - 1 + step, 1));
  return dt.toISOString().slice(0, 10);
}

const pct = (v: number | null) => (v === null ? "–" : `${(v * 100).toFixed(1)}%`);
const secs = (v: number | null) => (v === null ? "–" : duration(v));

type Kind = "count" | "rate" | "seconds";
interface TileDef { key: keyof KpiSummary; label: string; kind: Kind; goodUp: boolean }

const TEAM_TILES: TileDef[] = [
  { key: "calls", label: "المكالمات الواردة", kind: "count", goodUp: true },
  { key: "answered", label: "تم الرد", kind: "count", goodUp: true },
  { key: "unanswered", label: "لم يتم الرد", kind: "count", goodUp: false },
  { key: "answer_rate", label: "نسبة الرد", kind: "rate", goodUp: true },
  { key: "aht", label: "متوسط مدة المعالجة (AHT)", kind: "seconds", goodUp: false },
  { key: "avg_ring", label: "مدة الرنين قبل الرد", kind: "seconds", goodUp: false },
  { key: "outbound", label: "المكالمات الصادرة", kind: "count", goodUp: true },
  { key: "outbound_aht", label: "متوسط مدة الصادرة", kind: "seconds", goodUp: false },
  { key: "repeats", label: "متكررة خلال 72 ساعة", kind: "count", goodUp: false },
];
const AGENT_TILES: TileDef[] = [
  { key: "calls", label: "مكالمات ردّ عليها", kind: "count", goodUp: true },
  { key: "unanswered", label: "رنات فائتة (حسب سجل السنترال)", kind: "count", goodUp: false },
  { key: "aht", label: "متوسط مدة المعالجة (AHT)", kind: "seconds", goodUp: false },
  { key: "outbound", label: "المكالمات الصادرة", kind: "count", goodUp: true },
  { key: "outbound_aht", label: "متوسط مدة الصادرة", kind: "seconds", goodUp: false },
  { key: "repeats", label: "متكررة منسوبة للموظف", kind: "count", goodUp: false },
];

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
  const [f, setF] = useState<KpiFilters>(() => ({ period: "week", start: riyadhToday(), team: "", agent: "" }));
  const [data, setData] = useState<KpiReport | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [archive, setArchive] = useState<ArchivedReport[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setError("تعذر تحميل المؤشرات. تأكد أن الخدمة تعمل ثم أعد المحاولة.");
  }, [onExpired]);

  useEffect(() => {
    api.agents().then(setAgents).catch(fail);
    api.reports().then(setArchive).catch(fail);
  }, [fail]);

  useEffect(() => {   // filters apply at once (no "show" button)
    let alive = true;
    setLoading(true);
    setError("");
    api.kpi(f).then((d) => alive && setData(d)).catch((e) => { if (alive) { setData(null); fail(e); } }).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [f, fail]);

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

  const tiles = isAgent ? AGENT_TILES : TEAM_TILES;

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
        <label>الفترة
          <select value={f.period} onChange={(e) => set({ period: e.target.value as KpiFilters["period"] })}>
            <option value="week">أسبوع (الأحد – السبت)</option>
            <option value="month">شهر</option>
          </select>
        </label>
        <label>يوم داخل الفترة
          <input type="date" value={f.start} onChange={(e) => e.target.value && set({ start: e.target.value })} />
        </label>
        <div className="filter-actions step">
          <button type="button" onClick={() => set({ start: shift(f, -1) })}>→ السابق</button>
          <button type="button" onClick={() => set({ start: shift(f, 1) })}>التالي ←</button>
        </div>
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
          <a className="button primary" href={api.reportPdfUrl(f)} download>تصدير PDF</a>
        </div>
      </form>

      {error && <div className="error">{error}</div>}
      {data && !data.trusted && <div className="banner">الأرقام غير موثقة بعد: لم تكتمل 7 مطابقات ليلية ناجحة</div>}

      {data && (
        <div className={loading ? "kpi dim" : "kpi"}>
          <p className="period-line">
            من <span dir="ltr" className="num">{data.period.start}</span> إلى <span dir="ltr" className="num">{data.period.end}</span>
            {data.period.partial && <span className="tag">جزئي</span>}
            <span className="muted"> · مقارنة مع <span dir="ltr" className="num">{data.previous_period.start}</span> – <span dir="ltr" className="num">{data.previous_period.end}</span></span>
          </p>

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

          <div className="panels">
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
          </div>

          <section className="panel">
            <h2>الموظفون</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>الموظف</th><th>الفريق</th><th>مكالمات ردّ عليها</th><th>AHT</th><th>رنات فائتة</th>
                    <th>الصادرة (تم الرد)</th><th>AHT الصادرة</th><th>متكررة</th>
                  </tr>
                </thead>
                <tbody>
                  {data.agents.map((a) => (
                    <tr key={a.ext} onClick={() => set({ agent: a.ext, team: f.team && a.team })}>
                      <td>{a.ext} <span className="muted">{a.name}</span></td>
                      <td>{TEAM_LABEL[a.team] ?? a.team}</td>
                      <td className="num">{a.calls}</td>
                      <td className="num">{secs(a.aht)}</td>
                      <td className="num">{a.missed_rings}</td>
                      <td className="num">{a.outbound} ({a.outbound_answered})</td>
                      <td className="num">{secs(a.outbound_aht)}</td>
                      <td className="num">{a.repeats}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!isAgent && data.summary.repeats_unattributed ? (
              <p className="muted small">مكالمات متكررة غير منسوبة لموظف (لم يُرد على الاتصال السابق): {data.summary.repeats_unattributed}</p>
            ) : null}
          </section>

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
            <section className="panel wide">
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
            </section>
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
