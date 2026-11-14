import { useEffect, useRef, useState } from "react";
import { api, NotFound, Unauthorized, type Transcript } from "./api";
import { mmss } from "./format";

interface Props {
  callId: number;
  canSeek: boolean;              // the call has a recording
  time: number | null;           // playback position when the player plays this call
  onSeek: (seconds: number) => void;
  onExpired: () => void;
}

const POLL_MS = 15_000;          // while queued/running (status checks write no audit row)
const USER_PAUSE_MS = 5_000;     // auto-scroll waits this long after the user scrolls or uses the keyboard

const STATUS_TEXT: Record<string, string> = {
  queued: "المكالمة في انتظار التفريغ…",
  running: "جارٍ تفريغ المكالمة الآن…",
  error: "تعذر تفريغ هذه المكالمة.",
  skipped: "هذه المكالمة لا تُفرَّغ (قصيرة جداً أو طويلة جداً أو بلا تسجيل).",
};

export function TranscriptPanel({ callId, canSeek, time, onSeek, onExpired }: Props) {
  const [data, setData] = useState<Transcript | null>(null);
  const [state, setState] = useState<"loading" | "none" | "failed" | "ok">("loading");
  const listRef = useRef<HTMLOListElement>(null);
  const userAt = useRef(0);          // last time the user scrolled/typed inside the list
  const ownScroll = useRef(false);   // the next scroll event is ours, not the user's
  const expired = useRef(onExpired);
  expired.current = onExpired;

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      api.transcript(callId)
        .then((t) => {
          if (!alive) return;
          setData(t);
          setState("ok");
          if (t.status === "queued" || t.status === "running") timer = setTimeout(load, POLL_MS);
        })
        .catch((e) => {
          if (!alive) return;
          if (e instanceof Unauthorized) expired.current();
          else setState(e instanceof NotFound ? "none" : "failed");
        });
    };
    setState("loading");
    setData(null);
    load();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [callId]);

  const segs = data?.status === "done" ? data.segments : [];
  let active = -1;
  if (time !== null) {
    for (let i = 0; i < segs.length; i++) if (segs[i].start <= time) active = i; else break;
    if (active >= 0 && time >= segs[active].end) active = -1;
  }

  useEffect(() => {   // keep the line being played in view, unless the user is reading elsewhere
    const list = listRef.current;
    if (active < 0 || !list || Date.now() - userAt.current < USER_PAUSE_MS) return;
    const el = list.children[active] as HTMLElement | undefined;
    if (!el) return;
    const top = el.offsetTop;            // .segments is position: relative
    if (top < list.scrollTop || top + el.offsetHeight > list.scrollTop + list.clientHeight) {
      ownScroll.current = true;
      list.scrollTop = Math.max(0, top - list.clientHeight / 3);
    }
  }, [active]);

  const userBusy = () => { userAt.current = Date.now(); };

  return (
    <section className="transcript" aria-label="نص المكالمة">
      <div className="transcript-head">
        <h3>نص المكالمة</h3>
        {data?.status === "done" && (
          <a className="button small" href={api.transcriptTxtUrl(callId)} download>تنزيل النص (.txt)</a>
        )}
      </div>
      {state === "loading" && <p className="muted small">جارٍ تحميل النص…</p>}
      {state === "none" && <p className="muted small">لا يوجد تفريغ لهذه المكالمة.</p>}
      {state === "failed" && <p className="muted small">تعذر تحميل النص. أعد المحاولة لاحقاً.</p>}
      {data && data.status !== "done" && <p className="muted small">{STATUS_TEXT[data.status]}</p>}
      {data?.status === "done" && (
        <>
          <p className="muted small">
            تفريغ آلي وقد يحتوي أخطاء. كل سطر يغطي حتى 30 ثانية من الصوت
            {canSeek ? "؛ اضغط على سطر لسماعه من بدايته." : "."}
          </p>
          {segs.length === 0 && <p className="muted small">لا يوجد كلام مفهوم في هذه المكالمة.</p>}
          <ol className="segments" ref={listRef}
            onScroll={() => { if (ownScroll.current) ownScroll.current = false; else userBusy(); }}
            onWheel={userBusy} onTouchMove={userBusy} onKeyDown={userBusy} onFocus={userBusy}>
            {segs.map((s, i) => (
              <li key={s.seq}>
                <button type="button" className={`seg ${i === active ? "on" : ""}`} disabled={!canSeek}
                  aria-current={i === active ? "true" : undefined} onClick={() => onSeek(s.start)}>
                  <span dir="ltr" className="seg-time">{mmss(s.start)}</span>
                  <span className={s.dropped ? "seg-text muted" : "seg-text"}>
                    {s.dropped ? "— (صمت/غير واضح)" : s.text}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
