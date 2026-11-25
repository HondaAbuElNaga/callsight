// Shared calls as a popup chat (spec 2026-11-25-share-chat), on every page.
// Agent: a supervisor's shared call pops up as a chat; they read and answer (24 h for the first answer: KPI).
// Staff: the chat pops up when an agent writes; «أرشفة» (can reopen later) or «إغلاق المحادثة» (gone for both sides,
// still in the KPI). A WorkPanels button opens a chat through the window event OPEN_CHAT.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, OPEN_CHAT, Unauthorized, type Agent, type Me, type Share, type ShareList, type ShareReplyState } from "./api";
import { CallsPage } from "./CallsPage";
import { formatDateTime, riyadhToday } from "./format";

const POLL_MS = 20_000;
const REPLY_MIN = 10;     // same as the server (api/work.py REPLY_MIN)

const STATE: Record<ShareReplyState, [string, string]> = {
  pending: ["لم يتم الرد بعد", "warn"], on_time: ["تم الرد في الوقت", "ok"], late: ["تم الرد متأخراً", "bad"],
  missing: ["لم يتم الرد خلال 24 ساعة", "bad"], old: ["قبل تطبيق مهلة 24 ساعة", "off"],
};

function monthAgo(): string {
  const [y, m, d] = riyadhToday().split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - 30)).toISOString().slice(0, 10);
}

function left(deadline: string): string {
  const ms = new Date(deadline).getTime() - Date.now();
  if (ms <= 0) return "انتهت المهلة";
  const h = Math.floor(ms / 3_600_000), m = Math.floor((ms % 3_600_000) / 60_000);
  return h > 0 ? `باقي ${h} س ${m} د` : `باقي ${m} د`;
}

interface Props { me: Me; onLogout: () => void; onExpired: () => void }

export function ChatDock({ me, onLogout, onExpired }: Props) {
  const staff = me.role === "admin" || me.role === "supervisor";
  const [list, setList] = useState<ShareList | null>(null);
  const [win, setWin] = useState<"none" | "list" | number>("none");
  const [chat, setChat] = useState<Share | null>(null);
  const [call, setCall] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const popped = useRef(new Map<number, string>());      // chat id -> the last message that already popped up

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else setError(e instanceof Error && e.message ? e.message : "تعذر التحميل. أعد المحاولة.");
  }, [onExpired]);

  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") setTick((n) => n + 1); }, POLL_MS);
    const open = (e: Event) => setWin((e as CustomEvent<number>).detail);
    window.addEventListener(OPEN_CHAT, open);
    return () => { clearInterval(t); window.removeEventListener(OPEN_CHAT, open); };
  }, []);

  useEffect(() => {     // the open chats; a chat with new messages from the other side pops up once
    let alive = true;
    api.chats({ box: "open" }).then((d) => {
      if (!alive) return;
      setList(d);
      setError("");
      const ids = new Set(d.items.map((i) => i.id));      // archived / closed: forget, so a reopen pops up again
      for (const id of [...popped.current.keys()]) if (!ids.has(id)) popped.current.delete(id);
      const fresh = d.items.filter((i) => i.unread > 0 && popped.current.get(i.id) !== i.last_at);
      if (fresh.length) setWin((w) => {          // only counted as "popped" when the window really opens
        if (w !== "none") return w;
        fresh.forEach((i) => popped.current.set(i.id, i.last_at));
        return fresh.length === 1 ? fresh[0].id : "list";
      });
    }).catch((e) => alive && fail(e));
    return () => { alive = false; };
  }, [tick, fail]);

  const chatId = typeof win === "number" ? win : null;
  useEffect(() => {     // the open chat: read (marks it read) on open and on every poll
    if (chatId === null) { setChat(null); return; }
    let alive = true;
    api.chat(chatId).then((d) => { if (alive) { setChat(d); setError(""); } })
      .catch((e) => { if (!alive) return; setWin(staff ? "list" : "none"); fail(e); });
    return () => { alive = false; };
  }, [chatId, tick, staff, fail]);

  const refresh = () => setTick((n) => n + 1);
  const count = list?.unread ?? 0;
  const showButton = staff || (list?.open ?? 0) > 0;

  return (
    <>
      {showButton && win === "none" && (
        <button type="button" className={`chat-fab${count ? " has-new" : ""}`} onClick={() => setWin("list")}>
          المحادثات{count > 0 && <b>{count}</b>}</button>
      )}
      {win !== "none" && (
        <section className="chat-win" role="dialog" aria-label="المحادثات">
          {error && <div className="error">{error}</div>}
          {chatId !== null && chat?.id === chatId
            ? <ChatView chat={chat} staff={staff} onBack={() => setWin("list")} onClose={() => setWin("none")}
                onCall={() => setCall(chat.call_id)} onChanged={(d) => { setChat(d); refresh(); }}
                onGone={() => { setWin("list"); refresh(); }} onFail={fail} />
            : chatId !== null ? <p className="muted center">…</p>
            : <ChatList staff={staff} open={list} tick={tick} onPick={(id) => setWin(id)}
                onClose={() => setWin("none")} onFail={fail} />}
        </section>
      )}
      {call !== null && (
        <CallsPage key={call} me={me} onLogout={onLogout} onExpired={onExpired} detailOnly embedded openCallId={call}
          fromShare onFollowups={() => setCall(null)} />
      )}
    </>
  );
}

