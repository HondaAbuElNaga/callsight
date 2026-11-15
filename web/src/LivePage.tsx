import { useEffect, useMemo, useRef, useState } from "react";
import { api, Unauthorized, type Agent, type LiveCall, type LiveMessage, type Me } from "./api";
import { DIRECTION_LABEL, formatDateTime, mmss } from "./format";

interface Props { me: Me; onCalls: () => void; onLogout: () => void; onExpired: () => void }

const STATE_LABEL: Record<LiveCall["state"], string> = {
  waiting: "في الانتظار", ringing: "يرن", talking: "يتحدث", holding: "انتظار (Hold)",
};

type Board = {
  available: boolean;          // false = live service off / not running: "no live data"
  status: string;              // syncing | connected | disconnected | auth_blocked | off | stopped
  lastSeen: number | null;     // epoch seconds of the last live data
  seq: number | null;
  calls: Map<string, LiveCall>;
  skew: number;                // server clock - browser clock (s), so timers don't depend on the PC's clock
};

const EMPTY: Board = { available: false, status: "off", lastSeen: null, seq: null, calls: new Map(), skew: 0 };

function wsUrl(): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`;
}

// Browser side of the live push: snapshot, then deltas with a sequence number; on a gap it asks for the snapshot
// again. Reconnects with back-off 1 s -> 30 s plus jitter. Close code 4401 = login expired; a refused handshake
// shows up as 1006 in browsers, so after 1006 /api/me tells whether the login has expired.
function useLiveBoard(onExpired: () => void): [Board, boolean] {
  const [board, setBoard] = useState<Board>(EMPTY);
  const [connected, setConnected] = useState(false);
  const expired = useRef(onExpired);
  expired.current = onExpired;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let delay = 1000;
    let timer: number | undefined;
    let idle: number | undefined;
    let seq: number | null = null;
    let snapshotPending = false;   // one "snapshot" request at a time; deltas are dropped until it arrives

    const askSnapshot = (force = false) => {
      if (snapshotPending && !force) return;
      if (ws?.readyState === WebSocket.OPEN) { ws.send("snapshot"); snapshotPending = true; }
    };
    const armIdle = () => {   // nothing heard for 45 s (service died?): ask for the snapshot again
      window.clearTimeout(idle);
      idle = window.setTimeout(() => { askSnapshot(true); armIdle(); }, 45_000);
    };

    const connect = () => {
      ws = new WebSocket(wsUrl());
      ws.onopen = () => { setConnected(true); delay = 1000; snapshotPending = false; armIdle(); };
      ws.onmessage = (ev) => {
        armIdle();
        let m: LiveMessage;
        try { m = JSON.parse(ev.data as string) as LiveMessage; } catch { return; }
        const skew = m.type === "snapshot" ? m.server_time - Date.now() / 1000 : undefined;
        if (m.type === "snapshot") {
          seq = m.seq;
          snapshotPending = false;
          setBoard((b) => ({
            available: m.available, status: m.status, lastSeen: m.last_seen, seq: m.seq,
            calls: new Map(m.calls.map((c) => [c.call_id, c])), skew: skew ?? b.skew,
          }));
          return;
        }
        if (seq !== null && m.seq <= seq) return;   // stale: already in the snapshot
        if (seq === null || m.seq !== seq + 1) {   // gap (or no snapshot yet): fetch the whole state again
          seq = null;
          askSnapshot();
          return;
        }
        seq = m.seq;
        setBoard((b) => {
          const calls = new Map(b.calls);
          for (const d of m.deltas) {
            if (d.op === "reset") calls.clear();
            else if (d.op === "remove") calls.delete(d.call_id);
            else if (d.op === "upsert") calls.set(d.call.call_id, d.call);
          }
          const available = m.status !== "stopped";
          return { ...b, available, status: m.status, lastSeen: m.published_at, seq: m.seq, calls };
        });
      };
      ws.onclose = (ev) => {
        setConnected(false);
        window.clearTimeout(idle);
        if (stopped) return;
        if (ev.code === 4401) { expired.current(); return; }
        if (ev.code === 4403) return;                  // not allowed: don't retry
        if (ev.code === 1006) {                        // handshake refused (or network): is the login still valid?
          api.me().catch((e) => { if (e instanceof Unauthorized && !stopped) { stopped = true; expired.current(); } });
        }
        const wait = Math.min(30_000, delay) * (0.8 + Math.random() * 0.4);
        delay = Math.min(30_000, delay * 2);
        timer = window.setTimeout(connect, wait);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearTimeout(idle);
      ws?.close();
    };
  }, []);
  return [board, connected];
}

function useNow(): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, []);
  return now;
}

function agentLabel(ext: string, names: Map<string, string>): string {
  const n = names.get(ext);
  return n ? `${n} (${ext})` : `تحويلة ${ext}`;
}

// Admin + supervisor: the calls happening now. Listen is switched off (no button): see the notice.
export function LivePage({ me, onCalls, onLogout, onExpired }: Props) {
  const [board, connected] = useLiveBoard(onExpired);
  const [agents, setAgents] = useState<Agent[]>([]);
  const now = useNow();

  useEffect(() => { api.agents().then(setAgents).catch(() => undefined); }, []);
  const names = useMemo(() => new Map(agents.map((a) => [a.ext, a.name])), [agents]);

  const calls = [...board.calls.values()].sort((a, b) => a.started_at - b.started_at);
  const active = calls.filter((c) => c.state !== "waiting");
  const waiting = calls.filter((c) => c.state === "waiting" || (c.state === "ringing" && c.queue));
  const serverNow = now + board.skew;

  let notice: string | null = null;
  if (!connected) notice = "جاري الاتصال بالخادم…";
  else if (!board.available)
    notice = `لا توجد بيانات مباشرة: خدمة اللوحة المباشرة متوقفة.${board.lastSeen ? ` آخر بيانات: ${formatDateTime(new Date(board.lastSeen * 1000).toISOString())}` : ""}`;
  else if (board.status === "syncing") notice = "جاري المزامنة… تظهر المكالمات مع أول تغيير في حالتها.";
  else if (board.status === "disconnected") notice = "انقطع الاتصال بالسنترال، تتم إعادة المحاولة تلقائيًا.";
  else if (board.status === "auth_blocked")
    notice = "تم إيقاف الدخول إلى السنترال بعد رفض تسجيل الدخول. يحتاج المسؤول إلى تشغيل الأمر pbx-unblock بعد إصلاح السبب.";

  return (
    <div className="page">
      <header className="topbar">
        <h1>المكالمات الحالية</h1>
        <p className="muted small">مكالمات موظفي الفريق وطابور خدمة العملاء فقط.</p>
        <div className="who">
          <button className="link" onClick={onCalls}>سجل المكالمات</button>
          <span className="muted">{me.username}{me.ext ? ` · تحويلة ${me.ext}` : ""}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>

      {notice && <div className={`live-notice ${board.available && connected ? "" : "off"}`} role="status">{notice}</div>}

      <section className="panel">
        <h2>مكالمات جارية <span className="muted num">({active.length})</span></h2>
        {active.length === 0 ? (
          <p className="muted">{board.available ? "لا توجد مكالمات جارية الآن." : "—"}</p>
        ) : (
          <div className="live-grid">
            {active.map((c) => {
              const since = c.answered_at ?? c.started_at;
              const who = c.agents.length ? c.agents : c.ringing;
              return (
                <article key={c.call_id} className={`live-card st-${c.state}`}>
                  <div className="live-card-top">
                    <span className={`badge live-${c.state}`}>{STATE_LABEL[c.state]}</span>
                    <span className="num live-timer" dir="ltr">{mmss(serverNow - since)}</span>
                  </div>
                  <div className="live-agent">{who.length ? who.map((e) => agentLabel(e, names)).join("، ") : "—"}</div>
                  <div className="muted small">
                    {DIRECTION_LABEL[c.direction] ?? c.direction}
                    {c.caller && <> · <span className="num" dir="ltr">{c.caller}</span></>}
                    {c.queue && <> · طابور {c.queue}</>}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="panel">
        <h2>في انتظار الرد <span className="muted num">({waiting.length})</span></h2>
        {waiting.length === 0 ? <p className="muted">لا أحد في الانتظار.</p> : (
          <ul className="live-queue">
            {waiting.map((c) => (
              <li key={c.call_id}>
                <span className="num" dir="ltr">{c.caller ?? "—"}</span>
                {c.queue && <span className="muted"> · طابور {c.queue}</span>}
                <span className="num muted" dir="ltr"> {mmss(serverNow - c.started_at)}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="muted small">قائمة الانتظار تقريبية (من مسار المكالمة)، حتى نلتقط أحداث السنترال الحقيقية.</p>
      </section>

      <p className="muted small live-legal">
        الاستماع المباشر متوقف. قبل تشغيله يلزم: إشعار في الرد الآلي بأن المكالمات قد تُسجَّل أو تُراقَب، وسياسة
        مراقبة مكتوبة للموظفين، وموافقة قانونية (نظام حماية البيانات الشخصية ونظام مكافحة الجرائم المعلوماتية).
        أرقام المتصلين تظهر مخفية (آخر 4 أرقام فقط).
      </p>
    </div>
  );
}
