// Reusable media attach area: click to browse, drag-and-drop, thumbnails,
// click-to-enlarge. Used by the hosting editor and available to any other form
// that needs attachments (shared store — services/media_store.py).
//
// Files that are not raster images (SVG, PDF, video) are listed as chips rather
// than previewed: the backend hands those out as opaque attachments on purpose,
// so there is nothing safe to render inline.
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { ImagePlus, Loader2, Trash2, X, FileText, Download } from "lucide-react";
import { toast } from "../infra/Toast";

export interface MediaItem {
  id: string;
  name: string;
  mime: string;
  size: number;
  inline: boolean;
  created_at: number;
}

export const mediaUrl = (id: string) => `/api/media/${id}`;

export async function uploadMedia(file: File): Promise<MediaItem> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/media/upload", { method: "POST", body: fd });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof body.detail === "string" ? body.detail : "Не удалось загрузить");
  }
  return res.json();
}

export async function fetchMediaMeta(ids: string[]): Promise<MediaItem[]> {
  if (ids.length === 0) return [];
  const res = await fetch("/api/media");
  if (!res.ok) return [];
  const all: MediaItem[] = await res.json();
  const byId = new Map(all.map(m => [m.id, m]));
  // Preserve the caller's order — it is the order the user arranged.
  return ids.map(id => byId.get(id)).filter((m): m is MediaItem => !!m);
}

