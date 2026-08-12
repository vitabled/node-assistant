import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Loader2, Upload, FileText, Download, Trash2, Plus, PanelLeft,
  ChevronDown, ChevronRight, NotebookPen, FolderOpen,
} from "lucide-react";
import { toast } from "./infra/Toast";
import { fmtSize } from "./common/MediaDrop";
import {
  libraryApi, normFolder, parentFolder,
  type Graph, type LibItem, type LibNote, type ReorderRow,
} from "./library/api";
import { NoteTree, notesOf, foldersOf } from "./library/NoteTree";
import { NoteEditor } from "./library/NoteEditor";
import { Backlinks } from "./library/Backlinks";
import { FileViewer } from "./library/FileViewer";
import type { NoteRef } from "./library/markdown";

// Wave-5 Plan C (scoped) — «Библиотека»: files + markdown notes, now in the
// Obsidian layout: folder tree ▸ note editor with live preview ▸ backlinks.
// Wiki-links (`[[Заметка]]`) and media embeds (`![[media-id]]`) are resolved by
// the backend/renderer pair — see library/markdown.ts.

// Одна строка файла в секции «Файлы» (вынесена: теперь рендерится и плоско,
// и внутри групп-папок «Копии сайта»).
function FileRow({ it, viewing, onOpen, onDownload, onDelete, confirmDel }: {
  it: LibItem; viewing: LibItem | null;
  onOpen: () => void; onDownload: () => void; onDelete: () => void;
  confirmDel: string | null;
}) {
  return (
    <div
      role="button" tabIndex={0}
      onClick={onOpen}
      onKeyDown={e => { if (e.key === "Enter") onOpen(); }}
      title="Открыть"
      className={viewing?.id === it.id ? "active" : undefined}
      style={{
        display: "flex", alignItems: "center", gap: 6, padding: "3px 6px",
        borderRadius: "var(--r-sm)", minWidth: 0, cursor: "pointer",
        background: viewing?.id === it.id ? "var(--bg3)" : undefined,
      }}>
      <FileText size={12} style={{ color: "var(--t-low)", flex: "none" }} />
      <span className="trunc" style={{ flex: 1, fontSize: 12.5, color: "var(--t-mid)" }} title={it.name}>{it.name}</span>
      <span className="micro" style={{ flex: "none" }}>{it.size == null ? "" : fmtSize(it.size)}</span>
      <button className="iconbtn" title="Скачать" style={{ width: 22, height: 22 }}
        onClick={e => { e.stopPropagation(); onDownload(); }}><Download size={12} /></button>
      <button className="iconbtn" title={confirmDel === it.id ? "Нажмите ещё раз" : "Удалить"}
        style={{ width: 22, height: 22, color: confirmDel === it.id ? "var(--err)" : undefined }}
        onClick={e => { e.stopPropagation(); onDelete(); }}><Trash2 size={12} /></button>
    </div>
  );
}

