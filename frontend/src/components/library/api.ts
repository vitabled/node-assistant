// Typed client for /api/library (Obsidian-style notes + library files).
// Errors throw with the backend `detail` (shown as toasts). The account bearer
// token is attached globally by the auth fetch interceptor (auth/apiClient.ts).

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/library${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    throw Object.assign(new Error(msg), { status: res.status });
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

/** List row — the backend never ships the note body here.
 *  A `folder` row is the store's way of keeping an EMPTY folder alive: a folder
 *  is otherwise only implied by the notes inside it, so a freshly created one
 *  would vanish on the next reload. Its path lives in `path`, not `name`. */
export interface LibItem {
  id: string;
  kind: "note" | "file" | "folder";
  name?: string;
  folder?: string;
  order?: number;
  path?: string;
  filename?: string;
  mime?: string;
  size?: number;
  created_at: number;
  updated_at?: number;
}

export interface LibNote {
  id: string;
  kind: "note";
  name: string;
  folder: string;
  text: string;
  created_at: number;
  updated_at: number;
}

export interface NoteBody {
  name: string;
  text: string;
  folder: string;
}

/** `/graph` — links resolved by name on read, so a link to a note that does not
 *  exist yet arrives as `unresolved` instead of being dropped. */
export interface GraphNode {
  name: string;
  folder: string;
  out: string[];
  in: string[];
  unresolved: string[];
}
export type Graph = Record<string, GraphNode>;

/** One line of a `/reorder` call. `folder` omitted → leave the note where it is. */
export interface ReorderRow {
  id: string;
  folder?: string;
  order: number;
}

export const libraryApi = {
  list: () => req<LibItem[]>(""),
  graph: () => req<Graph>("/graph"),
  getNote: (id: string) => req<LibNote>(`/notes/${id}`),
  createNote: (b: NoteBody) => req<LibItem>("/notes", { method: "POST", body: JSON.stringify(b) }),
  updateNote: (id: string, b: NoteBody) =>
    req<LibItem>(`/notes/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  createFolder: (path: string) =>
    req<LibItem>("/folders", { method: "POST", body: JSON.stringify({ path }) }),
  renameFolder: (src: string, dst: string) =>
    req<{ ok: boolean; moved: number }>("/folders/rename", {
      method: "POST", body: JSON.stringify({ src, dst }),
    }),
  /** One call carries the WHOLE recomputed order of the touched folder — sending
   *  deltas would leave the list wrong if any single row failed to apply. */
  reorder: (items: ReorderRow[]) =>
    req<{ ok: boolean; moved: number }>("/reorder", {
      method: "POST", body: JSON.stringify({ items }),
    }),
  remove: (id: string) => req<void>(`/${id}`, { method: "DELETE" }),

  // Multipart: no JSON Content-Type here — the boundary is set by the browser.
  async upload(file: File): Promise<LibItem> {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/library/upload", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === "string" ? err.detail : "Не удалось загрузить");
    }
    return res.json();
  },

  /** Текст файла для просмотра в панели.
   *
   *  Сервер отдаёт файлы библиотеки ТОЛЬКО как вложение (`Content-Disposition:
   *  attachment`) — открыть ссылку в новой вкладке нельзя by design: чужой HTML,
   *  отрисованный на нашем origin, это хранимая XSS. Поэтому текст забираем
   *  запросом и показываем сами: HTML — в песочнице (`sandbox=""`), остальное —
   *  как текст. */
  async text(item: LibItem): Promise<string> {
    const res = await fetch(`/api/library/files/${item.id}`);
    if (!res.ok) throw new Error("Не удалось открыть файл");
    return res.text();
  },

  /** Library files are served as attachments — fetch, then hand the blob over. */
  async download(item: LibItem): Promise<void> {
    const res = await fetch(`/api/library/files/${item.id}`);
    if (!res.ok) throw new Error("Не удалось скачать");
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = item.filename || item.name || "file";
    a.click();
    URL.revokeObjectURL(url);
  },
};

/** Mirrors `library_store.norm_folder`: `"/Инфра//Провайдеры/"` → `"Инфра/Провайдеры"`.
 *  A folder is only a path string on the note (no identity of its own), so the
 *  tree has to normalise the same way the store does or two spellings of one
 *  folder would show up as two branches. */
const MAX_FOLDER_DEPTH = 8;
export function normFolder(folder: string): string {
  const parts = (folder || "")
    .replace(/\\/g, "/")
    .split("/")
    .map(p => p.trim())
    .filter(p => p && p !== "." && p !== "..");
  return parts.slice(0, MAX_FOLDER_DEPTH).join("/").slice(0, 300);
}

/** Parent of a folder path («Инфра/Провайдеры» → «Инфра», верхний уровень → «»). */
export function parentFolder(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}
