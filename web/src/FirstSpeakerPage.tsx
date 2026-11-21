import { useEffect, useRef, useState } from "react";
import { api, Unauthorized, type FirstSpeakerList, type Me } from "./api";

interface Props { me: Me; onBack: () => void; onLogout: () => void; onExpired: () => void }
type Who = "agent" | "customer" | "skip";

const DIR: Record<string, string> = { inbound: "واردة", outbound: "صادرة", internal: "داخلية" };
const WHO: Record<string, string> = { agent: "الموظف", customer: "العميل", unsure: "غير متأكد", not_split: "لم يُقسَّم بعد" };

// First-speaker check (diarization acceptance): the admin listens to the START of each of the 30 gold calls and
// marks who speaks first. The system's answer is shown only after every row is answered (no steering).
export function FirstSpeakerPage({ me, onBack, onLogout, onExpired }: Props) {
  const [data, setData] = useState<FirstSpeakerList | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const fail = (e: unknown, text: string) => {
    if (e instanceof Unauthorized) onExpired();
    else setErr(text);
  };
  const saves = useRef(0);                           // a load started before a later save is dropped (stale)
  const [version, setVersion] = useState(0);         // bumps on each save: re-fetches the score after a dropped load
  const load = () => {
    const at = saves.current;
    api.firstSpeaker().then((d) => { if (at === saves.current) setData(d); })
      .catch((e) => fail(e, "تعذر تحميل الصفحة. حدّث الصفحة."));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  async function mark(id: number, who: Who) {
    setErr(null);
    try {
      const saved = await api.saveFirstSpeaker(id, who);
      saves.current += 1;
      setVersion((v) => v + 1);
      setData((d) => {                               // update this row from its own save (no reload race)
        if (!d) return d;
        const rows = d.rows.map((r) => (r.id === saved.id ? { ...r, first_speaker: saved.first_speaker } : r));
        const answered = rows.filter((r) => r.first_speaker !== null || !r.has_recording).length;
        return { ...d, rows, answered, score: null };   // any change: the score is fetched again
      });
    } catch (e) { fail(e, "حدث خطأ، لم يتم الحفظ. حاول مرة أخرى."); }
  }

  // every row answered but no score yet (or an answer changed): fetch the score
  const needScore = !!data && data.total > 0 && data.answered === data.total && data.score === null;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (needScore) load(); }, [needScore, version]);

  const s = data?.score;
  const waiting = !!s && s.not_split > 0;
  const pass = !!s && !waiting && s.right >= s.target;
  return (
    <div className="page">
      <header className="topbar">
        <h1>من يتكلم أولاً؟ (اختبار فصل المتحدثين)</h1>
        <div className="who">
          <button className="link" onClick={onBack}>سجل المكالمات</button>
          <span className="muted">{me.username}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>

      {err && <div className="error">{err}</div>}
      {data === null ? <div className="center muted">…</div> : data.total === 0 ? (
        <div className="center muted">لا توجد مكالمات في المجموعة المرجعية بعد.</div>
      ) : (
        <section className="fs">
          <ul className="gold-help">
            <li>شغّل بداية كل مكالمة واسمع حتى أول كلمة مفهومة (تجاهل الرنين والرسالة الآلية).</li>
            <li>اختر من تكلم أولاً: الموظف أو العميل. إذا لم تستطع التمييز اختر «لا أعرف».</li>
            <li>يُحفظ الاختيار فوراً ويمكن تغييره. تظهر النتيجة بعد الإجابة على الكل ({data.answered} من {data.total}).</li>
          </ul>
          <div className="gold-progress"><div style={{ width: `${(data.answered / data.total) * 100}%` }} /></div>

          {s && (
            <div className={waiting ? "muted fs-score" : pass ? "saved fs-score" : "error fs-score"}>
              {waiting && <div>بعض المكالمات لم يُقسَّم كلامها بعد ({s.not_split}). انتظر حتى يكتمل ثم حدّث الصفحة.</div>}
              صح {s.right} من {s.scored} (المطلوب {s.target} على الأقل)
              {" · "}خطأ {s.wrong} · غير متأكد {s.unsure} · لم يُقسَّم بعد {s.not_split}
              {s.skipped > 0 && <> · «لا أعرف» أو بدون تسجيل {s.skipped} (لا تُحسب)</>}
              <div className="small">«غير متأكد» = النظام لم يحدد الموظف، فلا تُحسب صحيحة.</div>
            </div>
          )}

          <table className="fs-table">
            <thead><tr><th>#</th><th>المكالمة</th><th>التسجيل</th><th>من تكلم أولاً؟</th>{s && <th>النظام</th>}</tr></thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.id}>
                  <td>{r.n}</td>
                  <td className="small">تحويلة {r.agent_ext ?? "—"}{DIR[r.direction ?? ""] ? ` · ${DIR[r.direction ?? ""]}` : ""}</td>
                  <td>{r.has_recording
                    ? <audio controls preload="none" src={api.recordingUrl(r.call_id)} />
                    : <span className="muted small">لا يوجد تسجيل (لا تُحسب)</span>}</td>
                  <td className="fs-pick">
                    {r.has_recording && (["agent", "customer", "skip"] as Who[]).map((w) => (
                      <button key={w} className={r.first_speaker === w ? "primary" : ""} onClick={() => mark(r.id, w)}>
                        {w === "skip" ? "لا أعرف" : WHO[w]}
                      </button>
                    ))}
                  </td>
                  {s && <td className={r.system === undefined ? "muted" : r.system === r.first_speaker ? "done" : "bad"}>
                    {r.system === undefined ? "—" : WHO[r.system]}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
