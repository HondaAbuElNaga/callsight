import { useState, type FormEvent } from "react";
import { api } from "./api";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const r = await api.login(username, password).catch(() => ({ ok: false as const, message: "تعذر الاتصال بالخادم" }));
    setBusy(false);
    if (r.ok) onLogin();
    else setError(r.message);
  }

  return (
    <div className="login-wrap">
      <form className="login" onSubmit={submit}>
        <h1>سجل المكالمات</h1>
        <p className="muted">فريق خدمة العملاء وما بعد البيع</p>
        <label>
          اسم المستخدم
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus dir="ltr" />
        </label>
        <label>
          كلمة المرور
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" dir="ltr" />
        </label>
        {error && <div className="error">{error}</div>}
        <button type="submit" className="primary" disabled={busy || !username || !password}>
          {busy ? "جارٍ الدخول…" : "دخول"}
        </button>
      </form>
    </div>
  );
}
