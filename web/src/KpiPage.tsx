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
// Teams that only make calls (no incoming queue): their page shows outgoing numbers only.
// Same list on the server: callsight/kpi.py OUTBOUND_ONLY_TEAMS (the PDF follows it).
const OUTBOUND_ONLY_TEAMS = ["after_sales"];
const OUTBOUND_TILES: TileDef[] = [
  { key: "outbound", label: "المكالمات الصادرة", kind: "count", goodUp: true },
  { key: "outbound_answered", label: "الصادرة التي تم الرد عليها", kind: "count", goodUp: true },
  { key: "outbound_aht", label: "متوسط مدة الصادرة", kind: "seconds", goodUp: false },
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
                  <tr><th>الموظف</th><th>الفريق</th><th>الصادرة</th><th>تم الرد</th><th>متوسط مدة الصادرة</th></tr>
                </thead>
                <tbody>
                  {data.agents.map((a) => (
                    <tr key={a.ext} onClick={() => set({ agent: a.ext, team: f.team && a.team })}>
                      <td>{a.ext} <span className="muted">{a.name}</span></td>
                      <td>{TEAM_LABEL[a.team] ?? a.team}</td>
                      <td className="num">{a.outbound}</td>
                      <td className="num">{a.outbound_answered}</td>
                      <td className="num">{secs(a.outbound_aht)}</td>
                    </tr>
                  ))}
                </tbody>
                </> : <>
                <thead>
                  <tr>
                    <th>الموظف</th><th>الفريق</th><th>مكالمات ردّ عليها</th><th>AHT</th><th>رنات فائتة</th>
                    <th>الصادرة (تم الرد)</th><th>AHT الصادرة</th><th>متكررة</th>
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

          {data.followup?.enabled && (
            <section className="panel">
              <h2>المتابعة ومعاودة الاتصال {!data.followup.active && <span className="tag">للعرض فقط — لا تدخل في التقارير بعد</span>}</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>الموظف</th><th>نماذج مطلوبة</th><th>في وقتها</th><th>نسبة النماذج</th>
                      <th>معاودات مستحقة</th><th>في وقتها</th><th>نسبة المعاودة</th><th>عاد العميل بعد الحل</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...Object.entries(data.followup.agents).map(([ext, v]) => ({ key: ext, label: `${ext} ${data.agents.find((a) => a.ext === ext)?.name ?? ""}`, v })),
                      ...(isAgent ? [] : [{ key: "all", label: "الإجمالي", v: data.followup.summary }])].map(({ key, label, v }) => (
                      <tr key={key}>
                        <td>{label}</td>
                        <td className="num">{v.forms_due}</td><td className="num">{v.forms_on_time}</td><td className="num">{pct(v.forms_rate)}</td>
                        <td className="num">{v.callbacks_due}</td><td className="num">{v.callbacks_on_time}</td><td className="num">{pct(v.callbacks_rate)}</td>
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
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small">
                تُحتسب من {data.followup.since ?? data.followup.start}. النموذج في وقته = قبل 23:00 يوم المكالمة (أو خلال ساعتين من نهاية مكالمة انتهت بعد 21:00). معاودة الاتصال في وقتها = خلال 60 دقيقة من الوقت المحدد، من أي موظف في الفريق.
                المعاودات تُحتسب في كل جولة. الملاحظة بعد المعاودة في وقتها = قبل 23:00 يوم مكالمة المعاودة (أو خلال ساعتين منها).
                «نماذج بها مشكلة» = النماذج المعبّأة عدا «لا تحتاج متابعة». «ما زالت مفتوحة» = بانتظار معاودة أو معاودة فائتة أو تحتاج ملاحظة.
                «عاد العميل بعد الحل» للعرض فقط.
              </p>
            </section>
          )}

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
