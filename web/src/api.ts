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
  transcript_match: boolean;   // the row came from a transcript word search (badge only, no snippet)
}

export interface TranscriptSegment {
  seq: number;
  start: number;
  end: number;
  speaker: string | null;
  dropped: boolean;            // silence / unclear window: no text
  text: string;
}

export interface Transcript {
  call_id: number;
  status: "queued" | "running" | "done" | "error" | "skipped";
  model: string;
  created_at: string | null;
  dropped_windows: number;
  segments: TranscriptSegment[];
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
  words: string;               // transcript words: sent to the API only, never put in the page URL
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

export interface HealthItem { key: string; state: "ok" | "warn" | "bad" | "off"; [k: string]: unknown }
export interface Health { ok: boolean; problems: string[]; checked_at: string; items: HealthItem[] }

// ---------- call analysis (spec 2026-11-14-call-analysis): admin + supervisor only ----------
export interface AnalysisContent {
  status: "queued" | "running" | "done" | "error" | "skipped";
  model: string;
  prompt_version: string;
  summary?: string;              // plain text: rendered as text, never as HTML
  topic?: string;
  topic_label?: string;
  action_items?: string[];
  escalation?: boolean | null;
  trimmed?: boolean;
}
export interface Estimate { score: number; reason: string; label: string }
export interface CallAnalysis {
  call_id: number;
  analysis: AnalysisContent | null;
  estimate: Estimate | null;
  estimate_hidden: "blind" | "not_frozen" | null;
  flags: { kind: string; phrase: string; start: number; end: number }[];
  silence: { share: number; seconds: number; longest: number; count: number; long_count: number } | null;
}
export interface RatingProgress {
  labels: number; labels_target: number; blind_frozen: boolean; blind_rated: number; blind_target: number; mine: boolean;
}
export interface RatingItem { call_id: number; start_time: string; talk_seconds: number; blind: boolean }
export interface RatingQueue { progress: RatingProgress; items: RatingItem[] }
export interface RatingCall {
  call_id: number;
  start_time: string;
  talk_seconds: number;
  has_recording: boolean;
  blind: boolean;
  analysis: AnalysisContent | null;
  estimate: Estimate | null;     // always null for blind-set calls
  my_label: { score: number; note: string | null } | null;
}


// Live board (WebSocket /api/live). Caller numbers arrive masked (last 4 digits) from the server.
export interface LiveCall {
  call_id: string;
  state: "waiting" | "ringing" | "talking" | "holding";
  direction: "inbound" | "outbound" | "internal";
  agents: string[];            // extensions talking / holding
  ringing: string[];           // extensions ringing
  caller: string | null;       // masked, e.g. ••••4567
  queue: string | null;
  started_at: number;          // epoch seconds
  answered_at: number | null;
}

export type LiveDelta =
  | { op: "reset" }
  | { op: "remove"; call_id: string }
  | { op: "upsert"; call: LiveCall };

export type LiveMessage =
  | { type: "snapshot"; available: boolean; seq: number | null; status: string; calls: LiveCall[];
      last_seen: number | null; server_time: number }
  | { type: "delta"; seq: number; status: string; deltas: LiveDelta[]; published_at: number };

export class Unauthorized extends Error {}
export class NotFound extends Error {}
export class BadRequest extends Error {}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 401) throw new Unauthorized();
  if (r.status === 404) throw new NotFound();
  if (r.status === 422) throw new BadRequest();
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
  transcript: (id: number) => get<Transcript>(`/api/calls/${id}/transcript`),
  transcriptTxtUrl: (id: number) => `/api/calls/${id}/transcript.txt`,
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
  health: () => get<Health>("/api/health"),
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
  analysis: (id: number) => get<CallAnalysis>(`/api/calls/${id}/analysis`),
  ratingQueue: () => get<RatingQueue>("/api/rating/queue"),
  ratingCall: (id: number) => get<RatingCall>(`/api/rating/calls/${id}`),
  async saveRating(id: number, score: number, note: string): Promise<void> {
    const r = await fetch(`/api/rating/calls/${id}`, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ score, note: note.trim() || null }),
    });
    if (r.status === 401) throw new Unauthorized();
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  },
};
