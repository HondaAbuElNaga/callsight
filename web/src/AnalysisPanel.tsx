import { useEffect, useRef, useState } from "react";
import { api, NotFound, Unauthorized, type AnalysisContent, type CallAnalysis, type Estimate } from "./api";
import { mmss } from "./format";

// Call analysis block in the call detail (admin + supervisor only). Everything here is rendered as plain text.
// The first LLM estimate is never shown for blind-set calls (until you rated them) or before the blind set exists.

const KIND_LABEL: Record<string, string> = { bad_words: "ألفاظ", escalation: "تصعيد", competitor: "منافس" };
const STATUS_TEXT: Record<string, string> = {
  queued: "المكالمة في انتظار التحليل…",
  running: "جارٍ تحليل المكالمة الآن…",
  error: "تعذر تحليل هذه المكالمة.",
  skipped: "لم تُحلَّل هذه المكالمة (معظمها صمت أو غير واضح).",
};

export function AnalysisSummary({ a }: { a: AnalysisContent }) {
  if (a.status !== "done") return <p className="muted small">{STATUS_TEXT[a.status]}</p>;
  return (
    <>
      <p className="analysis-summary">{a.summary}</p>
      <div className="analysis-meta">
        <span className="badge neutral">{a.topic_label}</span>
        {a.escalation && <span className="badge bad">طلب تصعيد</span>}
        {a.trimmed && <span className="muted small">(مكالمة طويلة: لُخّص أولها وآخرها)</span>}
      </div>
      {a.action_items && a.action_items.length > 0 && (
        <ul className="action-items">{a.action_items.map((x, i) => <li key={i}>{x}</li>)}</ul>
      )}
      <p className="muted small">ملخص آلي من نموذج لغوي محلي ({a.model}) — قد يحتوي أخطاء.</p>
    </>
  );
}

export function EstimateLine({ e }: { e: Estimate }) {
  return (
    <div className="estimate">
      <b>تقدير أولي: {e.score}/10 (غير معتمد)</b>
      <div className="small">{e.reason}</div>
      <div className="muted small">{e.label}</div>
    </div>
  );
}

export function AnalysisPanel({ callId, onSeek, onExpired }:
  { callId: number; onSeek?: (t: number) => void; onExpired: () => void }) {
  const [d, setD] = useState<CallAnalysis | null>(null);
  const [state, setState] = useState<"loading" | "none" | "failed" | "ok">("loading");
  const expired = useRef(onExpired);
  expired.current = onExpired;

  useEffect(() => {
    let alive = true;
    setState("loading");
    api.analysis(callId)
      .then((x) => { if (alive) { setD(x); setState("ok"); } })
      .catch((e) => {
        if (!alive) return;
        if (e instanceof Unauthorized) expired.current();
        else setState(e instanceof NotFound ? "none" : "failed");
      });
    return () => { alive = false; };
  }, [callId]);

  if (state === "loading") return <section className="analysis"><h3>تحليل المكالمة</h3><p className="muted small">…</p></section>;
  if (state !== "ok" || !d) return null;
  const empty = !d.analysis && d.flags.length === 0 && !d.silence;
  return (
    <section className="analysis" aria-label="تحليل المكالمة">
      <h3>تحليل المكالمة</h3>
      {empty && <p className="muted small">لا يوجد تحليل لهذه المكالمة بعد.</p>}
      {d.analysis && <AnalysisSummary a={d.analysis} />}
      {d.estimate && <EstimateLine e={d.estimate} />}
      {d.estimate_hidden === "blind" && (
        <p className="muted small">التقدير الآلي مخفي: هذه المكالمة من مجموعة الاختبار، قيّمها أولاً في صفحة التقييم.</p>
      )}
      {d.flags.length > 0 && (
        <>
          <h4>تنبيهات تحتاج استماع</h4>
          <ul className="flags">
            {d.flags.map((f, i) => (
              <li key={i}>
                <button type="button" className="link" disabled={!onSeek} onClick={() => onSeek?.(f.start)}>
                  <span dir="ltr" className="num">{mmss(f.start)}</span>
                </button>
                {" "}<span className="badge warn">{KIND_LABEL[f.kind] ?? f.kind}</span> {f.phrase}
              </li>
            ))}
          </ul>
          <p className="muted small">مطابقة كلمات آلية: قد تكون خاطئة (نفي، لهجة، خطأ تفريغ). لا تُحسب في المؤشرات.</p>
        </>
      )}
      {d.silence && (
        <p className="small">
          الصمت: <b className="num">{Math.round(d.silence.share * 100)}%</b> من المكالمة · أطول صمت{" "}
          <b className="num">{Math.round(d.silence.longest)}</b> ث · صمت أطول من{" "}
          <span className="num">{d.silence.long_s ?? 10}</span> ث:{" "}
          <b className="num">{d.silence.long_count}</b>
          <span className="muted"> (موسيقى الانتظار لا تُحسب صمتاً)</span>
        </p>
      )}
    </section>
  );
}
