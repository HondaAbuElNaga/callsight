import { useEffect, useState } from "react";
import { api, Unauthorized, type FirstSpeakerList, type Me } from "./api";

interface Props { me: Me; onBack: () => void; onLogout: () => void; onExpired: () => void }

const DIR: Record<string, string> = { inbound: "واردة", outbound: "صادرة" };
const WHO: Record<string, string> = { agent: "الموظف", customer: "العميل", unsure: "غير متأكد", not_split: "لم يُقسَّم بعد" };

// First-speaker check (diarization acceptance): the admin listens to the START of each of the 30 gold calls and
// marks who speaks first. The system's answer is shown only after every row is answered.
export function FirstSpeakerPage({ me, onBack, onLogout, onExpired }: Props) {
  const [data, setData] = useState<FirstSpeakerList | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const fail = (e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setErr("حدث خطأ، لم يتم الحفظ. حاول مرة أخرى.");
  };
  const load = () => api.firstSpeaker().then(setData).catch(fail);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  async function mark(id: number, who: "agent" | "customer") {
    setErr(null);
    try { await api.saveFirstSpeaker(id, who); await load(); } catch (e) { fail(e); }
  }

  const s = data?.score;
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
            <li>شغّل بداية كل مكالمة واسمع أول 10 ثوانٍ فقط.</li>
            <li>اختر من تكلم أولاً: الموظف أو العميل. يُحفظ الاختيار فوراً، ويمكن تغييره.</li>
            <li>تظهر النتيجة بعد الإجابة على كل المكالمات ({data.answered} من {data.total}).</li>
          </ul>
          <div className="gold-progress"><div style={{ width: `${(data.answered / data.total) * 100}%` }} /></div>

          {s && (
            <div className={s.right >= Math.ceil(data.total * 0.9) ? "saved fs-score" : "error fs-score"}>
              صح {s.right} من {data.total} (المطلوب {Math.ceil(data.total * 0.9)} على الأقل)
              {" · "}خطأ {s.wrong} · غير متأكد {s.unsure} · لم يُقسَّم بعد {s.not_split}
            </div>
          )}

          <table className="fs-table">
            <thead><tr><th>#</th><th>المكالمة</th><th>التسجيل</th><th>من تكلم أولاً؟</th>{s && <th>النظام</th>}</tr></thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.id}>
                  <td>{r.n}</td>
                  <td className="small">تحويلة {r.agent_ext ?? "—"} · {DIR[r.direction ?? ""] ?? ""}</td>
                  <td>{r.has_recording
                    ? <audio controls preload="none" src={api.recordingUrl(r.call_id)} />
                    : <span className="muted small">لا يوجد تسجيل</span>}</td>
                  <td className="fs-pick">
                    <button className={r.first_speaker === "agent" ? "primary" : ""} onClick={() => mark(r.id, "agent")}>الموظف</button>
                    <button className={r.first_speaker === "customer" ? "primary" : ""} onClick={() => mark(r.id, "customer")}>العميل</button>
                  </td>
                  {s && <td className={r.system === r.first_speaker ? "done" : "bad"}>{WHO[r.system ?? ""] ?? ""}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
