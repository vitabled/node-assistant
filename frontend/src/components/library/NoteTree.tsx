// Folder tree over the note set.
//
// A folder is mostly DERIVED from the `folder` path string on the notes (exactly
// like Obsidian), so "renaming a folder" is a bulk move of everything under it.
// The one exception is an empty folder: nothing implies it, so the store keeps a
// row of its own for it — those arrive in `folders` and merge into the same tree.
//
// Everything is arranged by drag-and-drop (native HTML5, no library): a note
// dropped ON a folder moves into it, dropped on the upper/lower half of another
// note it takes that place in the order, and a folder dropped on a folder becomes
// its child.
import { useEffect, useMemo, useState } from "react";
import {
  ChevronRight, ChevronDown, Folder, FolderOpen, FolderPlus, FileText,
  Plus, Pencil, Trash2, Check, X,
} from "lucide-react";
import { getActiveId } from "../../auth/store";
import { normFolder, parentFolder, type LibItem, type ReorderRow } from "./api";

export interface TreeNote { id: string; name: string; folder: string; order: number }
/** An empty folder's own row — `id` is what DELETE takes. */
export interface TreeFolder { id: string; path: string }

interface Node {
  path: string;
  name: string;
  /** Backing folder row, when this folder has one (an implied folder has none). */
  id: string | null;
  children: Node[];
  notes: TreeNote[];
  count: number;   // notes in this folder and every folder under it
}

/** Folders above notes; notes by `order`, ties broken by name so a set of notes
 *  that never was reordered (all zeros) still reads alphabetically. */
const byOrder = (a: TreeNote, b: TreeNote) =>
  (a.order - b.order) || a.name.localeCompare(b.name, "ru");

function buildTree(notes: TreeNote[], folders: TreeFolder[]): Node {
  const root: Node = { path: "", name: "", id: null, children: [], notes: [], count: 0 };
  const byPath = new Map<string, Node>([["", root]]);

  const ensure = (path: string): Node => {
    const found = byPath.get(path);
    if (found) return found;
    const parent = ensure(parentFolder(path));
    const node: Node = {
      path, name: path.slice(path.lastIndexOf("/") + 1),
      id: null, children: [], notes: [], count: 0,
    };
    parent.children.push(node);
    byPath.set(path, node);
    return node;
  };

  for (const f of folders) {
    const path = normFolder(f.path);
    if (path) ensure(path).id = f.id;
  }
  for (const n of notes) ensure(normFolder(n.folder || "")).notes.push(n);

  const finish = (n: Node): number => {
    n.children.sort((a, b) => a.name.localeCompare(b.name, "ru"));
    n.notes.sort(byOrder);
    n.count = n.notes.length + n.children.reduce((s, c) => s + finish(c), 0);
    return n.count;
  };
  finish(root);
  return root;
}

export function notesOf(items: LibItem[]): TreeNote[] {
  return items
    .filter(it => it.kind === "note")
    .map(it => ({
      id: it.id, name: it.name || "", folder: normFolder(it.folder || ""), order: it.order ?? 0,
    }));
}

export function foldersOf(items: LibItem[]): TreeFolder[] {
  return items
    .filter(it => it.kind === "folder" && it.path)
    .map(it => ({ id: it.id, path: normFolder(it.path || "") }));
}

// ── collapsed folders, per account ────────────────────────────
const collapsedKey = () => `ni_lib_collapsed_${getActiveId() ?? "none"}`;

