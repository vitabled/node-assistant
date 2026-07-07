import { Construction } from "lucide-react";

// Reusable section placeholder — a centered card with an icon, title and note.
// Used for not-yet-implemented Remnawave sections (rw-*): Волна-2 stubs
// (Миграция/Профили) and the yet-to-be-built Волна-1 sections
// (Установка/Страницы подписок/Переменные/Резервное копирование).
export function Placeholder({ title, note }: { title: string; note?: string }) {
  return (
    <div style={{
      flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
      padding: 24, minHeight: 0, overflowY: "auto",
    }}>
      <div className="card" style={{
        maxWidth: 420, width: "100%", padding: "32px 28px", textAlign: "center",
        display: "flex", flexDirection: "column", alignItems: "center", gap: 14,
      }}>
        <span style={{
          width: 52, height: 52, borderRadius: "var(--r-md)", flex: "none",
          background: "var(--accent-dim)", color: "var(--accent)",
          display: "grid", placeItems: "center",
        }}>
          <Construction size={26} />
        </span>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <p style={{ fontSize: 15, fontWeight: 600, color: "var(--t-hi)" }}>{title}</p>
          <p style={{ fontSize: 13, color: "var(--t-low)", lineHeight: 1.5 }}>
            {note ?? "Раздел появится в Волне 2"}
          </p>
        </div>
      </div>
    </div>
  );
}
