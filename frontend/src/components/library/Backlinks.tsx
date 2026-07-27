// «Обратные ссылки» — who points at this note, and which of its own links point
// at nothing yet. Both come straight from /api/library/graph (resolution happens
// server-side, on read, because links are stored by name).
import { CornerUpLeft, Link2Off, Plus, FileText } from "lucide-react";
import type { Graph } from "./api";

export function Backlinks({ graph, noteId, names, onOpen, onCreate }: {
  graph: Graph | null;
  noteId: string;
  /** id → note name, for rendering the incoming links. */
  names: Map<string, string>;
  onOpen: (id: string) => void;
  onCreate: (name: string) => void;
}) {
  const node = graph?.[noteId];
  const incoming = node?.in ?? [];
  const unresolved = node?.unresolved ?? [];

  return (
    <div className="card" style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <div className="micro" style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
          <CornerUpLeft size={12} /> Обратные ссылки · {incoming.length}
        </div>
        {incoming.length === 0 ? (
          <p className="hint" style={{ marginTop: 0 }}>На эту заметку пока никто не ссылается.</p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {incoming.map(id => (
              <button key={id} className="btn btn-sm" onClick={() => onOpen(id)}
                title="Открыть заметку">
                <FileText size={12} /> {names.get(id) || id}
              </button>
            ))}
          </div>
        )}
      </div>

      {unresolved.length > 0 && (
        <div>
          <div className="micro" style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
            <Link2Off size={12} /> Неразрешённые · {unresolved.length}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {unresolved.map(name => (
              <button key={name} className="btn btn-sm" onClick={() => onCreate(name)}
                title={`Создать заметку «${name}»`}
                style={{ color: "var(--t-low)", borderStyle: "dashed" }}>
                <Plus size={12} /> {name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
