import type { EarlyTries, HangupBy, Leg, Status } from "./api";

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

// ---------- outbound ring time (spec 2026-11-15-ring-time) ----------
export const EARLY_HANGUP_SECONDS = 30;   // same as callsight/followups.py
const LINE_CLOSED_SECONDS = 55;           // the network closes an unanswered line after ~62 s
/** "رن 62 ث · انقطع الخط" for an outbound call that was not answered ("" when it was answered). */
export function ringText(ring: number | null | undefined, by: HangupBy | null | undefined, answered: boolean): string {
  if (answered || ring === null || ring === undefined) return "";
  const who = by === "busy" ? "مشغول"
    : by === "customer" ? (ring >= LINE_CLOSED_SECONDS ? "انقطع الخط" : "العميل رفض")
    : by === "agent" ? "الموظف قفل" : "";
  return `رن ${ring} ث${who ? " · " + who : ""}`;
}
export function earlyText(e: EarlyTries): string {
  return `محاولة لم تُحسب: رن ${e.last_ring_seconds} ث والموظف قفل${e.count > 1 ? ` (×${e.count})` : ""}`;
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
// follow-up stages: the step of one call and what the agent must do next
export const STEP_LABEL: Record<string, string> = {
  required: "مطلوب", overdue: "لم يُعبّأ في وقته", no_followup: "لا تحتاج متابعة", solved_first: "حُلّت من أول مكالمة",
  waiting_callback: "بانتظار معاودة الاتصال", callback_missed: "معاودة فائتة", note_needed: "تم الاتصال – أضف ملاحظة",
  solved_after: "حُلّت بعد المتابعة", no_number: "بلا رقم للمعاودة",
  waiting_solution: "بانتظار الحل", solution_overdue: "فات موعد الحل",
  forwarded: "محوّلة – بانتظار الحل", forward_overdue: "محوّلة – انتهت الـ24 ساعة", forward_failed: "لم تُحوَّل – اختر من جديد",
  forward_solved: "حُلّت بالتحويل ✓", forward_unconfirmed: "أغلقها مشرف الفرع (غير مؤكد)",
};
// D2: why a supervisor's "solved" is not confirmed (the last call to the customer decides)
export const PROOF_REASON: Record<string, string> = {
  no_ext: "لا توجد تحويلة للمشرف", no_call: "لا توجد مكالمة من المشرف للعميل", not_answered: "لم يرد العميل",
  short: "المكالمة 60 ثانية أو أقل", waiting_score: "بانتظار تقييم الرضا", low_score: "الرضا 7 أو أقل",
};
export const STEP_CLASS: Record<string, string> = {
  required: "off", overdue: "bad", no_followup: "ok", solved_first: "ok", waiting_callback: "off",
  callback_missed: "bad", note_needed: "warn", solved_after: "ok", no_number: "off",
  waiting_solution: "off", solution_overdue: "bad", forwarded: "off", forward_overdue: "bad", forward_failed: "bad", forward_solved: "ok",
  forward_unconfirmed: "warn",
};
export const STEP_NEXT: Record<string, string> = {
  required: "عبّئ نموذج المتابعة", overdue: "عبّئ نموذج المتابعة (تأخر)", note_needed: "اكتب ما حدث في مكالمة المعاودة",
  waiting_callback: "اتصل بالعميل في الموعد", callback_missed: "اتصل بالعميل الآن (فات الموعد)",
  waiting_solution: "تابع الحل واكتب تحديثًا عند الحل (خلال يومين)", solution_overdue: "فات موعد الحل: اكتب تحديثًا الآن",
  forwarded: "المسؤول في نظام خدمة العملاء يحلّها ويغلقها؛ اكتب تحديثًا إن اتصل العميل",
  forward_overdue: "انتهت مهلة الـ24 ساعة: تنتقل تلقائيًا للمستوى التالي",
  forward_failed: "لم تصل المشكلة: عدّل النموذج واختر من جديد",
  forward_unconfirmed: "أغلقها مشرف الفرع دون مكالمة جيدة للعميل: تنتقل للإدارة بعد الـ24 ساعة إن لم يتصل",

};
// priority of the first form (spec 2026-11-16-priority-no-followup): same codes as callsight/followups.py
export type Priority = "high" | "medium" | "low";
export const PRIORITY_LABEL: Record<Priority, string> = { high: "عالية", medium: "متوسطة", low: "منخفضة" };
export const PRIORITY_CLASS: Record<Priority, string> = { high: "bad", medium: "warn", low: "off" };
export const PRIORITIES: Priority[] = ["high", "medium", "low"];
export const ROLE_LABEL: Record<string, string> = { admin: "مدير النظام", supervisor: "مشرف", agent: "موظف" };
export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(0)}%`);

// the forward chain (spec "Part 4 change")
export const HOP_LEVEL: Record<number, string> = { 1: "الموظف", 2: "مشرف الفرع", 3: "الإدارة" };
export const HOP_RESULT: Record<string, string> = {
  pending: "تُنشأ نسخة التذكرة", open: "عنده الآن", solved: "حلّها ✓", passed_on: "لم تُحل خلال 24 ساعة – انتقلت",
  closed_by_other: "حلّها غيره", failed: "تعذّر الإرسال",
};
