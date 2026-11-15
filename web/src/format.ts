import type { Leg, Status } from "./api";

export const STATUS_LABEL: Record<Status, string> = {
  answered: "تم الرد",
  abandoned_queue: "أغلق أثناء الانتظار",
  abandoned_ivr: "أغلق في القائمة",
  no_answer: "لم يتم الرد",
  busy: "مشغول",
  voicemail: "بريد صوتي",
};

export const STATUS_ORDER: Status[] = ["answered", "abandoned_queue", "no_answer", "busy", "voicemail"];

export const DIRECTION_LABEL: Record<string, string> = { inbound: "وارد", outbound: "صادر", internal: "داخلي" };

export const LEG_LABEL: Record<Leg["kind"], string> = {
  ivr: "القائمة الصوتية",
  queue: "الطابور",
  extension: "تحويلة",
  external: "رقم خارجي",
  voicemail: "بريد صوتي",
};

// Gregorian calendar, Latin digits, Riyadh time (the PC's own time zone doesn't matter).
const dt = new Intl.DateTimeFormat("ar-u-ca-gregory-nu-latn", {
  timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
});
const dOnly = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit" });

export const formatDateTime = (iso: string) => dt.format(new Date(iso));
export const riyadhToday = () => dOnly.format(new Date()); // YYYY-MM-DD

// Transcript line time, Western digits (shown inside dir="ltr").
export function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "–";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

// ---------- agent + supervisor work ----------
// <input type="datetime-local"> holds Riyadh wall time (UTC+3, no daylight saving) without a zone.
const inputFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  hourCycle: "h23",
});
export function toRiyadhInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const p = Object.fromEntries(inputFmt.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}
export const fromRiyadhInput = (v: string): string => `${v}:00+03:00`;

export const CALLBACK_LABEL: Record<string, string> = {
  waiting: "بانتظار الاتصال", on_time: "في الوقت", late: "متأخر", missed: "فائت", no_number: "بلا رقم",
};
export const FORM_LABEL: Record<string, string> = {
  missing: "مطلوب", overdue: "لم يُعبّأ في وقته", on_time: "في الوقت", late: "عُبّئ متأخرًا",
};
export const ROLE_LABEL: Record<string, string> = { admin: "مدير النظام", supervisor: "مشرف", agent: "موظف" };
export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(0)}%`);
