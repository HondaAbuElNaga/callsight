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

export class Unauthorized extends Error {}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 401) throw new Unauthorized();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json() as Promise<T>;
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
    if (r.status === 429) return { ok: false, message: "تم إيقاف الحساب مؤقتاً بعد محاولات خاطئة. حاول بعد 15 دقيقة." };
    return { ok: false, message: "اسم المستخدم أو كلمة المرور غير صحيحة" };
  },
  logout: () => fetch("/api/logout", { method: "POST", credentials: "same-origin" }),
};
