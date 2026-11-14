import { useCallback, useEffect, useState } from "react";
import { api, Unauthorized, type Health, type HealthItem, type Me } from "./api";
import { formatDateTime } from "./format";

interface Props { me: Me; onBack: () => void; onLogout: () => void; onExpired: () => void }

const STATE_LABEL: Record<HealthItem["state"], string> = { ok: "سليم", warn: "تنبيه", bad: "خلل", off: "لا يعمل" };

const DISK: Record<string, string> = {
  system: "قرص النظام / Docker (C:)",
  audio: "قرص التسجيلات (D:)",
  backup: "مجلد النسخ الاحتياطي",
};
const QUEUE: Record<string, string> = {
  ingest: "سحب المكالمات", asr: "تحويل الكلام إلى نص", nlp: "تحليل المكالمات", reports: "التقارير", maint: "الصيانة (النسخ الاحتياطي)",
};
const JOB: Record<string, string> = {
  ingest: "سحب المكالمات (كل دقيقتين)", recordings: "تنزيل التسجيلات (كل 5 دقائق)",
  reconcile: "المطابقة الليلية (2:00)", backup: "النسخ الاحتياطي الليلي (1:30)",
};

const when = (iso: string | null | undefined) => (iso ? formatDateTime(iso) : "لم يحدث بعد");

function describe(i: HealthItem): [string, string] {
  const [group, name] = i.key.split(".", 2) as [string, string | undefined];
  const v = i as unknown as Record<string, unknown>;
  switch (group) {
    case "disk":
      return [DISK[name ?? ""] ?? name ?? "", i.state === "off" ? "غير متاح" : `متبقٍ ${v.free_gb} GB من ${v.total_gb} GB (${v.free_pct}%)`];
    case "db":
      return ["حجم قاعدة البيانات", `${v.gb} GB`];
    case "queues":
      return ["الطوابير (Redis)", "لا يمكن الوصول إلى Redis"];
    case "queue":
      return [`طابور: ${QUEUE[name ?? ""] ?? name}`, `${v.waiting} مهمة تنتظر`];
    case "job":
      return [JOB[name ?? ""] ?? name ?? "", `آخر نجاح: ${when(v.last_ok as string | null)}${v.last_error ? ` · آخر فشل: ${when(v.last_error as string)}` : ""}`];
    case "workers":
      return ["العمّال (Celery)", `${v.answering} يستجيب`];
    case "gpu":
      return ["كرت الشاشة (عامل التفريغ)", v.running ? `${v.used_mb} / ${v.total_mb} MB مستخدم` : "عامل التفريغ لا يعمل"];
    case "backup":
      if (name === "age")
        return ["آخر نسخة احتياطية ناجحة", v.hours === null ? "لا توجد نسخة بعد" :
          `قبل ${v.hours} ساعة${v.last_failed ? " · آخر محاولة فشلت" : ""} · اختبار الاسترجاع: ${v.verified_ok === true ? "ناجح" : v.verified_ok === false ? "فشل" : "لم يُجرَ"}`];
      return ["مكان النسخ الاحتياطي", v.offsite ? "قرص ثانٍ مشفّر / NAS" :
        "على نفس القرص وغير مشفّرة: تحمي من الأخطاء فقط، لا من تلف القرص"];
    case "reconcile": {
      const runs = (v.runs as { day: string; ok: boolean }[]) ?? [];
      return ["آخر 7 مطابقات ليلية", runs.length ? runs.map((r) => `${r.day.slice(5)} ${r.ok ? "✓" : "✗"}`).join("  ") : "لا توجد مطابقات بعد"];
    }
  }
  return [i.key, ""];
}

// Admin only: is everything running? Refreshes every minute.
export function HealthPage({ me, onBack, onLogout, onExpired }: Props) {
  const [data, setData] = useState<Health | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.health().then((h) => { setData(h); setError(""); }).catch((e) => {
      if (e instanceof Unauthorized) onExpired();
      else setError("تعذر تحميل حالة النظام.");
    });
  }, [onExpired]);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="page">
      <header className="topbar">
        <h1>حالة النظام</h1>
        <div className="who">
          <button className="link" onClick={onBack}>سجل المكالمات</button>
          <span className="muted">{me.username}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>
      {error && <div className="error">{error}</div>}
      {data && (
        <>
          <p className={data.ok ? "health-sum ok" : "health-sum bad"}>
            {data.ok ? "كل شيء يعمل." : `يوجد ${data.problems.length} خلل يحتاج متابعة.`}
            <span className="muted small"> · آخر فحص {formatDateTime(data.checked_at)}</span>
          </p>
          <div className="table-wrap panel">
            <table className="health">
              <tbody>
                {data.items.map((i) => {
                  const [label, detail] = describe(i);
                  return (
                    <tr key={i.key}>
                      <td><span className={`state ${i.state}`}>{STATE_LABEL[i.state]}</span></td>
                      <td>{label}</td>
                      <td className="muted">{detail}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            أطوال الطوابير لا تشمل المهام التي استلمها عامل ولم ينهها بعد. لا توجد تنبيهات بالبريد: راجع هذه الصفحة.
          </p>
        </>
      )}
    </div>
  );
}
