// Admin page (agent-supervisor spec): supervisor / agent accounts, follow-up settings, admin log.
import { useCallback, useEffect, useState } from "react";
import { api, Refused, Unauthorized, type AdminLogRow, type AppUserRow, type Me, type Settings } from "./api";
import { ROLE_LABEL, formatDateTime } from "./format";

interface Props { me: Me; onBack: () => void; onLogout: () => void; onExpired: () => void }

export function UsersPage({ me, onBack, onLogout, onExpired }: Props) {
  const [users, setUsers] = useState<AppUserRow[]>([]);
  const [settings, setSettings] = useState<Settings>({ followup_start: null, followup_kpi_since: null });
  const [log, setLog] = useState<AdminLogRow[]>([]);
  const [error, setError] = useState("");
  const [secret, setSecret] = useState<{ username: string; password: string } | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState("agent");
  const [ext, setExt] = useState("");

  const fail = useCallback((e: unknown) => {
    if (e instanceof Unauthorized) onExpired();
    else if (e instanceof Refused) setError(e.message);
    else setError("تعذر الاتصال بالخادم. أعد المحاولة.");
  }, [onExpired]);
  const reload = useCallback(() => {
    api.users().then(setUsers).catch(fail);
    api.settings().then(setSettings).catch(fail);
    api.adminLog().then(setLog).catch(fail);
  }, [fail]);
  useEffect(() => { reload(); }, [reload]);

  const run = async (f: () => Promise<unknown>) => {
    setError("");
    try { await f(); reload(); } catch (e) { fail(e); }
  };
  const create = () => run(async () => {
    const u = await api.createUser(name.trim(), role, ext.trim() || null);
    setSecret({ username: u.username, password: u.password });
    setName(""); setExt("");
  });
  const reset = (u: AppUserRow) => run(async () => {
    if (!window.confirm(`كلمة مرور جديدة لـ ${u.username}؟ ستتوقف جلساته الحالية.`)) return;
    const r = await api.resetPassword(u.id);
    setSecret({ username: r.username, password: r.password });
  });
  const saveSetting = (patch: Partial<Settings>) => run(() => api.saveSettings({ ...settings, ...patch }));

  return (
    <div className="page">
      <header className="topbar">
        <h1>المستخدمون والإعدادات</h1>
        <div className="who">
          <button className="link" onClick={onBack}>سجل المكالمات</button>
          <span className="muted">{me.username}</span>
          <button className="link" onClick={onLogout}>خروج</button>
        </div>
      </header>
      {error && <div className="error">{error}</div>}
      {secret && (
        <div className="panel secret">
          <p>كلمة مرور <b>{secret.username}</b> (تظهر مرة واحدة فقط، انسخها وأعطها للموظف):</p>
          <p dir="ltr" className="num big">{secret.password}</p>
          <button onClick={() => setSecret(null)}>تم النسخ</button>
        </div>
      )}

      <section className="panel">
        <h2>المستخدمون</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>الاسم</th><th>الدور</th><th>التحويلة</th><th>الحالة</th><th></th></tr></thead>
          <tbody>
            {users.map((u) => {
              const web = u.role !== "admin" && u.username !== me.username;
              return (
                <tr key={u.id}>
                  <td>{u.username}</td>
                  <td>{ROLE_LABEL[u.role] ?? u.role}</td>
                  <td className="num">{u.ext ?? "–"}</td>
                  <td>{u.active ? <span className="state ok">نشط</span> : <span className="state off">موقوف</span>}
                    {u.locked && <span className="tag">مقفل 15 دقيقة</span>}</td>
                  <td>{web ? <>
                    <button className="link" onClick={() => run(() => api.changeUser(u.id, { active: !u.active }))}>{u.active ? "إيقاف" : "تفعيل"}</button>
                    <button className="link" onClick={() => reset(u)}>كلمة مرور جديدة</button>
                    <button className="link" onClick={() => {
                      const v = window.prompt("التحويلة الجديدة (أرقام فقط، اتركها فارغة للحذف):", u.ext ?? "");
                      if (v === null) return;
                      run(() => api.changeUser(u.id, v.trim() ? { ext: v.trim() } : { clear_ext: true }));
                    }}>تغيير التحويلة</button>
                  </> : <span className="muted small">{u.role === "admin" ? "من سطر الأوامر" : ""}</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table></div>
        <h3>مستخدم جديد</h3>
        <form className="filters" onSubmit={(e) => { e.preventDefault(); create(); }}>
          <label>اسم الدخول <input dir="ltr" value={name} maxLength={50} placeholder="ali.ahmed" onChange={(e) => setName(e.target.value)} /></label>
          <label>الدور
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="agent">موظف</option>
              <option value="supervisor">مشرف</option>
            </select>
          </label>
          <label>التحويلة {role === "agent" ? "(مطلوبة)" : "(اختيارية)"} <input dir="ltr" inputMode="numeric" value={ext} maxLength={6} onChange={(e) => setExt(e.target.value)} /></label>
          <div className="filter-actions"><button className="primary" disabled={!name.trim() || (role === "agent" && !ext.trim())}>إنشاء</button></div>
        </form>
        <p className="muted small">لا يُحذف المستخدم نهائيًا: الإيقاف يمنع الدخول ويُنهي جلساته، ويبقى سجله.</p>
      </section>

      <section className="panel">
        <h2>المتابعة ومعاودة الاتصال</h2>
        <label>نماذج المتابعة مطلوبة للمكالمات من تاريخ{" "}
          <input type="date" value={settings.followup_start ?? ""} onChange={(e) => saveSetting({ followup_start: e.target.value || null })} />
        </label>
        <p className="muted small">فارغ = النماذج غير مفعّلة. بعد التفعيل يُطلب نموذج لكل مكالمة واردة تم الرد عليها وأطول من 20 ثانية.</p>
        <label>احتساب مؤشرات المتابعة في التقارير من تاريخ{" "}
          <input type="date" value={settings.followup_kpi_since ?? ""} disabled={!settings.followup_start}
            onChange={(e) => saveSetting({ followup_kpi_since: e.target.value || null })} />
        </label>
        <p className="muted small">فارغ = للعرض فقط في صفحة المؤشرات ولا تدخل ملفات PDF. ننصح بالتفعيل بعد نحو أسبوعين من التجربة وبعد موافقة الموارد البشرية/القانونية، لأنها تقيّم الموظفين.</p>
      </section>

      <section className="panel">
        <h2>سجل الإدارة</h2>
        {log.length === 0 ? <p className="muted">لا يوجد.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>الوقت</th><th>من</th><th>العملية</th><th>على</th><th>السبب</th></tr></thead>
            <tbody>{log.map((r) => (
              <tr key={r.id}><td className="nowrap">{formatDateTime(r.at)}</td><td>{r.user}</td><td>{ACTION_LABEL[r.action] ?? r.action}</td>
                <td dir="ltr">{r.target}</td><td>{r.reason ?? ""}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}

const ACTION_LABEL: Record<string, string> = {
  create_user: "إنشاء مستخدم", change_user: "تعديل مستخدم", reset_password: "كلمة مرور جديدة", setting: "إعداد",
  delete_call_content: "حذف محتوى مكالمة", share_call: "مشاركة مكالمة", unshare_call: "سحب مشاركة", share_reply: "رد الموظف على المشرف", share_reply_removed: "حذف رد الموظف",
};
