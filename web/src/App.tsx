import { useEffect, useState } from "react";
import { api, type Me } from "./api";
import { Login } from "./Login";
import { CallsPage } from "./CallsPage";

export function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = still checking

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, []);

  if (me === undefined) return <div className="center muted">…</div>;
  if (me === null) return <Login onLogin={() => api.me().then(setMe).catch(() => setMe(null))} />;
  return (
    <CallsPage
      me={me}
      onLogout={async () => {
        await api.logout();
        setMe(null);
      }}
      onExpired={() => setMe(null)}
    />
  );
}