export const fmtSize = (n: number) =>
  n < 1024 ? `${n} Б` : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} КБ` : `${(n / 1048576).toFixed(1)} МБ`;

/**
 * ⚠️ Media CANNOT be loaded by putting the URL in `src`/`href`.
 *
 * The panel authenticates with a Bearer token attached by the global `fetch`
 * interceptor (auth/apiClient.ts). A browser-initiated image or link request does
 * not go through `fetch`, carries no Authorization header, and `/api/media/{id}`
 * answers 401 — every preview would be a broken icon. So the bytes are fetched
 * with auth and handed to the DOM as an object URL.
 */
export function useMediaObjectUrl(id: string | null | undefined): string {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!id) { setUrl(""); return; }
    let alive = true;
    let made = "";
    fetch(mediaUrl(id))
      .then(r => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then(blob => {
        if (!alive) return;
        made = URL.createObjectURL(blob);
        setUrl(made);
      })
      .catch(() => { if (alive) setUrl(""); });
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [id]);
  return url;
}

/** Authenticated <img>. Renders a neutral placeholder until the blob arrives. */
export function MediaImg({ item, style, className, onClick, title }: {
  item: MediaItem;
  style?: CSSProperties;
  className?: string;
  onClick?: (e: React.MouseEvent) => void;
  title?: string;
}) {
  const url = useMediaObjectUrl(item.id);
  if (!url) {
    return (
      <span className={className} onClick={onClick} title={title}
        style={{ ...style, display: "inline-block", background: "var(--bg-soft)" }} />
    );
  }
  return <img src={url} alt={item.name} title={title ?? item.name}
    className={className} style={style} onClick={onClick} />;
}

/** Save an attachment to disk — same auth problem, same solution. */
export async function downloadMedia(item: MediaItem): Promise<void> {
  const res = await fetch(mediaUrl(item.id));
  if (!res.ok) throw new Error("Не удалось скачать файл");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = item.name || "file";
  a.click();
  URL.revokeObjectURL(url);
}

/** Full-screen preview. Closes on backdrop click and on Escape. */
export function Lightbox({ item, onClose }: { item: MediaItem; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-6"
      onMouseDown={e => e.target === e.currentTarget && onClose()}
      onKeyDown={e => e.key === "Escape" && onClose()}
      tabIndex={-1}
      ref={el => el?.focus()}
      role="dialog"
      aria-label={item.name}
    >
      <button className="btn" style={{ position: "absolute", top: 16, right: 16 }} onClick={onClose}>
        <X size={16} />
      </button>
      <MediaImg item={item}
        style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", borderRadius: 8 }} />
    </div>
  );
}

export function MediaDrop({ value, onChange, label = "Медиа", hint }: {
  /** Media ids, in display order. */
  value: string[];
  onChange: (ids: string[]) => void;
  label?: string;
  hint?: string;
}) {
  const [items, setItems] = useState<MediaItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [zoom, setZoom] = useState<MediaItem | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  const loadedFor = useRef<string>("");

  // Resolve ids → metadata whenever the id list actually changes (not on every
  // render: the parent re-creates the array on each keystroke of its own form).
  const key = value.join(",");
  if (loadedFor.current !== key) {
    loadedFor.current = key;
    void fetchMediaMeta(value).then(setItems);
  }

  const accept = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    setBusy(true);
    const added: string[] = [];
    for (const f of list) {
      try {
        const m = await uploadMedia(f);
        added.push(m.id);
        setItems(prev => [...prev, m]);
      } catch (e) {
        toast(e instanceof Error ? e.message : `Не удалось загрузить ${f.name}`, "error");
      }
    }
    if (added.length) onChange([...value, ...added]);
    setBusy(false);
  }, [onChange, value]);

  const detach = (id: string) => {
    // Detach from THIS record only; the file itself stays in the shared store so
    // another note or hosting can still reference it.
    onChange(value.filter(x => x !== id));
    setItems(prev => prev.filter(m => m.id !== id));
  };

  return (
    <div className="flex flex-col gap-2">
      <label className="label">{label}</label>

      <div
        onClick={() => input.current?.click()}
        onDragOver={e => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={e => { e.preventDefault(); setOver(false); void accept(e.dataTransfer.files); }}
        className="rounded-lg flex items-center justify-center gap-2 cursor-pointer text-xs"
        style={{
          minHeight: 84, padding: 12,
          border: `1px dashed ${over ? "var(--accent)" : "var(--line)"}`,
          background: over ? "var(--accent-dim)" : "var(--bg-soft)",
          color: "var(--t-low)",
        }}
      >
        {busy ? <Loader2 size={15} className="animate-spin" /> : <ImagePlus size={15} />}
        <span>{busy ? "Загрузка…" : "Перетащите файлы сюда или нажмите, чтобы выбрать"}</span>
      </div>
      <input ref={input} type="file" multiple hidden
        accept="image/png,image/jpeg,image/gif,image/webp,image/avif,image/svg+xml,application/pdf,video/mp4,video/webm"
        onChange={e => { void accept(e.target.files ?? []); e.target.value = ""; }} />

      {hint && <p className="text-[11px] text-[var(--t-faint)]">{hint}</p>}

      {items.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {items.map(m => (
            <div key={m.id} className="relative rounded-lg overflow-hidden"
              style={{ border: "1px solid var(--line-soft)", background: "var(--panel)" }}>
              {m.inline ? (
                <MediaImg item={m} title={`${m.name} · ${fmtSize(m.size)}`}
                  onClick={() => setZoom(m)}
                  style={{ width: 88, height: 88, objectFit: "cover", cursor: "zoom-in", display: "block" }} />
              ) : (
                <button type="button" title={`${m.name} · ${fmtSize(m.size)} — скачать`}
                  onClick={() => downloadMedia(m).catch(e =>
                    toast(e instanceof Error ? e.message : "Не удалось скачать", "error"))}
                  className="flex flex-col items-center justify-center gap-1 text-[11px]"
                  style={{ width: 88, height: 88, color: "var(--t-low)" }}>
                  <FileText size={20} />
                  <span className="trunc" style={{ maxWidth: 78 }}>{m.name}</span>
                  <Download size={11} />
                </button>
              )}
              <button
                onClick={() => detach(m.id)}
                title="Открепить"
                className="absolute"
                style={{
                  top: 2, right: 2, padding: 3, borderRadius: 6,
                  background: "var(--panel)", border: "1px solid var(--line)",
                  color: "var(--t-low)", lineHeight: 0,
                }}
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      {zoom && <Lightbox item={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}
