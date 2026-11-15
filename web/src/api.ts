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
  draft_text?: string;              // machine draft (goldset drafts), shown only until the admin saves a text
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

export interface KpiPeriod { kind: "week" | "month" | "range"; start: string; end: string; partial: boolean }

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
  followup?: FollowupKpi;
}

export interface KpiFilters { from: string; to: string; team: string; agent: string }   // from/to: YYYY-MM-DD, both days included

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
  // long_s = the "long silence" threshold (seconds) these numbers were computed with (config/rules.yaml)
  silence: { share: number; seconds: number; longest: number; count: number; long_count: number;
             long_s: number | null } | null;
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
  analysis: AnalysisContent | null;   // always null for blind-set calls (no LLM judgement before the rating)
  estimate: Estimate | null;     // always null for blind-set calls
  my_label: { score: number; note: string | null } | null;
  locked: boolean;               // blind-set call already rated by me: the rating is final
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

// ---------- agent + supervisor work (spec 2026-11-15-agent-supervisor) ----------
export type CallbackStatus = "waiting" | "on_time" | "late" | "missed" | "no_number";
export type FormState = "missing" | "overdue" | "on_time" | "late";
export interface FollowupForm {
  no_followup: boolean;
  what_happened: string | null;
  solved: boolean | null;
  responsible_kind: "user" | "customer" | "other_department" | null;
  responsible_user_id: number | null;
  expected_at: string | null;
  callback_at: string | null;
  first_filled_at: string;
  updated_at: string;
}
export interface Callback { at: string; due: string; status: CallbackStatus; by_ext: string | null; call_id: number | null; time: string | null;
  answered: boolean | null; round: number }   // from the PBX record of the callback call (null = no callback call yet)
export interface FollowupItem {
  call_id: number; start_time: string; end_time: string; customer_number: string | null; agent_ext: string | null;
  agent_name: string; talk_seconds: number; deadline: string; form_state: FormState; form: FollowupForm | null;
  callback: Callback | null; came_back: boolean;
  step: Step; round: number; note_deadline: string | null; history: HistoryEntry[];
}
// follow-up stages (spec 2026-11-15-followup-stages)
export type Step = "required" | "overdue" | "no_followup" | "solved_first" | "waiting_callback" | "callback_missed"
  | "note_needed" | "solved_after" | "no_number";
export interface HistoryEntry {
  round: number; what_happened: string | null; solved: boolean | null; no_followup: boolean;
  responsible_kind: string | null; responsible_user_id: number | null; responsible: string | null;
  expected_at: string | null; callback_at: string | null; at: string; by: string | null;
  callback_status: CallbackStatus | null;
  callback_call: { call_id: number; time: string; by_ext: string | null; answered: boolean | null } | null;
}
export interface NoteIn {
  round: number; what_happened: string; solved: boolean; responsible_kind?: string;
  responsible_user_id?: number | null; expected_at?: string; callback_at?: string;
}
export interface FollowupCounts { forms: number; notes: number; callbacks_today: number; missed: number }
export interface Person { id: number; username: string; role: string; ext: string | null }
export type FollowupView =
  | { needed: false; enabled: boolean }
  | (FollowupItem & { needed: true; enabled: true; can_edit: boolean; lock_reason: string | null; options: Person[] });
export interface FollowupList { settings: Settings; items: FollowupItem[]; truncated?: boolean; linked: boolean;
  counts: FollowupCounts }