function loadCollapsed(): Set<string> {
  try {
    const raw = JSON.parse(localStorage.getItem(collapsedKey()) || "[]");
    return new Set(Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set<string>();
  }
}

type Drag = { kind: "note"; id: string } | { kind: "folder"; path: string };

export function NoteTree({
  notes, folders, activeId,
  onOpen, onCreateNote, onCreateFolder, onRenameFolder, onDeleteFolder, onReorder,
}: {
  notes: TreeNote[];
  folders: TreeFolder[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onCreateNote: (folder: string) => void;
  onCreateFolder: (path: string) => void;
  onRenameFolder: (src: string, dst: string) => void;
  /** `id` is null for an implied folder — it has no row of its own to delete. */
  onDeleteFolder: (folder: { id: string | null; path: string }) => void;
  onReorder: (rows: ReorderRow[]) => void;
}) {
  const tree = useMemo(() => buildTree(notes, folders), [notes, folders]);
  const [collapsed, setCollapsed] = useState<Set<string>>(loadCollapsed);
  const [selected, setSelected] = useState("");
  const [editing, setEditing] = useState<{ path: string; value: string } | null>(null);
  const [creating, setCreating] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [overFolder, setOverFolder] = useState<string | null>(null);
  const [overNote, setOverNote] = useState<{ id: string; edge: "top" | "bottom" } | null>(null);

  useEffect(() => {
    try { localStorage.setItem(collapsedKey(), JSON.stringify([...collapsed])); } catch { /* приватный режим */ }
  }, [collapsed]);

  // Каждый существующий путь, включая промежуточные: «А/Б» подразумевает «А».
  const paths = useMemo(() => {
    const out = new Set<string>();
    const add = (p: string) => { while (p) { out.add(p); p = parentFolder(p); } };
    for (const f of folders) add(normFolder(f.path));
    for (const n of notes) add(n.folder);
    return out;
  }, [notes, folders]);

  // Выбранную папку могли переименовать или удалить — иначе «+ Папка» воскресила
  // бы её, создав новую по устаревшему пути.
  useEffect(() => {
    if (selected && !paths.has(selected)) setSelected("");
  }, [paths, selected]);

  const toggle = (path: string) => setCollapsed(prev => {
    const next = new Set(prev);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });

  const clearDrag = () => { setDrag(null); setOverFolder(null); setOverNote(null); };

  const commitRename = () => {
    if (!editing) return;
    const name = editing.value.trim();
    const parent = parentFolder(editing.path);
    const dst = normFolder(parent ? `${parent}/${name}` : name);
    setEditing(null);
    if (name && dst !== editing.path) onRenameFolder(editing.path, dst);
  };

  const commitCreate = () => {
    const name = (creating || "").trim();
    setCreating(null);
    if (name) onCreateFolder(normFolder(selected ? `${selected}/${name}` : name));
  };

  // ── drop resolution ─────────────────────────────────────────
  const notesIn = (folder: string) => notes.filter(n => n.folder === folder).sort(byOrder);

  /** Re-numbers the WHOLE target folder — one authoritative list beats deltas. */
  const moveNote = (id: string, folder: string, beforeId: string | null) => {
    const rest = notesIn(folder).filter(n => n.id !== id);
    const at = beforeId ? rest.findIndex(n => n.id === beforeId) : -1;
    const ids = at < 0
      ? [...rest.map(n => n.id), id]
      : [...rest.slice(0, at).map(n => n.id), id, ...rest.slice(at).map(n => n.id)];
    const now = notesIn(folder);
    if (ids.length === now.length && ids.every((x, i) => now[i].id === x)) return;   // ничего не изменилось
    onReorder(ids.map((x, i) => ({ id: x, folder, order: i })));
  };

  /** A folder may not land inside itself or its own subtree. */
  const folderFits = (src: string, target: string) =>
    target !== src && !target.startsWith(src + "/");

  const moveFolder = (src: string, parent: string) => {
    const name = src.slice(src.lastIndexOf("/") + 1);
    const dst = normFolder(parent ? `${parent}/${name}` : name);
    if (dst !== src) onRenameFolder(src, dst);
  };

  const acceptsInto = (path: string) =>
    !!drag && (drag.kind === "note" || folderFits(drag.path, path));

  const intoProps = (path: string) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!acceptsInto(path)) return;
      e.preventDefault();
      setOverNote(null);
      setOverFolder(path);
    },
    onDragLeave: () => setOverFolder(cur => (cur === path ? null : cur)),
    onDrop: (e: React.DragEvent) => {
      if (!acceptsInto(path)) return;
      e.preventDefault();
      const d = drag;
      clearDrag();
      if (!d) return;
      if (d.kind === "note") moveNote(d.id, path, null);
      else moveFolder(d.path, path);
    },
  });

  // Half-height hit test: the drop lands before or after the hovered note.
  const edgeAt = (e: React.DragEvent, el: HTMLElement): "top" | "bottom" => {
    const r = el.getBoundingClientRect();
    return e.clientY < r.top + r.height / 2 ? "top" : "bottom";
  };

  const noteDropProps = (n: TreeNote) => ({
    onDragOver: (e: React.DragEvent<HTMLElement>) => {
      if (drag?.kind !== "note") return;
      e.preventDefault();
      const edge = edgeAt(e, e.currentTarget);
      setOverFolder(null);
      setOverNote(cur => (cur && cur.id === n.id && cur.edge === edge ? cur : { id: n.id, edge }));
    },
    onDragLeave: () => setOverNote(cur => (cur && cur.id === n.id ? null : cur)),
    onDrop: (e: React.DragEvent<HTMLElement>) => {
      if (drag?.kind !== "note") return;
      e.preventDefault();
      // Пересчитываем край по координате, а не по состоянию: последний dragover
      // мог не дойти, и тогда вставка ушла бы не туда.
      const after = edgeAt(e, e.currentTarget) === "bottom";
      const list = notesIn(n.folder);
      const pos = list.findIndex(x => x.id === n.id);
      const before = after ? list[pos + 1] : list[pos];
      const id = drag.id;
      clearDrag();
      moveNote(id, n.folder, before ? before.id : null);
    },
  });

  // ── rendering ───────────────────────────────────────────────
  const rowStyle = (o: { active?: boolean; over?: boolean; edge?: "top" | "bottom" | null }): React.CSSProperties => ({
    display: "flex", alignItems: "center", gap: 6, width: "100%",
    padding: "4px 6px", borderRadius: "var(--r-sm)", fontSize: 12.5, textAlign: "left",
    color: o.active ? "var(--t-hi)" : "var(--t-mid)",
    background: o.active ? "var(--accent-dim)" : o.over ? "var(--bg3)" : "transparent",
    border: o.over ? "1px dashed var(--accent)" : "1px solid transparent",
    boxShadow: o.edge === "top" ? "inset 0 2px 0 0 var(--accent)"
      : o.edge === "bottom" ? "inset 0 -2px 0 0 var(--accent)" : undefined,
    cursor: "pointer", minWidth: 0,
  });

  const iconBtn: React.CSSProperties = { width: 20, height: 20, flex: "none" };
  const inlineInput: React.CSSProperties = { flex: 1, minWidth: 0, padding: "3px 7px", fontSize: 12 };

  const renderFolder = (node: Node, depth: number) => {
    const open = !collapsed.has(node.path);
    const edit = editing && editing.path === node.path ? editing : null;
    const childDepth = node.path === "" ? depth : depth + 1;

    return (
      <div key={node.path || "root"}>
        {node.path !== "" && (
          <div style={{ paddingLeft: depth * 11 }}>
            {edit ? (
              <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "2px 0" }}>
                <input className="input" autoFocus value={edit.value}
                  onChange={e => setEditing({ path: node.path, value: e.target.value })}
                  onKeyDown={e => {
                    if (e.key === "Enter") commitRename();
                    if (e.key === "Escape") setEditing(null);
                  }}
                  style={inlineInput} />
                <button className="iconbtn" title="Переименовать" onClick={commitRename}><Check size={13} /></button>
                <button className="iconbtn" title="Отмена" onClick={() => setEditing(null)}><X size={13} /></button>
              </div>
            ) : confirmDel === node.path ? (
              <div style={{
                display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
                padding: "4px 6px", borderRadius: "var(--r-sm)", border: "1px dashed var(--err)",
              }}>
                <span className="micro" style={{ color: "var(--err)" }}>
                  Удалить «{node.name}»? Заметки внутри поднимутся на уровень выше.
                </span>
                <button className="btn btn-sm btn-danger"
                  onClick={() => { setConfirmDel(null); onDeleteFolder({ id: node.id, path: node.path }); }}>
                  Удалить
                </button>
                <button className="btn btn-sm" onClick={() => setConfirmDel(null)}>Отмена</button>
              </div>
            ) : (
              <div
                draggable
                onDragStart={e => {
                  e.dataTransfer.setData("text/plain", node.path);   // Firefox не начнёт drag без данных
                  setDrag({ kind: "folder", path: node.path });
                }}
                onDragEnd={clearDrag}
                {...intoProps(node.path)}
                style={rowStyle({ active: selected === node.path, over: overFolder === node.path })}>
                <button onClick={() => toggle(node.path)} title={open ? "Свернуть" : "Развернуть"}
                  style={{ display: "flex", flex: "none", color: "inherit" }}>
                  {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                </button>
                <button
                  onClick={() => { setSelected(node.path); if (!open) toggle(node.path); }}
                  title={node.path}
                  style={{ display: "flex", alignItems: "center", gap: 5, flex: 1, minWidth: 0, color: "inherit" }}>
                  {open ? <FolderOpen size={13} style={{ flex: "none", color: "var(--t-low)" }} />
                        : <Folder size={13} style={{ flex: "none", color: "var(--t-low)" }} />}
                  <span className="trunc">{node.name}</span>
                  <span className="micro" style={{ marginLeft: "auto", flex: "none" }}>{node.count}</span>
                </button>
                <button className="iconbtn" title="Переименовать папку" style={iconBtn}
                  onClick={() => setEditing({ path: node.path, value: node.name })}><Pencil size={11} /></button>
                <button className="iconbtn" title="Заметка в этой папке" style={iconBtn}
                  onClick={() => onCreateNote(node.path)}><Plus size={12} /></button>
                <button className="iconbtn" title="Удалить папку" style={iconBtn}
                  onClick={() => setConfirmDel(node.path)}><Trash2 size={11} /></button>
              </div>
            )}
          </div>
        )}

        {open && (
          <>
            {node.children.map(c => renderFolder(c, childDepth))}
            {node.notes.map(n => (
              <div key={n.id} style={{ paddingLeft: childDepth * 11 }}>
                <button
                  draggable
                  onDragStart={e => {
                    e.dataTransfer.setData("text/plain", n.id);
                    setDrag({ kind: "note", id: n.id });
                  }}
                  onDragEnd={clearDrag}
                  {...noteDropProps(n)}
                  onClick={() => onOpen(n.id)}
                  title={n.name}
                  style={rowStyle({
                    active: activeId === n.id,
                    edge: overNote && overNote.id === n.id ? overNote.edge : null,
                  })}>
                  <FileText size={12} style={{ flex: "none", color: activeId === n.id ? "var(--accent-hi)" : "var(--t-low)" }} />
                  <span className="trunc">{n.name}</span>
                </button>
              </div>
            ))}
          </>
        )}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {/* Корень — тоже цель для дропа: иначе заметку/папку не вынести наверх. */}
      <div {...intoProps("")}
        style={{
          display: "flex", alignItems: "center", gap: 6, padding: "3px 6px", marginBottom: 2,
          borderRadius: "var(--r-sm)",
          border: overFolder === "" ? "1px dashed var(--accent)" : "1px solid transparent",
          // Корень выбран по умолчанию, поэтому подсветка тише, чем у папок.
          background: overFolder === "" || selected === "" ? "var(--bg3)" : "transparent",
        }}>
        <button className="micro" onClick={() => setSelected("")} title="Корень — сюда создаются новые папки"
          style={{ flex: 1, minWidth: 0, textAlign: "left", color: "inherit" }}>
          Заметки · {tree.count}
        </button>
        <button className="iconbtn" style={iconBtn}
          title={selected ? `Заметка в «${selected}»` : "Заметка в корне"}
          onClick={() => onCreateNote(selected)}><Plus size={12} /></button>
        <button className="iconbtn" style={iconBtn}
          title={selected ? `Папка в «${selected}»` : "Папка в корне"}
          onClick={() => setCreating("")}><FolderPlus size={12} /></button>
      </div>

      {creating !== null && (
        <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "0 0 2px" }}>
          <FolderPlus size={12} style={{ flex: "none", color: "var(--t-low)" }} />
          <input className="input" autoFocus value={creating}
            placeholder={selected ? `Папка в «${selected}»` : "Имя папки"}
            onChange={e => setCreating(e.target.value)}
            onKeyDown={e => {
              if (e.key === "Enter") commitCreate();
              if (e.key === "Escape") setCreating(null);
            }}
            style={inlineInput} />
          <button className="iconbtn" title="Создать" onClick={commitCreate}><Check size={13} /></button>
          <button className="iconbtn" title="Отмена" onClick={() => setCreating(null)}><X size={13} /></button>
        </div>
      )}

      {notes.length === 0 && folders.length === 0
        ? <p className="hint" style={{ padding: "0 6px" }}>Заметок пока нет.</p>
        : renderFolder(tree, 0)}
    </div>
  );
}
