// Folder tree over the note set. Folders have no identity of their own (exactly
// like Obsidian) — the tree is derived from the `folder` path string on each
// note, so "renaming a folder" is a bulk move of the notes under it.
import { useMemo, useState } from "react";
import {
  ChevronRight, ChevronDown, Folder, FolderOpen, FileText, Plus, Pencil, Check, X,
} from "lucide-react";
import { normFolder, type LibItem } from "./api";

export interface TreeNote { id: string; name: string; folder: string }

interface Node {
  path: string;
  name: string;
  children: Node[];
  notes: TreeNote[];
  count: number;   // notes in this folder and every folder under it
}

function buildTree(notes: TreeNote[]): Node {
  const root: Node = { path: "", name: "", children: [], notes: [], count: 0 };
  const byPath = new Map<string, Node>([["", root]]);

  const ensure = (path: string): Node => {
    const found = byPath.get(path);
    if (found) return found;
    const cut = path.lastIndexOf("/");
    const parent = ensure(cut < 0 ? "" : path.slice(0, cut));
    const node: Node = { path, name: path.slice(cut + 1), children: [], notes: [], count: 0 };
    parent.children.push(node);
    byPath.set(path, node);
    return node;
  };

  for (const n of notes) ensure(normFolder(n.folder || "")).notes.push(n);

  const finish = (n: Node): number => {
    n.children.sort((a, b) => a.name.localeCompare(b.name, "ru"));
    n.notes.sort((a, b) => a.name.localeCompare(b.name, "ru"));
    n.count = n.notes.length + n.children.reduce((s, c) => s + finish(c), 0);
    return n.count;
  };
  finish(root);
  return root;
}

export function notesOf(items: LibItem[]): TreeNote[] {
  return items
    .filter(it => it.kind === "note")
    .map(it => ({ id: it.id, name: it.name, folder: normFolder(it.folder || "") }));
}

export function NoteTree({ notes, activeId, onOpen, onCreate, onRenameFolder, onMoveNote }: {
  notes: TreeNote[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onCreate: (folder: string) => void;
  onRenameFolder: (src: string, dst: string) => void;
  onMoveNote: (id: string, folder: string) => void;
}) {
  const tree = useMemo(() => buildTree(notes), [notes]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<{ path: string; value: string } | null>(null);
  const [dropAt, setDropAt] = useState<string | null>(null);

  const toggle = (path: string) => setCollapsed(prev => {
    const next = new Set(prev);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });

  const commitRename = () => {
    if (!editing) return;
    const name = editing.value.trim();
    const cut = editing.path.lastIndexOf("/");
    const parent = cut < 0 ? "" : editing.path.slice(0, cut);
    const dst = normFolder(parent ? `${parent}/${name}` : name);
    setEditing(null);
    if (name && dst !== editing.path) onRenameFolder(editing.path, dst);
  };

  const dropProps = (path: string) => ({
    onDragOver: (e: React.DragEvent) => { e.preventDefault(); setDropAt(path); },
    onDragLeave: () => setDropAt(cur => (cur === path ? null : cur)),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      setDropAt(null);
      const id = e.dataTransfer.getData("text/plain");
      const note = notes.find(n => n.id === id);
      if (note && note.folder !== path) onMoveNote(id, path);
    },
  });

  const rowStyle = (active: boolean, over: boolean): React.CSSProperties => ({
    display: "flex", alignItems: "center", gap: 6, width: "100%",
    padding: "4px 6px", borderRadius: "var(--r-sm)", fontSize: 12.5, textAlign: "left",
    color: active ? "var(--t-hi)" : "var(--t-mid)",
    background: active ? "var(--accent-dim)" : over ? "var(--bg3)" : "transparent",
    border: over ? "1px dashed var(--accent-line)" : "1px solid transparent",
    cursor: "pointer", minWidth: 0,
  });

  const renderFolder = (node: Node, depth: number) => {
    const open = !collapsed.has(node.path);
    const edit = editing && editing.path === node.path ? editing : null;
    return (
      <div key={node.path || "root"}>
        {node.path !== "" && (
          <div style={{ paddingLeft: depth * 11 }} {...dropProps(node.path)}>
            {edit ? (
              <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "2px 0" }}>
                <input className="input" autoFocus value={edit.value}
                  onChange={e => setEditing({ path: node.path, value: e.target.value })}
                  onKeyDown={e => {
                    if (e.key === "Enter") commitRename();
                    if (e.key === "Escape") setEditing(null);
                  }}
                  style={{ padding: "3px 7px", fontSize: 12 }} />
                <button className="iconbtn" title="Переименовать" onClick={commitRename}><Check size={13} /></button>
                <button className="iconbtn" title="Отмена" onClick={() => setEditing(null)}><X size={13} /></button>
              </div>
            ) : (
              <div style={rowStyle(false, dropAt === node.path)}>
                <button onClick={() => toggle(node.path)} title={open ? "Свернуть" : "Развернуть"}
                  style={{ display: "flex", alignItems: "center", gap: 5, flex: 1, minWidth: 0, color: "inherit" }}>
                  {open ? <ChevronDown size={12} style={{ flex: "none" }} /> : <ChevronRight size={12} style={{ flex: "none" }} />}
                  {open ? <FolderOpen size={13} style={{ flex: "none", color: "var(--t-low)" }} />
                        : <Folder size={13} style={{ flex: "none", color: "var(--t-low)" }} />}
                  <span className="trunc">{node.name}</span>
                  <span className="micro" style={{ marginLeft: "auto", flex: "none" }}>{node.count}</span>
                </button>
                <button className="iconbtn" title="Переименовать папку" style={{ width: 22, height: 22 }}
                  onClick={() => setEditing({ path: node.path, value: node.name })}><Pencil size={11} /></button>
                <button className="iconbtn" title="Заметка в этой папке" style={{ width: 22, height: 22 }}
                  onClick={() => onCreate(node.path)}><Plus size={12} /></button>
              </div>
            )}
          </div>
        )}

        {open && (
          <>
            {node.children.map(c => renderFolder(c, node.path === "" ? depth : depth + 1))}
            {node.notes.map(n => (
              <div key={n.id} style={{ paddingLeft: (node.path === "" ? depth : depth + 1) * 11 }}>
                <button
                  draggable
                  onDragStart={e => e.dataTransfer.setData("text/plain", n.id)}
                  onClick={() => onOpen(n.id)}
                  title={n.name}
                  style={rowStyle(activeId === n.id, false)}>
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
      {/* The root is a drop target too — dragging a note here moves it out of
          its folder, which is otherwise impossible without typing an empty path. */}
      <div {...dropProps("")}
        style={{
          display: "flex", alignItems: "center", gap: 6, padding: "3px 6px", marginBottom: 2,
          borderRadius: "var(--r-sm)",
          border: dropAt === "" ? "1px dashed var(--accent-line)" : "1px solid transparent",
          background: dropAt === "" ? "var(--bg3)" : "transparent",
        }}>
        <span className="micro">Заметки · {tree.count}</span>
        <button className="iconbtn" title="Заметка в корне" style={{ width: 22, height: 22, marginLeft: "auto" }}
          onClick={() => onCreate("")}><Plus size={12} /></button>
      </div>

      {tree.count === 0
        ? <p className="hint" style={{ padding: "0 6px" }}>Заметок пока нет.</p>
        : renderFolder(tree, 0)}
    </div>
  );
}
