import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Loader2, Upload, FileText, Download, Trash2, Plus, PanelLeft,
  ChevronDown, ChevronRight, NotebookPen,
} from "lucide-react";
import { toast } from "./infra/Toast";
import { fmtSize } from "./common/MediaDrop";
import { libraryApi, folderList, normFolder, type Graph, type LibItem, type LibNote } from "./library/api";
import { NoteTree, notesOf } from "./library/NoteTree";
import { NoteEditor } from "./library/NoteEditor";
import { Backlinks } from "./library/Backlinks";
import type { NoteRef } from "./library/markdown";

// Wave-5 Plan C (scoped) — «Библиотека»: files + markdown notes, now in the
// Obsidian layout: folder tree ▸ note editor with live preview ▸ backlinks.
// Wiki-links (`[[Заметка]]`) and media embeds (`![[media-id]]`) are resolved by
// the backend/renderer pair — see library/markdown.ts.
export function Library() {
  const [items, setItems] = useState<LibItem[] | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [note, setNote] = useState<LibNote | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [filesOpen, setFilesOpen] = useState(true);
  const [narrow, setNarrow] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);

  // ≤820px is the panel's mobile breakpoint (see .ni-sidebar/.ni-tabbar rules).
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 820px)");
    const sync = () => setNarrow(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const load = useCallback(async () => {
    try {
      const [list, g] = await Promise.all([libraryApi.list(), libraryApi.graph()]);
      setItems(list);
      setGraph(g);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка загрузки", "error");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const openNote = useCallback(async (id: string) => {
    try {
      setNote(await libraryApi.getNote(id));
      setTreeOpen(false);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Заметка не найдена", "error");
      void load();
    }
  }, [load]);

  const createNote = useCallback(async (name: string, folder: string) => {
    try {
      const created = await libraryApi.createNote({
        name: name.trim() || "Новая заметка", text: "", folder: normFolder(folder),
      });
      await load();
      await openNote(created.id);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось создать заметку", "error");
    }
  }, [load, openNote]);

  /** A `[[link]]` pointing at a note that does not exist yet. */
  const createFromLink = useCallback((name: string) => {
    if (!name.trim()) return;
    if (!confirm(`Заметки «${name}» ещё нет. Создать её?`)) return;
    void createNote(name, note?.folder || "");
  }, [createNote, note]);

  // The store rewrites `[[links]]` in OTHER notes when one is renamed, so both
  // the list and the graph have to come back from the server after a save.
  const onSaved = useCallback(() => { void load(); }, [load]);

  const deleteNote = useCallback(async () => {
    if (!note) return;
    try {
      await libraryApi.remove(note.id);
      setNote(null);
      toast("Заметка удалена", "success");
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось удалить", "error");
    }
  }, [note, load]);

  const renameFolder = useCallback(async (src: string, dst: string) => {
    try {
      const res = await libraryApi.renameFolder(src, dst);
      toast(`Перемещено заметок: ${res.moved}`, "success");
      await load();
      if (note && (note.folder === src || note.folder.startsWith(src + "/"))) await openNote(note.id);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось переименовать", "error");
    }
  }, [load, note, openNote]);

  const moveNote = useCallback(async (id: string, folder: string) => {
    // The open note is edited live and autosaves its own folder field — writing
    // it from here would race that save and could resurrect the old folder.
    if (note?.id === id) {
      toast("Открытая заметка переносится полем «Папка»", "info");
      return;
    }
    try {
      const full = await libraryApi.getNote(id);
      await libraryApi.updateNote(id, { name: full.name, text: full.text, folder });
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось перенести", "error");
    }
  }, [note, load]);

  const upload = async (f: File) => {
    setBusy(true);
    try {
      await libraryApi.upload(f);
      await load();
      toast("Загружено", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "error");
    } finally {
      setBusy(false);
    }
  };

  const delFile = async (id: string) => {
    if (confirmDel !== id) {
      setConfirmDel(id);
      setTimeout(() => setConfirmDel(c => (c === id ? null : c)), 3000);
      return;
    }
    setConfirmDel(null);
    try {
      await libraryApi.remove(id);
      await load();
      toast("Удалено", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "error");
    }
  };

  const download = async (it: LibItem) => {
    try { await libraryApi.download(it); }
    catch { toast("Не удалось скачать", "error"); }
  };

  // Memoised: `noteRefs` is a render dependency of the markdown pipeline, so a
  // fresh array on every keystroke would re-render the preview for nothing.
  const noteRefs = useMemo<NoteRef[]>(
    () => (items ?? []).filter(i => i.kind === "note").map(i => ({ id: i.id, name: i.name })),
    [items],
  );
  const names = useMemo(() => new Map(noteRefs.map(n => [n.id, n.name])), [noteRefs]);
  const treeNotes = useMemo(() => notesOf(items ?? []), [items]);
  const folders = useMemo(() => folderList(items ?? []), [items]);
  const files = (items ?? []).filter(i => i.kind === "file");

  const aside = (
    <aside style={{
      display: "flex", flexDirection: "column", gap: 10, minHeight: 0,
      maxHeight: narrow ? "46vh" : undefined,
      overflowY: "auto",
    }}>
      <div className="card" style={{ padding: 8 }}>
        {items === null ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 8, fontSize: 12.5, color: "var(--t-low)" }}>
            <Loader2 size={14} className="animate-spin" /> Загрузка…
          </div>
        ) : (
          <NoteTree notes={treeNotes} activeId={note?.id ?? null} onOpen={id => { void openNote(id); }}
            onCreate={folder => { void createNote("Новая заметка", folder); }}
            onRenameFolder={(src, dst) => { void renameFolder(src, dst); }}
            onMoveNote={(id, folder) => { void moveNote(id, folder); }} />
        )}
      </div>

      {/* Library files stay a separate section: they are attachments of the
          library itself (pdf/doc/xlsx), not the media embedded into notes. */}
      <div className="card" style={{ padding: 8 }}>
        <button onClick={() => setFilesOpen(o => !o)}
          style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", padding: "3px 6px", color: "var(--t-low)" }}>
          {filesOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span className="micro">Файлы · {files.length}</span>
        </button>
        {filesOpen && (
          <>
            {files.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 4 }}>
                {files.map(it => (
                  <div key={it.id} style={{
                    display: "flex", alignItems: "center", gap: 6, padding: "3px 6px",
                    borderRadius: "var(--r-sm)", minWidth: 0,
                  }}>
                    <FileText size={12} style={{ color: "var(--t-low)", flex: "none" }} />
                    <span className="trunc" style={{ flex: 1, fontSize: 12.5, color: "var(--t-mid)" }} title={it.name}>{it.name}</span>
                    <span className="micro" style={{ flex: "none" }}>{it.size == null ? "" : fmtSize(it.size)}</span>
                    <button className="iconbtn" title="Скачать" style={{ width: 22, height: 22 }}
                      onClick={() => { void download(it); }}><Download size={12} /></button>
                    <button className="iconbtn" title={confirmDel === it.id ? "Нажмите ещё раз" : "Удалить"}
                      style={{ width: 22, height: 22, color: confirmDel === it.id ? "var(--err)" : undefined }}
                      onClick={() => { void delFile(it.id); }}><Trash2 size={12} /></button>
                  </div>
                ))}
              </div>
            )}
            <p className="hint" style={{ padding: "0 6px" }}>
              {files.length === 0 && "Файлов нет — кнопка «Загрузить» вверху. "}
              pdf/doc/xlsx и другие, до 25 МБ. Элементов: {(items ?? []).length}/500.
            </p>
          </>
        )}
      </div>
    </aside>
  );

  return (
    <div className="ni-pagebody" style={{
      flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 12,
      padding: 20, overflow: narrow ? "auto" : "hidden",
    }}>
      <div className="ni-pagehead" style={{ display: "flex", alignItems: "flex-start", gap: 10, flex: "none" }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 700, color: "var(--t-hi)" }}>Библиотека</h2>
          <p className="hint">
            Заметки в Markdown: <code>[[Ссылка]]</code> на другую заметку, <code>![[media-id]]</code> — медиа.
            Файлы и заметки приватны для вашего аккаунта.
          </p>
        </div>
        <div className="ni-pagehead-actions" style={{ marginLeft: "auto", display: "flex", gap: 6, flexWrap: "wrap" }}>
          {narrow && (
            <button className="btn btn-sm" onClick={() => setTreeOpen(o => !o)}>
              <PanelLeft size={13} /> Заметки
            </button>
          )}
          <button className="btn btn-sm" onClick={() => { void createNote("Новая заметка", note?.folder || ""); }}>
            <Plus size={13} /> Заметка
          </button>
          <label className="btn btn-sm" style={{ opacity: busy ? 0.5 : 1, cursor: busy ? "not-allowed" : "pointer" }}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} Загрузить
            <input type="file" style={{ display: "none" }} disabled={busy}
              onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.currentTarget.value = ""; }} />
          </label>
        </div>
      </div>

      <div style={{
        flex: 1, minHeight: 0, display: "grid", gap: 14,
        gridTemplateColumns: narrow ? "minmax(0,1fr)" : "268px minmax(0,1fr)",
        alignItems: "stretch",
      }}>
        {(!narrow || treeOpen) && aside}

        <section style={{
          display: "flex", flexDirection: "column", gap: 10, minWidth: 0,
          minHeight: narrow ? 460 : 0,
        }}>
          {note ? (
            <>
              {/* Keyed by id AND folder: a folder rename re-opens the note with a
                  new path, and the editor holds the folder in its own state — a
                  plain id key would keep showing (and re-saving) the old one. */}
              <NoteEditor key={`${note.id}:${note.folder}`} note={note} notes={noteRefs} folders={folders}
                onSaved={onSaved} onOpenNote={id => { void openNote(id); }}
                onCreateNote={createFromLink} onDelete={() => { void deleteNote(); }} />
              <Backlinks graph={graph} noteId={note.id} names={names}
                onOpen={id => { void openNote(id); }} onCreate={createFromLink} />
            </>
          ) : (
            <div className="card" style={{
              flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
              justifyContent: "center", gap: 10, padding: 24, textAlign: "center",
            }}>
              <NotebookPen size={26} style={{ color: "var(--t-faint)" }} />
              <p className="hint" style={{ marginTop: 0 }}>
                {items === null ? "Загрузка…"
                  : treeNotes.length === 0 ? "Заметок пока нет — создайте первую."
                  : "Выберите заметку слева или создайте новую."}
              </p>
              <button className="btn btn-primary btn-sm"
                onClick={() => { void createNote("Новая заметка", ""); }}>
                <Plus size={13} /> Новая заметка
              </button>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