export interface FollowupIn {
  no_followup: boolean; what_happened?: string; solved?: boolean; responsible_kind?: string;
  responsible_user_id?: number | null; expected_at?: string; callback_at?: string;
}
export interface Share {
  id: number; call_id: number; note: string; from: string; to: string; to_user_id: number; created_at: string;
  seen_at: string | null; call_start: string | null; agent_ext: string | null;
}
export interface CallShares { items: Share[]; targets: { id: number; username: string; ext: string | null }[] }
export interface AgentSummary {
  call_id: number;
  analysis: { status: string; summary?: string; topic_label?: string; action_items?: string[] } | null;
}
export interface Settings { followup_start: string | null; followup_kpi_since: string | null }
export interface AppUserRow {
  id: number; username: string; role: string; ext: string | null; active: boolean; locked: boolean;
  created_at: string | null;
}
export interface AdminLogRow { id: number; user: string; action: string; target: string; reason: string | null; at: string }
export interface FollowupKpiBucket {
  forms_due: number; forms_on_time: number; forms_rate: number | null;
  callbacks_due: number; callbacks_on_time: number; callbacks_rate: number | null; came_back: number;
  // follow-up stages
  answered_forms: number; solved_first: number; first_rate: number | null; solved_after: number; open: number;
  rounds_to_solve: number; avg_rounds: number | null; notes_due: number; notes_on_time: number; notes_rate: number | null;
}
export interface FollowupKpi {
  enabled: boolean; active: boolean; start: string | null; since: string | null;
  summary: FollowupKpiBucket; agents: Record<string, FollowupKpiBucket>;
}

/** A refused write: `message` is the server's reason (shown to the user). */
export class Refused extends Error {}

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 401) throw new Unauthorized();
  if (!r.ok) {
    let msg = "";
    try {
      const d = await r.json();
      msg = typeof d.detail === "string" ? d.detail : "";
    } catch { /* not JSON */ }
    throw new Refused(msg || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

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
  const p = new URLSearchParams({ date_from: f.from, date_to: f.to });
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
  followup: (id: number) => get<FollowupView>(`/api/calls/${id}/followup`),
  saveFollowup: (id: number, body: FollowupIn) => send<FollowupView>("PUT", `/api/calls/${id}/followup`, body),
  addNote: (id: number, body: NoteIn) => send<FollowupView>("POST", `/api/calls/${id}/followup/notes`, body),
  followups: (p: { from?: string; to?: string; agent?: string; state?: string }) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(p)) if (v) q.set(k, v);
    return get<FollowupList>(`/api/followups?${q.toString()}`);
  },
  agentSummary: (id: number) => get<AgentSummary>(`/api/calls/${id}/summary`),
  shares: () => get<{ items: Share[]; unseen: number }>("/api/shares"),
  callShares: (id: number) => get<CallShares>(`/api/calls/${id}/shares`),
  share: (id: number, to_user_id: number, note: string) =>
    send<Share>("POST", `/api/calls/${id}/shares`, { to_user_id, note }),
  shareSeen: (id: number) => send<Share>("POST", `/api/shares/${id}/seen`),
  unshare: (id: number) => send<{ ok: true }>("DELETE", `/api/shares/${id}`),
  users: () => get<AppUserRow[]>("/api/admin/users"),
  createUser: (username: string, role: string, ext: string | null) =>
    send<AppUserRow & { password: string }>("POST", "/api/admin/users", { username, role, ext }),
  changeUser: (id: number, patch: { role?: string; ext?: string; clear_ext?: boolean; active?: boolean }) =>
    send<AppUserRow>("PATCH", `/api/admin/users/${id}`, patch),
  resetPassword: (id: number) => send<{ id: number; username: string; password: string }>("POST", `/api/admin/users/${id}/password`),
  settings: () => get<Settings>("/api/admin/settings"),
  saveSettings: (s: Settings) => send<Settings>("PUT", "/api/admin/settings", s),
  adminLog: () => get<AdminLogRow[]>("/api/admin/log"),
  deleteContent: (id: number, reason: string, confirm: number) =>
    send<{ ok: true; deleted: string[] }>("POST", `/api/calls/${id}/delete-content`, { reason, confirm }),
  analysis: (id: number) => get<CallAnalysis>(`/api/calls/${id}/analysis`),
  ratingQueue: () => get<RatingQueue>("/api/rating/queue"),
  ratingCall: (id: number) => get<RatingCall>(`/api/rating/calls/${id}`),
  // prefillShown = the LLM estimate the page displayed when the rating was given (the server checks it)
  async saveRating(id: number, score: number, note: string, prefillShown: number | null): Promise<void> {
    const r = await fetch(`/api/rating/calls/${id}`, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ score, note: note.trim() || null, prefill_shown: prefillShown }),
    });
    if (r.status === 401) throw new Unauthorized();
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  },
};
