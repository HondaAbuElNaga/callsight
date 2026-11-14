import { useCallback, useEffect, useRef, useState } from "react";
import { api, Unauthorized, type Me, type RatingCall, type RatingQueue } from "./api";
import { AnalysisSummary, EstimateLine } from "./AnalysisPanel";
import { TranscriptPanel } from "./TranscriptPanel";
import { duration, formatDateTime } from "./format";

interface Props { me: Me; onBack: () => void; onLogout: () => void; onExpired: () => void }

const SCORES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

// "تقييم المكالمات": supervisors rate the CUSTOMER's satisfaction 1-10. Test-set (blind) calls come first and never
// show the LLM estimate; the other calls show it as a starting point that the supervisor corrects.
export function RatingPage({ me, onBack, onLogout, onExpired }: Props) {
  const [queue, setQueue] = useState<RatingQueue | null>(null);
  const [call, setCall] = useState<RatingCall | null>(null);
  const [score, setScore] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [pos, setPos] = useState<number | null>(null);
  const audio = useRef<HTMLAudioElement>(null);

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setMsg({ ok: false, text: "حدث خطأ. حاول مرة أخرى." });
  }, [onExpired]);

  const open = useCallback(async (id: number) => {
    try {
      const c = await api.ratingCall(id);
      setCall(c);
      setScore(c.my_label?.score ?? c.estimate?.score ?? null);   // pre-fill: only non-blind calls have one
      setNote(c.my_label?.note ?? "");
      setPos(null);
    } catch (e) { fail(e); }
  }, [fail]);

  const reload = useCallback(async (next: boolean) => {
    try {
      const q = await api.ratingQueue();
      setQueue(q);
      if (next) {
        if (q.items[0]) await open(q.items[0].call_id);
        else setCall(null);
      }
    } catch (e) { fail(e); }
  }, [fail, open]);

  useEffect(() => { reload(true); }, [reload]);

  async function save() {
    if (!call || score === null) return;
    setBusy(true);
    try {
      await api.saveRating(call.call_id, score, note);
      setMsg({ ok: true, text: `تم حفظ التقييم ${score}/10` });
      await reload(true);
    } catch (e) { fail(e); } finally { setBusy(false); }
  }

  const p = queue?.progress;
  return (
    <div className="page">
      <header className="topbar">
        <h1>تقييم المكالمات</h1>
        <div className="who">
          <button className="link" onClick={onBack}>سجل المكالمات</button>
          <span className="muted">{me.username}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>

      <div className="rating-intro">
        <p>
          قيّم <b>رضا العميل</b> في المكالمة من 1 (غاضب جداً) إلى 10 (راضٍ جداً) — <b>وليس أداء الموظف</b>.
          التقييمات تُستخدم لتدريب نموذج الرضا ولا تدخل في مؤشرات الموظفين.
        </p>
        {p && (
          <p className="progress-line">
            <span>التقييمات: <b className="num">{p.labels}</b> / {p.labels_target} مكالمة</span>
            <span>مجموعة الاختبار: <b className="num">{p.blind_rated}</b> / {p.blind_target}
              {!p.blind_frozen && <span className="muted"> (لم تُثبَّت بعد — لا تظهر تقديرات آلية حتى تثبيتها)</span>}
            </span>
          </p>
        )}
      </div>

      {msg && <div className={msg.ok ? "ok-msg" : "error"}>{msg.text}</div>}

      <div className="rating-layout">
        <aside className="rating-queue" aria-label="مكالمات للتقييم">
          <h3>بانتظار تقييمك</h3>
          {queue && queue.items.length === 0 && <p className="muted small">لا توجد مكالمات مفرّغة بانتظار تقييمك.</p>}
          <ol>
            {queue?.items.map((it) => (
              <li key={it.call_id}>
                <button type="button" className={`queue-item ${call?.call_id === it.call_id ? "on" : ""}`}
                  onClick={() => open(it.call_id)}>
                  <span>{formatDateTime(it.start_time)}</span>
                  <span className="muted small num">{duration(it.talk_seconds)}</span>
                  {it.blind && <span className="tag">اختبار</span>}
                </button>
              </li>
            ))}
          </ol>
        </aside>

        {call && (
          <main className="rating-call">
            <div className="rating-head">
              <h2>مكالمة {formatDateTime(call.start_time)}</h2>
              {call.blind && <span className="badge neutral">مجموعة الاختبار: بدون تقدير آلي</span>}
            </div>
            {call.has_recording && (
              <audio ref={audio} controls preload="metadata" src={api.recordingUrl(call.call_id)}
                onTimeUpdate={(e) => setPos(e.currentTarget.currentTime)} />
            )}
            {call.analysis && <section className="analysis"><h3>ملخص آلي</h3><AnalysisSummary a={call.analysis} /></section>}
            {call.estimate && <EstimateLine e={call.estimate} />}

            <fieldset className="score-box">
              <legend>رضا العميل</legend>
              <div className="scores" role="radiogroup" aria-label="رضا العميل من 1 إلى 10">
                {SCORES.map((n) => (
                  <button key={n} type="button" role="radio" aria-checked={score === n}
                    className={`score ${score === n ? "on" : ""}`} onClick={() => setScore(n)}>{n}</button>
                ))}
              </div>
              <label className="note">ملاحظة (اختياري، بدون أرقام أو بيانات شخصية)
                <textarea value={note} maxLength={500} rows={2} onChange={(e) => setNote(e.target.value)} />
              </label>
              <button className="primary" disabled={busy || score === null} onClick={save}>حفظ والانتقال للتالية</button>
            </fieldset>

            <TranscriptPanel key={call.call_id} callId={call.call_id} canSeek={call.has_recording} time={pos}
              onSeek={(t) => { const a = audio.current; if (a) { a.currentTime = t; a.play().catch(() => undefined); } }}
              onExpired={onExpired} />
          </main>
        )}
      </div>
    </div>
  );
}
