export type Status = "answered" | "abandoned_queue" | "abandoned_ivr" | "no_answer" | "busy" | "voicemail";

export interface CallRow {
  id: number;
  start_time: string;
  direction: "inbound" | "outbound" | "internal";
  entry: string;
  customer_number: string | null;
  queue_number: string | null;
  agent_ext: string | null;
  agent_name: string | null;
  answered_by_agent: boolean;
  status: Status;
  ivr_seconds: number;
  wait_seconds: number;
  talk_seconds: number;
  total_seconds: number;
  has_recording: boolean;
}

export interface Leg {
  id: number;
  kind: "ivr" | "queue" | "extension" | "external" | "voicemail";
  start_time: string;
  to_number: string | null;
  disposition: string | null;
  duration: number | null;
  ring_duration: number | null;
  talk_duration: number | null;
  reason: string | null;
}

export interface CallDetail extends CallRow { legs: Leg[] }
export interface CallPage { total: number; page: number; page_size: number; items: CallRow[] }
export interface Summary { total: number; by_status: Record<Status, number> }
export interface Agent { ext: string; name: string; team: string | null }
export interface Me { username: string; role: string; ext: string | null }

export interface Filters {
  from: string;
  to: string;
  agent: string;
  status: string;
  direction: string;
  q: string;
}

export interface GoldClip {
  id: number;
  call_id: number;
  seconds: number;
  has_text: boolean;
  typed_at: string | null;
  agent_ext: string | null;
  direction: string | null;
  reference_text?: string;
}

export interface KpiSummary {
  calls: number;
  answered: number;
  unanswered: number;
  answer_rate: number | null;
  aht: number | null;
  avg_ring: number | null;
  outbound: number;
  outbound_answered: number;
  outbound_aht: number | null;
  repeats: number;
  repeats_unattributed: number | null;
  satisfaction: number | null;
}

export interface KpiAgent {
  ext: string;
  name: string;
  team: string;
  calls: number;
  aht: number | null;
  missed_rings: number;
  outbound: number;
  outbound_answered: number;
  outbound_aht: number | null;
  repeats: number;
}

export interface KpiPeriod { kind: "week" | "month"; start: string; end: string; partial: boolean }

export interface KpiReport {
  period: KpiPeriod;
  previous_period: KpiPeriod;
  scope: { team: string | null; agent: string | null };
  trusted: boolean;
  summary: KpiSummary;
  previous: KpiSummary;
  agents: KpiAgent[];
  by_status: Record<Status, number> | null;
  top_repeaters: { number: string; repeats: number }[];
  days: { day: string; calls: number; answered: number; unanswered: number }[];
}

export interface KpiFilters { period: "week" | "month"; start: string; team: string; agent: string }

export interface ArchivedReport {
  id: number;
  period_type: "week" | "month";
  period_start: string;
  period_end: string;
  scope: string;
  size_bytes: number;
  generated_at: string;
}

export class Unauthorized extends Error {}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 401) throw new Unauthorized();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

function kpiQuery(f: KpiFilters): string {
  const p = new URLSearchParams({ period: f.period, start: f.start });
  if (f.team) p.set("team", f.team);
  if (f.agent) p.set("agent", f.agent);
  return p.toString();
}

function query(f: Filters, extra: Record<string, string> = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...f, ...extra })) if (v) p.set(k, v);
  return p.toString();
}

export const api = {
  me: () => get<Me>("/api/me"),
  agents: () => get<Agent[]>("/api/agents"),
  calls: (f: Filters, page: number) => get<CallPage>(`/api/calls?${query(f, { page: String(page) })}`),
  summary: (f: Filters) => get<Summary>(`/api/calls/summary?${query(f)}`),
  call: (id: number) => get<CallDetail>(`/api/calls/${id}`),
  recordingUrl: (id: number) => `/api/calls/${id}/recording`,
  async login(username: string, password: string): Promise<{ ok: true } | { ok: false; message: string }> {
    const r = await fetch("/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (r.ok) return { ok: true };
    return { ok: false, message: "اسم المستخدم أو كلمة المرور غير صحيحة، أو الحساب موقوف 15 دقيقة بعد 5 محاولات خاطئة" };
  },
  kpi: (f: KpiFilters) => get<KpiReport>(`/api/kpi?${kpiQuery(f)}`),
  reportPdfUrl: (f: KpiFilters) => `/api/reports/pdf?${kpiQuery(f)}`,
  reports: () => get<ArchivedReport[]>("/api/reports"),
  archivedPdfUrl: (id: number) => `/api/reports/${id}/pdf`,
  goldset: () => get<GoldClip[]>("/api/goldset"),
  goldClip: (id: number) => get<GoldClip>(`/api/goldset/${id}`),
  goldAudioUrl: (id: number) => `/api/goldset/${id}/audio`,
  async saveGoldText(id: number, text: string): Promise<GoldClip> {
    const r = await fetch(`/api/goldset/${id}`, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (r.status === 401) throw new Unauthorized();
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json() as Promise<GoldClip>;
  },
  logout: () => fetch("/api/logout", { method: "POST", credentials: "same-origin" }),
};
