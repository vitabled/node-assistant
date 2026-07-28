// One note: title + markdown source, with a live preview and media dropped/pasted
// straight into the text at the caret.
//
// The folder is NOT edited here — it is the note's position in the tree. It still
// rides along on every PUT (the store treats a missing folder as «в корень»), and
// it is read from the prop through a ref at save time: dragging the OPEN note into
// another folder updates the prop, and a stale captured value would put the note
// straight back where it was.
//
// Saving is debounced (800 ms) and also flushed on unmount, because switching
// notes remounts this component — without the flush the last keystrokes before a
// click on another note would be lost.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Pencil, Eye, Columns2, ImagePlus, Loader2, Save, Trash2, Check, AlertTriangle,
} from "lucide-react";
import { toast } from "../infra/Toast";
import { uploadMedia } from "../common/MediaDrop";
import { libraryApi, type LibItem, type LibNote } from "./api";
import { cacheMedia, type NoteRef } from "./markdown";
import { MarkdownView } from "./MarkdownView";

type Mode = "edit" | "split" | "preview";
type SaveState = "saved" | "dirty" | "saving" | "error";

const MODES: { v: Mode; l: string; icon: React.ReactNode }[] = [
  { v: "edit", l: "Редактор", icon: <Pencil size={12} /> },
  { v: "split", l: "Разделённый", icon: <Columns2 size={12} /> },
  { v: "preview", l: "Просмотр", icon: <Eye size={12} /> },
];

/** `![[id|подпись]]` — the caption keeps the raw markdown readable. Characters
 *  that would break the embed syntax are dropped rather than escaped. */
function embedSnippet(id: string, name: string): string {
  const caption = (name || "").replace(/[[\]|\r\n]+/g, " ").trim();
  return caption ? `![[${id}|${caption}]]` : `![[${id}]]`;
}

