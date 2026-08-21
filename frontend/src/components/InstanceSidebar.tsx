import { useState } from "react";
import { ChevronLeft, ChevronRight, Plus, Boxes } from "lucide-react";
import { useInstance } from "../instances/InstanceContext";

const COLLAPSED_KEY = "ni_instance_sidebar_collapsed";

export function InstanceSidebar() {
  const { instances, activeInstanceId, selectInstance, createInstance, loading } = useInstance();
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSED_KEY) === "1"; } catch { return false; }
  });
  const toggle = () => setCollapsed(value => {
    const next = !value;
    try { localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0"); } catch {}
    return next;
  });
  const add = async () => {
    const name = window.prompt("Название нового инстанса")?.trim();
    if (!name) return;
    try { await createInstance(name); }
    catch { window.alert("Не удалось создать инстанс"); }
  };

  if (collapsed) {
    return (
      <aside className="ni-instance-sidebar" style={{ width: 18, flex: "none", position: "relative", borderRight: "1px solid var(--line-soft)", background: "var(--sidebar-bg)" }}>
        <button type="button" onClick={toggle} title="Показать инстансы" aria-label="Показать инстансы"
          style={{ position: "absolute", top: 16, left: 2, width: 28, height: 28, borderRadius: 14, border: "1px solid var(--line-soft)", background: "var(--surface)", color: "var(--t-low)", display: "grid", placeItems: "center", cursor: "pointer", zIndex: 2 }}>
          <ChevronRight size={14} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="ni-instance-sidebar" aria-label="Инстансы" style={{ width: 64, flex: "none", borderRight: "1px solid var(--line-soft)", background: "var(--sidebar-bg)", display: "flex", flexDirection: "column", alignItems: "center", gap: 9, padding: "13px 8px" }}>
      <button type="button" onClick={toggle} title="Скрыть инстансы" aria-label="Скрыть инстансы" className="icon-btn" style={{ width: 36, height: 30 }}>
        <ChevronLeft size={14} />
      </button>
      <Boxes size={17} style={{ color: "var(--t-faint)", margin: "3px 0 2px" }} />
      {!loading && instances.map(instance => {
        const active = instance.id === activeInstanceId;
        return (
          <button key={instance.id} type="button" onClick={() => selectInstance(instance.id)}
            title={instance.name} aria-label={instance.name} aria-current={active ? "true" : undefined}
            style={{ width: 38, height: 38, borderRadius: 11, border: active ? "1px solid var(--accent)" : "1px solid var(--line-soft)", background: active ? "var(--accent-dim)" : "var(--surface)", color: active ? "var(--accent)" : "var(--t-low)", fontWeight: 700, fontSize: 12, cursor: "pointer", boxShadow: active ? "0 0 0 2px color-mix(in srgb, var(--accent) 15%, transparent)" : "none" }}>
            {instance.name.slice(0, 2).toUpperCase()}
          </button>
        );
      })}
      <button type="button" onClick={() => void add()} title="Создать инстанс" aria-label="Создать инстанс"
        style={{ width: 38, height: 38, borderRadius: 11, border: "1px dashed var(--line)", background: "transparent", color: "var(--t-low)", display: "grid", placeItems: "center", cursor: "pointer" }}>
        <Plus size={16} />
      </button>
    </aside>
  );
}