function ChatList({ staff, open, tick, onPick, onClose, onFail }:
  { staff: boolean; open: ShareList | null; tick: number; onPick: (id: number) => void; onClose: () => void;
    onFail: (e: unknown) => void }) {
  const [box, setBox] = useState<"open" | "archive">("open");
  const [agent, setAgent] = useState("");
  const [from, setFrom] = useState(monthAgo);
  const [to, setTo] = useState(riyadhToday);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [arch, setArch] = useState<ShareList | null>(null);

  useEffect(() => { if (staff) api.agents().then(setAgents).catch(onFail); }, [staff, onFail]);
  useEffect(() => {
    if (box !== "archive") return;
    let alive = true;
    api.chats({ box, agent, from, to }).then((d) => alive && setArch(d)).catch((e) => alive && onFail(e));
    return () => { alive = false; };
  }, [box, agent, from, to, tick, onFail]);

  const d = box === "open" ? open : arch;
  const items = (d?.items ?? []).filter((i) => box === "archive" || !agent || i.to_ext === agent);
  return (
    <>
      <header className="chat-head">
        <b>المحادثات</b>
        <button type="button" className="link" onClick={onClose} aria-label="إغلاق النافذة">✕</button>
      </header>
      {staff && (
        <div className="chat-tools">
          <div className="chips" role="tablist">
            {([["open", `مفتوحة${open ? ` (${open.open})` : ""}`], ["archive", "الأرشيف"]] as const).map(([k, t]) => (
              <button key={k} type="button" role="tab" aria-selected={box === k} className={`chip${box === k ? " on" : ""}`}
                onClick={() => setBox(k)}>{t}</button>
            ))}
          </div>
          <form className="filters" onSubmit={(e) => e.preventDefault()}>
            {box === "archive" && <>
              <label>من <input type="date" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>
              <label>إلى <input type="date" value={to} min={from} max={riyadhToday()} onChange={(e) => e.target.value && setTo(e.target.value)} /></label>
            </>}
            <label>الموظف
              <select value={agent} onChange={(e) => setAgent(e.target.value)}>
                <option value="">كل الموظفين</option>
                {agents.map((a) => <option key={a.ext} value={a.ext}>{a.ext} · {a.name}</option>)}
              </select>
            </label>
          </form>
        </div>
      )}
      <ul className="chat-list">
        {items.map((i) => {
          const [label, tone] = STATE[i.reply_state];
          return (
            <li key={i.id}>
              <button type="button" onClick={() => onPick(i.id)}>
                <span className="line">
                  <b>{staff ? i.to : i.from}</b>
                  {i.unread > 0 && <span className="chat-badge">{i.unread}</span>}
                  <span className="muted small">{formatDateTime(i.last_at)}</span>
                </span>
                <span className="chat-snippet">{i.last ? `${i.last.who}: ${i.last.text}` : ""}</span>
                <span className="line small">
                  <span className="muted">مكالمة {i.call_start ? formatDateTime(i.call_start) : "–"}</span>
                  <span className={`state ${tone}`}>{label}</span>
                </span>
              </button>
            </li>
          );
        })}
        {d && items.length === 0 && (
          <li className="muted center">{box === "open" ? "لا توجد محادثات مفتوحة" : "لا يوجد شيء في الأرشيف لهذه الفترة"}</li>
        )}
      </ul>
      {d?.truncated && <p className="muted small">تظهر أول 500 فقط: ضيّق الفترة.</p>}
    </>
  );
}

