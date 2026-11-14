import { useEffect, useState } from "react";
import { api, type Me } from "./api";
import { Login } from "./Login";
import { CallsPage } from "./CallsPage";
import { GoldsetPage } from "./GoldsetPage";
import { KpiPage } from "./KpiPage";

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = still checking
  const [view, setView] = useState<"calls" | "goldset" | "kpi">("calls");

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, []);

  if (me === undefined) return <div className="center muted">…</div>;
  if (me === null) return <Login onLogin={() => api.me().then(setMe).catch(() => setMe(null))} />;
  const logout = async () => {
    await api.logout();
    setMe(null);
    setView("calls");
  };
  if (view === "goldset" && me.role === "admin")
    return <GoldsetPage me={me} onBack={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  if (view === "kpi")
    return <KpiPage me={me} onCalls={() => setView("calls")} onLogout={logout} onExpired={() => setMe(null)} />;
  return (
    <CallsPage
      me={me}
      onLogout={logout}
      onExpired={() => setMe(null)}
      onGoldset={me.role === "admin" ? () => setView("goldset") : undefined}
      onKpi={() => setView("kpi")}
    />
  );
}
