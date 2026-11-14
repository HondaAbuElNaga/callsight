import { useEffect, useRef, useState } from "react";
import { api, Unauthorized, type GoldClip, type Me } from "./api";

interface Props { me: Me; onBack: () => void; onLogout: () => void; onExpired: () => void }

const DIR: Record<string, string> = { inbound: "واردة", outbound: "صادرة" };

// Unsaved text survives an expired login / closed tab: kept in this browser tab's sessionStorage until saved.
const draftKey = (id: number) => `gold-draft-${id}`;
const readDraft = (id: number): string | null => { try { return sessionStorage.getItem(draftKey(id)); } catch { return null; } };
const writeDraft = (id: number, t: string | null) => {
  try { if (t === null) sessionStorage.removeItem(draftKey(id)); else sessionStorage.setItem(draftKey(id), t); } catch { /* storage off */ }
};

// Type what you hear in each clip. Changes are saved on "حفظ", on Ctrl+Enter, and before moving to another clip.
export function GoldsetPage({ me, onBack, onLogout, onExpired }: Props) {
  const [clips, setClips] = useState<GoldClip[] | null>(null);
  const [current, setCurrent] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [savedText, setSavedText] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const fail = (e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setMsg({ ok: false, text: "حدث خطأ، لم يتم الحفظ. حاول مرة أخرى." });
  };

  useEffect(() => {
    api.goldset().then((list) => {
      setClips(list);
      const first = list.find((c) => !c.has_text) ?? list[0];
      if (first) open(first.id);
    }).catch(fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function open(id: number) {
    try {
      const c = await api.goldClip(id);
      setCurrent(id);
      const draft = readDraft(id);
      const machine = !c.reference_text && draft === null && c.draft_text ? c.draft_text : null;
      setText(draft ?? c.reference_text ?? machine ?? "");
      setSavedText(c.reference_text ?? "");
      if (draft !== null && draft.trim() !== (c.reference_text ?? "").trim())
        setMsg({ ok: true, text: "تمت استعادة نص غير محفوظ" });
      else if (machine) setMsg({ ok: true, text: "مسودة آلية فيها أخطاء: استمع وصحّح كل كلمة قبل الحفظ" });
      else setMsg(null);
      box.current?.focus();
    } catch (e) { fail(e); }
  }

  async function save(): Promise<boolean> {
    if (current === null) return true;                // nothing open = nothing to save (logout/back must work)
    if (text.trim() === savedText.trim()) return true;
    setBusy(true);
    try {
      const c = await api.saveGoldText(current, text);
      writeDraft(current, null);
      setSavedText(c.reference_text ?? "");
      setClips((list) => list?.map((x) => (x.id === c.id ? { ...x, has_text: c.has_text, typed_at: c.typed_at } : x)) ?? null);
      setMsg({ ok: true, text: "تم الحفظ" });
      return true;
    } catch (e) { fail(e); return false; } finally { setBusy(false); }
  }

  async function go(id: number) {
    if (id === current) return;
    if (await save()) open(id);
  }

  async function saveAndNext() {
    if (!clips || current === null || !(await save())) return;
    const i = clips.findIndex((c) => c.id === current);
    const next = clips.slice(i + 1).find((c) => !c.has_text) ?? clips[i + 1];
    if (next) open(next.id);
  }

  useEffect(() => {                                   // keep the draft + warn before closing the tab
    if (current === null) return;
    const dirtyNow = text.trim() !== savedText.trim();
    writeDraft(current, dirtyNow ? text : null);
    if (!dirtyNow) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [current, text, savedText]);

  const back5 = () => { if (audio.current) audio.current.currentTime = Math.max(0, audio.current.currentTime - 5); };
  const toggle = () => { const a = audio.current; if (a) (a.paused ? a.play() : a.pause()); };

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") { e.preventDefault(); toggle(); }
    else if (e.key === "F2") { e.preventDefault(); back5(); }
    else if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); saveAndNext(); }
  }

  const done = clips?.filter((c) => c.has_text).length ?? 0;
  const total = clips?.length ?? 0;
  const cur = clips?.find((c) => c.id === current);
  const dirty = text.trim() !== savedText.trim();

  return (
    <div className="page" onKeyDown={onKey}>
      <header className="topbar">
        <h1>النصوص المرجعية لاختبار التفريغ</h1>
        <div className="who">
          <button className="link" onClick={async () => { if (await save()) onBack(); }}>سجل المكالمات</button>
          <span className="muted">{me.username}</span>
          <button className="link" onClick={async () => { if (await save()) onLogout(); }}>خروج</button>
        </div>
      </header>

      {clips === null ? <div className="center muted">…</div> : total === 0 ? (
        <div className="center muted">لا توجد مقاطع بعد. يجب اختيار المقاطع أولاً.</div>
      ) : (
        <div className="gold">
          <nav className="gold-list" aria-label="المقاطع">
            <div className="small muted" style={{ padding: "4px 10px" }}>تم {done} من {total}</div>
            <div className="gold-progress"><div style={{ width: `${(done / total) * 100}%` }} /></div>
            {clips.map((c, i) => (
              <button key={c.id} className={c.id === current ? "on" : ""} onClick={() => go(c.id)}>
                <span>مقطع {i + 1}</span>
                <span className={c.has_text ? "done" : "muted"}>{c.has_text ? "✓" : "—"}</span>
              </button>
            ))}
          </nav>

          {cur && (
            <section className="gold-editor">
              <h2>مقطع {clips.indexOf(cur) + 1} <span className="muted small">
                · تحويلة {cur.agent_ext} · {DIR[cur.direction ?? ""] ?? ""} · {Math.round(cur.seconds)} ث</span></h2>
              <audio ref={audio} key={cur.id} controls preload="metadata" src={api.goldAudioUrl(cur.id)} />
              <div className="gold-row">
                <button onClick={toggle}>تشغيل / إيقاف (Esc)</button>
                <button onClick={back5}>رجوع 5 ثوانٍ (F2)</button>
              </div>
              <ul className="gold-help">
                <li>اكتب كل كلمة تسمعها كما قيلت باللهجة، بدون تصحيح أو تلخيص.</li>
                <li>كلام الموظف والعميل معاً، بالترتيب. الكلمة غير الواضحة اتركها ولا تكتب مكانها شيئاً.</li>
                <li>الأرقام اكتبها بالأرقام (مثلاً 25 أو 0551234567).</li>
              </ul>
              <textarea
                ref={box}
                dir="rtl"
                lang="ar"
                value={text}
                maxLength={10000}
                onChange={(e) => { setText(e.target.value); setMsg(null); }}
                placeholder="اكتب هنا ما تسمعه…"
                aria-label="النص المرجعي"
              />
              <div className="gold-row">
                <button className="primary" disabled={busy} onClick={saveAndNext}>حفظ والتالي (Ctrl+Enter)</button>
                <button disabled={busy || !dirty} onClick={save}>حفظ</button>
                {msg && <span className={msg.ok ? "saved" : "error"}>{msg.text}</span>}
                {!msg && dirty && <span className="muted small">تغييرات غير محفوظة</span>}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
