// «Настройки → Роли» (Волна 13).
//
// Роль — набор привилегий; пользователь получает объединение привилегий своих
// ролей. Работает с `/api/roles` (нужна `admin.roles`).
//
// ⚠️ Матрица строится из `GET /api/roles/catalogue`, а НЕ из своей копии списка
// привилегий. Копия отстала бы от бэкенда на первой же новой ручке, и роль
// молча перестала бы покрывать раздел, который в ней вроде бы отмечен.
//
// ⚠️ Ошибки сервера показываем ДОСЛОВНО: «роль шире ваших прав — не хватает
// admin.infrastructure» объясняет отказ, а «Ошибка» заставляет гадать.
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ShieldCheck, Plus, Trash2, Loader2, AlertTriangle, Lock, Save, X, Wand2,
} from "lucide-react";

interface Catalogue {
  actions: { id: string; title: string }[];
  domains: { id: string; title: string; actions: string[] }[];
  special: { id: string; title: string }[];
}

interface Role {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  builtin: boolean;
  holders: number;
}

interface Props {
  /** Роли текущего пользователя: правку своей роли надо сопроводить
   *  предупреждением — можно отрезать себе доступ к этому самому разделу. */
  myRoleIds: string[];
}

const JSONH = { "Content-Type": "application/json" };