function ChatView({ chat, staff, onBack, onClose, onCall, onChanged, onGone, onFail }:
  { chat: Share; staff: boolean; onBack: () => void; onClose: () => void; onCall: () => void;
    onChanged: (d: Share) => void; onGone: () => void; onFail: (e: unknown) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sure, setSure] = useState(false);      // «إغلاق المحادثة» asks once more: it can't be undone
  const end = useRef<HTMLDivElement>(null);
  const n = chat.messages?.length ?? 0;
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [n]);

  const say = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try { onChanged(await api.chatSay(chat.id, t)); setText(""); } catch (e) { onFail(e); } finally { setBusy(false); }
  };
  const act = async (a: "archive" | "close" | "reopen") => {
    if (busy) return;
    setBusy(true);
    try {
      const d = await api.chatState(chat.id, a);
      if (a === "reopen") onChanged({ ...chat, ...d, messages: chat.messages }); else onGone();
    } catch (e) { onFail(e); } finally { setBusy(false); setSure(false); }
  };

  const [label, tone] = STATE[chat.reply_state];
  const t = text.trim();
  const firstShort = !staff && !chat.first_reply_at && t.length > 0 && t.length < REPLY_MIN;
  return (
    <>
      <header className="chat-head">
        <button type="button" className="link" onClick={onBack} aria-label="كل المحادثات">→</button>
        <span className="grow">
          <b>{staff ? `إلى ${chat.to}` : `من ${chat.from}`}</b>
          <span className="muted small"> · مكالمة {chat.call_start ? formatDateTime(chat.call_start) : "–"}</span>
        </span>
        <button type="button" className="link" onClick={onClose} aria-label="إغلاق النافذة">✕</button>
      </header>
      <div className="chat-bar">
        <button type="button" className="small" onClick={onCall}>فتح المكالمة</button>
        <span className={`state ${tone}`}>{label}</span>
        {chat.reply_state === "pending" && <span className="muted small">{left(chat.deadline)}</span>}
        {staff && <span className="muted small">{chat.seen_at ? `فتحها ${formatDateTime(chat.seen_at)}` : "لم يفتحها الموظف"}</span>}
      </div>
      <div className="chat-msgs">
        {chat.messages?.map((m) => (
          <div key={m.id} className={`chat-msg${m.mine ? " mine" : ""}`}>
            <div>{m.text}</div>
            <span className="muted small">{m.mine ? "أنت" : m.who} · {formatDateTime(m.at)}</span>
          </div>
        ))}
        <div ref={end} />
      </div>
      {staff && chat.status === "archived" && (
        <div className="chat-actions">
          <span className="muted small">في الأرشيف. إرسال رسالة يعيد فتحها أيضاً.</span>
          <button type="button" className="primary small" disabled={busy} onClick={() => act("reopen")}>إعادة فتح المحادثة</button>
        </div>
      )}
      <div className="chat-input">
        <textarea value={text} maxLength={1000} rows={2} placeholder={staff ? "اكتب رسالة للموظف" : "اكتب ردّك على المشرف"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); say(); } }} />
        <button type="button" className="primary" disabled={busy || !t} onClick={say}>إرسال</button>
      </div>
      {!staff && !chat.first_reply_at && (
        <p className={`small ${firstShort ? "state warn" : "muted"}`}>
          ردّك الأول خلال 24 ساعة من الإرسال يُحتسب في تقييمك ({REPLY_MIN} أحرف على الأقل).</p>
      )}
      {staff && chat.status === "open" && (
        <div className="chat-actions">
          <button type="button" className="small" disabled={busy} onClick={() => act("archive")}>أرشفة</button>
          {sure
            ? <><span className="small">تختفي عند الطرفين ولا يمكن فتحها مرة أخرى.</span>
                <button type="button" className="small danger" disabled={busy} onClick={() => act("close")}>تأكيد الإغلاق</button>
                <button type="button" className="link small" onClick={() => setSure(false)}>إلغاء</button></>
            : <button type="button" className="small" disabled={busy} onClick={() => setSure(true)}>إغلاق المحادثة</button>}
        </div>
      )}
    </>
  );
}
