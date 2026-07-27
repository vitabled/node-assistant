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

/** List row — the backend never ships the note body here. */
export interface LibItem {
  id: string;
  kind: "note" | "file";
  name: string;
  folder?: string;
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

export const libraryApi = {
  list: () => req<LibItem[]>(""),
  graph: () => req<Graph>("/graph"),
  getNote: (id: string) => req<LibNote>(`/notes/${id}`),
  createNote: (b: NoteBody) => req<LibItem>("/notes", { method: "POST", body: JSON.stringify(b) }),
  updateNote: (id: string, b: NoteBody) =>
    req<LibItem>(`/notes/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  renameFolder: (src: string, dst: string) =>
    req<{ ok: boolean; moved: number }>("/folders/rename", {
      method: "POST", body: JSON.stringify({ src, dst }),
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

  /** Library files are served as attachments — fetch, then hand the blob over. */
  async download(item: LibItem): Promise<void> {
    const res = await fetch(`/api/library/files/${item.id}`);
    if (!res.ok) throw new Error("Не удалось скачать");
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = item.filename || item.name;
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

/** Every folder path present in the note set, sorted — for datalist hints. */
export function folderList(items: LibItem[]): string[] {
  const out = new Set<string>();
  for (const it of items) {
    if (it.kind !== "note") continue;
    const f = normFolder(it.folder || "");
    if (!f) continue;
    // Intermediate folders count too: «А/Б» implies «А».
    const parts = f.split("/");
    for (let i = 1; i <= parts.length; i++) out.add(parts.slice(0, i).join("/"));
  }
  return [...out].sort((a, b) => a.localeCompare(b, "ru"));
}
