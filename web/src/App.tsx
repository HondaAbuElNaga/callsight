import { useEffect, useState } from "react";
import { FollowupsPage } from "./FollowupsPage";
import { UsersPage } from "./UsersPage";
import { api, type Me } from "./api";
import { Login } from "./Login";
import { CallsPage } from "./CallsPage";
import { GoldsetPage } from "./GoldsetPage";
import { KpiPage } from "./KpiPage";
import { HealthPage } from "./HealthPage";
import { RatingPage } from "./RatingPage";
import { LivePage } from "./LivePage";

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = still checking
  const [view, setView] = useState<"calls" | "goldset" | "kpi" | "health" | "live" | "rating" | "followups" | "followupCall" | "users">("calls");
  const [openCall, setOpenCall] = useState<number | null>(null);   // follow-ups page -> open that call's panel
  const [problems, setProblems] = useState(0);
  const isAdmin = me?.role === "admin";
  const isStaff = me?.role === "admin" || me?.role === "supervisor";
  const isAgent = me?.role === "agent";   // agents: the follow-ups page is their only page (+ the call they open)

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, []);

  useEffect(() => {   // admins only: red banner on every page when a health check fails
    if (!isAdmin) { setProblems(0); return; }
    const check = () => api.health().then((h) => setProblems(h.problems.length)).catch(() => undefined);
    check();
    const t = setInterval(check, 120_000);
    return () => clearInterval(t);
  }, [isAdmin]);

  if (me === undefined) return <div className="center muted">…</div>;
  if (me === null) return <Login onLogin={() => api.me().then(setMe).catch(() => setMe(null))} />;
  const logout = async () => {
    await api.logout();
    setMe(null);
    setView("calls");
  };
  const banner = isAdmin && problems > 0 && view !== "health" ? (
    <button className="health-banner" onClick={() => setView("health")}>
      تنبيه: يوجد {problems} خلل في النظام (قرص، عامل، نسخ احتياطي…) — افتح صفحة حالة النظام
    </button>
  ) : null;
  let page;
  if (view === "health" && isAdmin)
    page = <HealthPage me={me} onBack={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  else if (view === "goldset" && isAdmin)
    page = <GoldsetPage me={me} onBack={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  else if (view === "rating" && isStaff)
    page = <RatingPage me={me} onBack={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  else if (view === "live" && isStaff)
    page = <LivePage me={me} onCalls={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  else if (view === "users" && isAdmin)
    page = <UsersPage me={me} onBack={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  else if (isAgent && (view !== "calls" || openCall === null))
    page = <FollowupsPage me={me} onLogout={logout} onExpired={() => setMe(null)}
      onOpenCall={(id) => { setOpenCall(id); setView("calls"); }} />;
  else if (isAgent)
    page = <CallsPage me={me} onLogout={logout} onExpired={() => setMe(null)} detailOnly
      openCallId={openCall} onFollowups={() => { setOpenCall(null); setView("followups"); }} />;
  else if (view === "followups")
    page = <FollowupsPage me={me} onCalls={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)}
      onOpenCall={(id) => { setOpenCall(id); setView("followupCall"); }} />;
  else if (view === "followupCall" && openCall !== null)   // staff: a call opened from the follow-ups page stays there
    page = <CallsPage me={me} onLogout={logout} onExpired={() => setMe(null)} detailOnly
      openCallId={openCall} onFollowups={() => { setOpenCall(null); setView("followups"); }} />;
  else if (view === "kpi" && isStaff)
    page = <KpiPage me={me} onCalls={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  else
    page = (
      <CallsPage
        me={me}
        onLogout={logout}
        onExpired={() => setMe(null)}
        onGoldset={isAdmin ? () => setView("goldset") : undefined}
        onKpi={isStaff ? () => setView("kpi") : undefined}
        onLive={isStaff ? () => setView("live") : undefined}
        onHealth={isAdmin ? () => setView("health") : undefined}
        onRating={isStaff ? () => setView("rating") : undefined}
        onFollowups={() => { setOpenCall(null); setView("followups"); }}
        onUsers={isAdmin ? () => setView("users") : undefined}
        openCallId={openCall}
        onOpened={() => setOpenCall(null)}
      />
    );
  return <>{banner}{page}</>;
}
