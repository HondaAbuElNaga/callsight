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

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "–";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