function fmtError(data: unknown): string {
  const d = (data as { detail?: unknown } | null)?.detail;
  if (typeof d === "string" && d.trim()) return d;
  if (Array.isArray(d)) {
    const parts = d.map(e => (e as { msg?: string })?.msg).filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return "Не удалось выполнить операцию";
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let body: unknown = null;
    try { body = await r.json(); } catch { /* тело может быть пустым */ }
    throw new Error(fmtError(body));
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

/** Шаблоны: собирать шесть десятков галочек руками ради типовой роли — не работа
 *  для человека. Берём привилегии готовой встроенной роли как отправную точку. */
const TEMPLATES: { id: string; label: string }[] = [
  { id: "operator", label: "как у Оператора" },
  { id: "finance", label: "как у Финансов" },
  { id: "viewer", label: "как у Наблюдателя" },
];

export function RolesTab({ myRoleIds }: Props) {
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<Role | null>(null);
  const [creating, setCreating] = useState(false);
  const [confirmDel, setConfirmDel] = useState("");

  const reload = useCallback(async () => {
    setErr("");
    try {
      const [c, r] = await Promise.all([
        call<Catalogue>("/api/roles/catalogue"),
        call<{ roles: Role[] }>("/api/roles"),
      ]);
      setCat(c);
      setRoles(r.roles);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось загрузить роли");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const remove = async (id: string) => {
    setErr("");
    try {
      await call<void>(`/api/roles/${id}`, { method: "DELETE" });
      setConfirmDel("");
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось удалить роль");
    }
  };

  if (loading) {
    return (
      <p className="hint" style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <Loader2 size={13} className="animate-spin" /> Загрузка ролей…
      </p>
    );
  }

  return (
    <div className="card" style={{ maxWidth: 860 }}>
      <div className="cardhead">
        <ShieldCheck size={14} />
        <span>Роли</span>
        <button className="btn btn-sm" style={{ marginLeft: "auto" }}
          onClick={() => { setCreating(true); setEditing(null); }}>
          <Plus size={13} /> Роль
        </button>
      </div>

      <div className="cardbody flex flex-col gap-3">
        {err && (
          <p className="hint" style={{ color: "var(--err)", display: "flex", gap: 6 }}>
            <AlertTriangle size={13} /> {err}
          </p>
        )}

        <p className="hint" style={{ marginTop: 0 }}>
          Роль — набор привилегий. Пользователь получает объединение привилегий
          всех своих ролей. Разделение по действиям: просмотр, создание,
          изменение и выполнение операций.
        </p>

        {roles.map(role => (
          <div key={role.id} className="flex flex-col gap-1"
            style={{
              border: "1px solid var(--line-soft)", borderRadius: 8, padding: 10,
            }}>
            <div className="flex items-center gap-2">
              <span style={{ fontSize: 13, fontWeight: 600, color: "var(--t-hi)" }}>
                {role.name}
              </span>
              {role.builtin && (
                <span className="micro" title="Встроенную роль можно изменить, но не удалить"
                  style={{ display: "flex", alignItems: "center", gap: 3 }}>
                  <Lock size={10} /> встроенная
                </span>
              )}
              <span className="micro">{role.permissions.length} привилегий</span>
              <span className="micro">
                {role.holders === 0 ? "не назначена" : `носителей: ${role.holders}`}
              </span>
              <div className="flex items-center gap-1" style={{ marginLeft: "auto" }}>
                <button className="btn btn-sm"
                  onClick={() => { setEditing(role); setCreating(false); }}>
                  Изменить
                </button>
                {!role.builtin && (
                  confirmDel === role.id ? (
                    <button className="btn btn-sm btn-danger"
                      onClick={() => void remove(role.id)}>
                      Точно удалить?
                    </button>
                  ) : (
                    <button className="iconbtn" title="Удалить роль"
                      onClick={() => setConfirmDel(role.id)}>
                      <Trash2 size={13} />
                    </button>
                  )
                )}
              </div>
            </div>
            {role.description && <p className="hint" style={{ margin: 0 }}>{role.description}</p>}
          </div>
        ))}

        {(editing || creating) && cat && (
          <RoleEditor
            key={editing?.id ?? "new"}
            cat={cat}
            role={editing}
            roles={roles}
            warnSelf={!!editing && myRoleIds.includes(editing.id)}
            onClose={() => { setEditing(null); setCreating(false); }}
            onSaved={async () => { setEditing(null); setCreating(false); await reload(); }}
          />
        )}
      </div>
    </div>
  );
}

function RoleEditor({ cat, role, roles, warnSelf, onClose, onSaved }: {
  cat: Catalogue;
  role: Role | null;
  roles: Role[];
  warnSelf: boolean;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [perms, setPerms] = useState<Set<string>>(new Set(role?.permissions ?? []));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const toggle = (p: string) =>
    setPerms(prev => {
      const next = new Set(prev);
      next.has(p) ? next.delete(p) : next.add(p);
      return next;
    });

  /** Вся строка домена: одна галочка вместо четырёх. */
  const toggleDomain = (domain: string, actions: string[], on: boolean) =>
    setPerms(prev => {
      const next = new Set(prev);
      for (const a of actions) {
        const key = `${domain}.${a}`;
        on ? next.add(key) : next.delete(key);
      }
      return next;
    });

  const applyTemplate = (templateId: string) => {
    const src = roles.find(r => r.id === templateId);
    if (src) setPerms(new Set(src.permissions));
  };

  const templates = useMemo(
    () => TEMPLATES.filter(t => roles.some(r => r.id === t.id)),
    [roles],
  );

  const save = async () => {
    setErr("");
    if (!name.trim()) { setErr("Название роли не может быть пустым"); return; }
    setBusy(true);
    try {
      const body = JSON.stringify({
        name: name.trim(), description, permissions: [...perms],
      });
      if (role) {
        await call<Role>(`/api/roles/${role.id}`, { method: "PATCH", headers: JSONH, body });
      } else {
        await call<Role>("/api/roles", { method: "POST", headers: JSONH, body });
      }
      await onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось сохранить роль");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3"
      style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12 }}>
      <div className="flex items-center gap-2">
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--t-hi)" }}>
          {role ? `Изменение роли «${role.name}»` : "Новая роль"}
        </span>
        <button className="iconbtn" title="Закрыть" style={{ marginLeft: "auto" }}
          onClick={onClose}><X size={13} /></button>
      </div>

      {warnSelf && (
        <p className="hint" style={{ color: "var(--warn)", display: "flex", gap: 6 }}>
          <AlertTriangle size={13} /> Эта роль надета на вас: снятая привилегия
          подействует и на вас, вплоть до потери доступа к этому разделу.
        </p>
      )}
      {err && (
        <p className="hint" style={{ color: "var(--err)", display: "flex", gap: 6 }}>
          <AlertTriangle size={13} /> {err}
        </p>
      )}

      <label className="flex flex-col gap-1">
        <span className="micro">Название</span>
        <input className="input" value={name} disabled={busy}
          onChange={e => setName(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="micro">Описание</span>
        <input className="input" value={description} disabled={busy}
          placeholder="для кого эта роль" onChange={e => setDescription(e.target.value)} />
      </label>

      {templates.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="micro" style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <Wand2 size={11} /> Заполнить
          </span>
          {templates.map(t => (
            <button key={t.id} className="btn btn-sm" disabled={busy}
              onClick={() => applyTemplate(t.id)}>{t.label}</button>
          ))}
        </div>
      )}

      <div style={{ overflowX: "auto" }}>
        <table className="tbl" style={{ minWidth: 520 }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Раздел</th>
              {cat.actions.map(a => <th key={a.id}>{a.title}</th>)}
              <th />
            </tr>
          </thead>
          <tbody>
            {cat.domains.map(d => {
              const all = d.actions.every(a => perms.has(`${d.id}.${a}`));
              return (
                <tr key={d.id}>
                  <td style={{ color: "var(--t-mid)" }}>{d.title}</td>
                  {cat.actions.map(a => (
                    <td key={a.id} style={{ textAlign: "center" }}>
                      {d.actions.includes(a.id) ? (
                        <input type="checkbox" disabled={busy}
                          aria-label={`${d.title}: ${a.title}`}
                          checked={perms.has(`${d.id}.${a.id}`)}
                          onChange={() => toggle(`${d.id}.${a.id}`)} />
                      ) : (
                        // Действие для этого раздела не имеет смысла — прочерк
                        // честнее, чем выключенная галочка, которую примут за «нет».
                        <span className="micro">—</span>
                      )}
                    </td>
                  ))}
                  <td style={{ textAlign: "right" }}>
                    <button className="btn btn-sm" disabled={busy}
                      onClick={() => toggleDomain(d.id, d.actions, !all)}>
                      {all ? "снять" : "всё"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex flex-col gap-1">
        <span className="micro">Особые привилегии</span>
        {cat.special.map(s => (
          <label key={s.id} className="flex items-start gap-2"
            style={{ fontSize: 12, color: "var(--t-mid)" }}>
            <input type="checkbox" disabled={busy} checked={perms.has(s.id)}
              onChange={() => toggle(s.id)} style={{ marginTop: 2 }} />
            <span>{s.title} <span className="micro">({s.id})</span></span>
          </label>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <button className="btn btn-primary" disabled={busy} onClick={() => void save()}>
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          Сохранить
        </button>
        <span className="micro">{perms.size} привилегий выбрано</span>
      </div>
    </div>
  );
}