export function Library() {
  const [items, setItems] = useState<LibItem[] | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [note, setNote] = useState<LibNote | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [filesOpen, setFilesOpen] = useState(true);
  const [viewing, setViewing] = useState<LibItem | null>(null);
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
      setViewing(null);   // правая область показывает что-то одно
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

  /** Follow a moved subtree with the OPEN note: the editor takes the folder from
   *  its prop on every save, so patching it here (instead of re-fetching) keeps
   *  the next autosave from writing the note back to the old path. Mirrors the
   *  store's `_move_subtree`. */
  const rebaseOpenNote = useCallback((src: string, dst: string) => {
    setNote(cur => {
      if (!cur) return cur;
      const at = cur.folder || "";
      if (at !== src && !at.startsWith(src + "/")) return cur;
      return { ...cur, folder: normFolder(dst + at.slice(src.length)) };
    });
  }, []);

  const createFolder = useCallback(async (path: string) => {
    try {
      await libraryApi.createFolder(path);
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось создать папку", "error");
    }
  }, [load]);

  const renameFolder = useCallback(async (src: string, dst: string) => {
    try {
      await libraryApi.renameFolder(src, dst);
      rebaseOpenNote(src, dst);
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось переименовать", "error");
    }
  }, [load, rebaseOpenNote]);

  const deleteFolder = useCallback(async (f: { id: string | null; path: string }) => {
    const up = parentFolder(f.path);
    try {
      // Папка без своей записи существует только за счёт заметок внутри —
      // поднять их в родителя и ЕСТЬ её удаление, отдельного id тут нет.
      if (f.id) await libraryApi.remove(f.id);
      else await libraryApi.renameFolder(f.path, up);
      rebaseOpenNote(f.path, up);
      await load();
      toast("Папка удалена", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось удалить папку", "error");
    }
  }, [load, rebaseOpenNote]);

  const reorder = useCallback(async (rows: ReorderRow[]) => {
    try {
      await libraryApi.reorder(rows);
      setNote(cur => {
        if (!cur) return cur;
        const row = rows.find(r => r.id === cur.id);
        if (!row || row.folder === undefined || row.folder === cur.folder) return cur;
        return { ...cur, folder: row.folder };
      });
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось перенести", "error");
    }
  }, [load]);

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
    () => (items ?? []).filter(i => i.kind === "note").map(i => ({ id: i.id, name: i.name || "" })),
    [items],
  );
  const names = useMemo(() => new Map(noteRefs.map(n => [n.id, n.name])), [noteRefs]);
  const treeNotes = useMemo(() => notesOf(items ?? []), [items]);
  const treeFolders = useMemo(() => foldersOf(items ?? []), [items]);
  const files = (items ?? []).filter(i => i.kind === "file");

  // Файлы с папкой («Сайты/<host>-<дата>» из «Копии сайта») группируются по ней;
  // без папки — плоский список, как раньше.
  const fileGroups = useMemo(() => {
    const byFolder = new Map<string, LibItem[]>();
    const flat: LibItem[] = [];
    for (const it of files) {
      const f = (it.folder || "").trim();
      if (!f) { flat.push(it); continue; }
      const arr = byFolder.get(f);
      if (arr) arr.push(it); else byFolder.set(f, [it]);
    }
    const groups = [...byFolder.entries()]
      .sort((a, b) => b[0].localeCompare(a[0]))        // свежие сайты сверху
      .map(([folder, items2]) => ({ folder, items: items2 }));
    return flat.length ? [{ folder: "", items: flat }, ...groups] : groups;
  }, [files]);

  const delFileGroup = async (folder: string) => {
    if (!window.confirm(`Удалить группу «${folder}» со всеми файлами?`)) return;
    try {
      await fetch(`/api/library/files-by-folder?folder=${encodeURIComponent(folder)}`,
        { method: "DELETE" }).then(r => r.json());
      await load();
      toast("Группа удалена", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка удаления группы", "error");
    }
  };

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
          <NoteTree notes={treeNotes} folders={treeFolders} activeId={note?.id ?? null}
            onOpen={id => { void openNote(id); }}
            onCreateNote={folder => { void createNote("Новая заметка", folder); }}
            onCreateFolder={path => { void createFolder(path); }}
            onRenameFolder={(src, dst) => { void renameFolder(src, dst); }}
            onDeleteFolder={f => { void deleteFolder(f); }}
            onReorder={rows => { void reorder(rows); }} />
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
                {/* Файлы с папкой (напр. скопированные сайты из «Копии сайта»)
                    группируются по ней; плоские — как раньше, по одному списку. */}
                {fileGroups.map(g => g.folder === "" ? g.items.map(it => (
                  <FileRow key={it.id} it={it} viewing={viewing}
                    onOpen={() => { setNote(null); setViewing(it); }}
                    onDownload={() => { void download(it); }}
                    onDelete={() => { void delFile(it.id); }}
                    confirmDel={confirmDel} />
                )) : (
                  <div key={g.folder} style={{ marginTop: 2 }}>
                    <div style={{
                      display: "flex", alignItems: "center", gap: 6, padding: "3px 6px",
                      color: "var(--t-low)",
                    }}>
                      <FolderOpen size={12} style={{ flex: "none" }} />
                      <span className="micro trunc" style={{ flex: 1 }} title={g.folder}>{g.folder} · {g.items.length}</span>
                      <button className="iconbtn danger" title="Удалить группу целиком"
                        style={{ width: 20, height: 20 }}
                        onClick={() => { void delFileGroup(g.folder); }}>
                        <Trash2 size={11} />
                      </button>
                    </div>
                    {g.items.map(it => (
                      <FileRow key={it.id} it={it} viewing={viewing}
                        onOpen={() => { setNote(null); setViewing(it); }}
                        onDownload={() => { void download(it); }}
                        onDelete={() => { void delFile(it.id); }}
                        confirmDel={confirmDel} />
                    ))}
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
          {viewing ? (
            <FileViewer item={viewing} onClose={() => setViewing(null)}
              onDownload={() => { void download(viewing); }} />
          ) : note ? (
            <>
              {/* Keyed by id only: the folder now travels as a prop the editor
                  re-reads on every save, so a move must NOT remount (a remount
                  would flush the outgoing instance with the pre-move path). */}
              <NoteEditor key={note.id} note={note} notes={noteRefs}
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
