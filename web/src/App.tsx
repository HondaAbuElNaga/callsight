import { useEffect, useState } from "react";
import { api, type Me } from "./api";
import { Login } from "./Login";
import { CallsPage } from "./CallsPage";
import { GoldsetPage } from "./GoldsetPage";
import { KpiPage } from "./KpiPage";
import { HealthPage } from "./HealthPage";
import { RatingPage } from "./RatingPage";

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = still checking
  const [view, setView] = useState<"calls" | "goldset" | "kpi" | "health" | "rating">("calls");
  const [problems, setProblems] = useState(0);
  const isAdmin = me?.role === "admin";
  const isStaff = isAdmin || me?.role === "supervisor";

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
  else if (view === "kpi")
    page = <KpiPage me={me} onCalls={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  else
    page = (
      <CallsPage
        me={me}
        onLogout={logout}
        onExpired={() => setMe(null)}
        onGoldset={isAdmin ? () => setView("goldset") : undefined}
        onKpi={() => setView("kpi")}
        onHealth={isAdmin ? () => setView("health") : undefined}
        onRating={isStaff ? () => setView("rating") : undefined}
      />
    );
  return <>{banner}{page}</>;
}