export function NoteEditor({ note, notes, onSaved, onOpenNote, onCreateNote, onDelete }: {
  note: LibNote;
  notes: NoteRef[];
  onSaved: (item: LibItem) => void;
  onOpenNote: (id: string) => void;
  onCreateNote: (name: string) => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(note.name);
  const [text, setText] = useState(note.text || "");
  const [mode, setMode] = useState<Mode>("split");
  const [state, setState] = useState<SaveState>("saved");
  const [uploading, setUploading] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);

  const ta = useRef<HTMLTextAreaElement | null>(null);
  const filePick = useRef<HTMLInputElement | null>(null);
  const stored = useRef({ name: note.name, text: note.text || "" });
  const latest = useRef(stored.current);
  latest.current = { name, text };
  const folderRef = useRef(note.folder || "");
  folderRef.current = note.folder || "";

  const save = useCallback(async () => {
    const cur = latest.current;
    const prev = stored.current;
    if (cur.name === prev.name && cur.text === prev.text) {
      // Nothing to write — also the path StrictMode's double-mount takes, and
      // the one where the user undid their edit; both must clear the badge.
      setState("saved");
      return;
    }
    if (!cur.name.trim()) { setState("error"); return; }   // the store requires a name
    setState("saving");
    try {
      const item = await libraryApi.updateNote(note.id, {
        name: cur.name.trim(), text: cur.text, folder: folderRef.current,
      });
      stored.current = { ...cur, name: cur.name.trim() };
      setState("saved");
      onSaved(item);
    } catch (e) {
      setState("error");
      toast(e instanceof Error ? e.message : "Не удалось сохранить", "error");
    }
  }, [note.id, onSaved]);

  // Kept in a ref so the debounce below depends only on the note's content —
  // a parent re-render must not restart the timer.
  const saveRef = useRef(save);
  saveRef.current = save;

  useEffect(() => {
    // Compared against the last stored values rather than a "first render" flag:
    // StrictMode mounts twice, and a flag would report a freshly opened note as
    // dirty on the second mount.
    const prev = stored.current;
    if (name === prev.name && text === prev.text) return;
    setState("dirty");
    const t = setTimeout(() => { void saveRef.current(); }, 800);
    return () => clearTimeout(t);
  }, [name, text]);

  useEffect(() => () => { void saveRef.current(); }, []);

  // ── media at the caret ──────────────────────────────────────
  const insertAt = (start: number, end: number, snippet: string) => {
    setText(prev => {
      const a = Math.min(Math.max(start, 0), prev.length);
      const b = Math.min(Math.max(end, a), prev.length);
      return prev.slice(0, a) + snippet + prev.slice(b);
    });
    // After React commits the new value: focus back, caret past the insert.
    requestAnimationFrame(() => {
      const el = ta.current;
      if (!el) return;
      const pos = Math.min(start, el.value.length) + snippet.length;
      el.focus();
      el.setSelectionRange(pos, pos);
    });
  };

  const uploadInto = async (files: File[], start: number, end: number) => {
    if (files.length === 0) return;
    setUploading(true);
    const parts: string[] = [];
    for (const f of files) {
      try {
        const m = await uploadMedia(f);
        cacheMedia(m);                       // so the preview resolves it at once
        parts.push(embedSnippet(m.id, m.name));
      } catch (e) {
        toast(e instanceof Error ? e.message : `Не удалось загрузить ${f.name}`, "error");
      }
    }
    setUploading(false);
    if (parts.length) insertAt(start, end, parts.join("\n"));
  };

  const caret = () => {
    const el = ta.current;
    return el ? [el.selectionStart, el.selectionEnd] as const : [text.length, text.length] as const;
  };

  const badge = {
    saved: { icon: <Check size={12} />, label: "Сохранено", color: "var(--ok)" },
    dirty: { icon: <Save size={12} />, label: "Есть изменения", color: "var(--t-low)" },
    saving: { icon: <Loader2 size={12} className="animate-spin" />, label: "Сохраняю…", color: "var(--t-low)" },
    error: { icon: <AlertTriangle size={12} />, label: "Не сохранено", color: "var(--err)" },
  }[state];

  const editor = (
    <textarea
      ref={ta}
      className="input"
      value={text}
      onChange={e => setText(e.target.value)}
      onDragOver={e => { if (Array.from(e.dataTransfer.types).includes("Files")) e.preventDefault(); }}
      onDrop={e => {
        const files = Array.from(e.dataTransfer.files);
        if (files.length === 0) return;      // plain text drops keep working
        e.preventDefault();
        const el = e.currentTarget;
        void uploadInto(files, el.selectionStart, el.selectionEnd);
      }}
      onPaste={e => {
        const files = Array.from(e.clipboardData.files);
        if (files.length === 0) return;      // pasting text keeps working
        e.preventDefault();
        const el = e.currentTarget;
        void uploadInto(files, el.selectionStart, el.selectionEnd);
      }}
      placeholder="Markdown. [[Другая заметка]] — ссылка, ![[media-id]] — медиа. Файл можно перетащить сюда."
      spellCheck={false}
      style={{
        flex: 1, minHeight: 0, width: "100%", resize: "none", fontFamily: "var(--mono)",
        fontSize: 12.5, lineHeight: 1.6, padding: "10px 12px",
      }}
    />
  );

  const preview = (
    <div style={{
      flex: 1, minHeight: 0, overflowY: "auto", padding: "10px 14px",
      background: "var(--bg2)", border: "1px solid var(--line-soft)", borderRadius: "var(--r-md)",
    }}>
      <MarkdownView text={text} notes={notes} onOpenNote={onOpenNote} onCreateNote={onCreateNote} />
    </div>
  );

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Title */}
      <input className="input" value={name} onChange={e => setName(e.target.value)}
        placeholder="Название заметки"
        style={{ fontSize: 14.5, fontWeight: 600, color: "var(--t-hi)" }} />

      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div className="seg mini" style={{ flex: "none" }}>
          {MODES.map(m => (
            <button key={m.v} className={mode === m.v ? "on" : ""} onClick={() => setMode(m.v)}
              style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              {m.icon} {m.l}
            </button>
          ))}
        </div>

        <button className="btn btn-sm" disabled={uploading}
          title="Загрузить и вставить в текст на месте курсора"
          onClick={() => filePick.current?.click()}>
          {uploading ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />} Вставить медиа
        </button>
        <input ref={filePick} type="file" multiple hidden
          accept="image/png,image/jpeg,image/gif,image/webp,image/avif,image/svg+xml,application/pdf,video/mp4,video/webm"
          onChange={e => {
            const files = Array.from(e.target.files ?? []);
            // The textarea keeps its selection while blurred, so the insert
            // still lands where the user left the caret.
            const [selStart, selEnd] = caret();
            e.target.value = "";
            void uploadInto(files, selStart, selEnd);
          }} />

        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, color: badge.color }}>
          {badge.icon} {state === "error" && !name.trim() ? "Нужно название" : badge.label}
        </span>

        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button className="btn btn-sm" onClick={() => { void save(); }} disabled={state === "saving"}>
            <Save size={13} /> Сохранить
          </button>
          <button className={`btn btn-sm ${confirmDel ? "btn-danger" : ""}`}
            title={confirmDel ? "Нажмите ещё раз" : "Удалить заметку"}
            onClick={() => {
              if (!confirmDel) {
                setConfirmDel(true);
                setTimeout(() => setConfirmDel(false), 3000);
                return;
              }
              setConfirmDel(false);
              // Nothing to flush on unmount for a note that no longer exists.
              stored.current = { ...latest.current };
              onDelete();
            }}>
            <Trash2 size={13} /> {confirmDel ? "Точно?" : "Удалить"}
          </button>
        </div>
      </div>

      {/* Body */}
      {mode === "split" ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2" style={{ flex: 1, minHeight: 0 }}>
          <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>{editor}</div>
          <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>{preview}</div>
        </div>
      ) : mode === "edit" ? editor : preview}
    </div>
  );
}
