// Obsidian-flavoured markdown → sanitized HTML.
//
// Two things happen here, in this order:
//   1. wiki syntax is rewritten to HTML BEFORE marked runs — `![[media-id]]`
//      embeds and `[[Заметка]]` links are not markdown, marked knows nothing
//      about them;
//   2. the result goes through DOMPurify. This is NOT optional: markdown passes
//      raw HTML through by design, so an unsanitised note is stored XSS against
//      our own origin (where the session token lives).
//
// The rewrite deliberately skips fenced blocks and inline code spans — a note
// that DOCUMENTS the `[[…]]` syntax must show it, not follow it.
import { marked } from "marked";
import DOMPurify from "dompurify";
import { fmtSize, mediaUrl, type MediaItem } from "../common/MediaDrop";

// ⚠️ `/api/media/{id}` is gated by `require_account`, i.e. it needs an
// `Authorization: Bearer …` header — and a plain `<img src>` / `<a href>` never
// sends one (only `fetch` does, via the global interceptor in auth/apiClient).
// So embeds are rendered WITHOUT a src and resolved to an object URL by the
// viewer; attachments download through fetch on click. Do not "simplify" these
// back to a direct URL — it renders as a broken image.

export interface NoteRef { id: string; name: string }

// `![[id]]` / `![[id|подпись]]`
const EMBED_RE = /!\[\[([^\][|\n]+?)(?:\|([^\]\n]*))?\]\]/g;
// `[[Имя]]` / `[[Имя|алиас]]` / `[[Имя#якорь]]` / `[[Имя#якорь|алиас]]`
const LINK_RE = /\[\[([^\][|#\n]+?)(?:#([^\][|\n]*))?(?:\|([^\]\n]*))?\]\]/g;
// Fenced blocks and inline code spans, as one capturing group so `split` keeps
// them (they land on the odd indices).
const CODE_RE = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g;

// ── media metadata cache ──────────────────────────────────────
// Whether an embed renders as <img> or as a download link depends on the
// backend's `inline` flag (only raster images are handed back with their real
// type), so the ids have to be resolved before rendering. One request for the
// whole list, cached for the module's lifetime.
const cache = new Map<string, MediaItem>();
const missing = new Set<string>();
let inflight: Promise<void> | null = null;

/** Prime the cache with a just-uploaded item so its embed renders immediately. */
export function cacheMedia(item: MediaItem): void {
  cache.set(item.id, item);
  missing.delete(item.id);
}

async function ensureMedia(ids: string[]): Promise<void> {
  // An id that came back unknown goes on the `missing` list instead of being
  // retried: a deleted media id would otherwise refetch on every keystroke.
  // The list is never cleared wholesale — dropping it on each load made two
  // unknown ids in one note ping-pong a fetch per render.
  const need = ids.filter(id => !cache.has(id) && !missing.has(id));
  if (need.length === 0) return;
  if (!inflight) {
    inflight = (async () => {
      try {
        const res = await fetch("/api/media");
        if (!res.ok) return;
        const all: MediaItem[] = await res.json();
        for (const m of all) { cache.set(m.id, m); missing.delete(m.id); }
      } catch {
        // Offline/500 — leave the cache as it is; embeds degrade to links.
      } finally {
        inflight = null;
      }
    })();
  }
  await inflight;
  for (const id of need) if (!cache.has(id)) missing.add(id);
}

// ── authenticated media bytes ─────────────────────────────────
// Object URLs are kept for the tab's lifetime: a note is re-rendered on every
// keystroke in split view, and revoking per render would blank the images that
// are still on screen. The set is bounded by "media the user actually looked at".
const objectUrls = new Map<string, string>();
const objectInflight = new Map<string, Promise<string>>();

/** Blob URL for an embedded image, fetched with the account's bearer token. */
export function mediaObjectUrl(id: string): Promise<string> {
  const ready = objectUrls.get(id);
  if (ready) return Promise.resolve(ready);
  const running = objectInflight.get(id);
  if (running) return running;
  const p = (async () => {
    const res = await fetch(mediaUrl(id));
    if (!res.ok) throw new Error("Медиа недоступно");
    const url = URL.createObjectURL(await res.blob());
    objectUrls.set(id, url);
    return url;
  })().finally(() => objectInflight.delete(id));
  objectInflight.set(id, p);
  return p;
}

/** Download an embedded attachment (SVG/PDF/video) through the same auth path. */
export async function downloadMedia(id: string, name: string): Promise<void> {
  const res = await fetch(mediaUrl(id));
  if (!res.ok) throw new Error("Медиа недоступно");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name || id;
  a.click();
  URL.revokeObjectURL(url);
}

// ── helpers ───────────────────────────────────────────────────
function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function splitCode(src: string): { code: boolean; text: string }[] {
  return src.split(CODE_RE).map((text, i) => ({ code: i % 2 === 1, text }));
}

function embedHtml(rawId: string, caption: string): string {
  const id = rawId.trim();
  const item = cache.get(id);
  const label = caption.trim() || item?.name || id;
  if (item?.inline) {
    // No `src`: the viewer fills it from an authenticated fetch (see above).
    return `<img class="md-embed" alt="${esc(label)}" title="${esc(label)}" data-media="${esc(id)}">`;
  }
  if (!item && missing.has(id)) {
    return `<span class="md-missing">Медиа не найдено: ${esc(id)}</span>`;
  }
  // Not a raster image (SVG/PDF/video): the backend hands those out as opaque
  // attachments on purpose, so there is nothing safe to render inline.
  const size = item ? ` · ${fmtSize(item.size)}` : "";
  return `<a class="md-file" href="#" data-media="${esc(id)}" data-name="${esc(label)}">` +
    `${esc(label)}${esc(size)}</a>`;
}

function linkHtml(name: string, anchor: string, alias: string, byName: Map<string, string>): string {
  const target = name.trim();
  const id = byName.get(target.toLowerCase()) || "";
  const label = alias.trim() || (anchor.trim() ? `${target} › ${anchor.trim()}` : target);
  return `<a href="#" class="wikilink${id ? "" : " wikilink-missing"}"` +
    ` data-note="${esc(id)}" data-name="${esc(target)}"` +
    ` title="${esc(id ? `Открыть «${target}»` : `Заметки «${target}» ещё нет — создать`)}">` +
    `${esc(label)}</a>`;
}

/** Wiki syntax → HTML, outside code only. Exported for the preview and tests. */
export function expandWiki(src: string, notes: NoteRef[]): string {
  const byName = new Map<string, string>();
  for (const n of notes) {
    const key = (n.name || "").trim().toLowerCase();
    if (key && !byName.has(key)) byName.set(key, n.id);
  }
  return splitCode(src).map(seg => {
    if (seg.code) return seg.text;
    // Embeds first: after this pass no `![[…]]` is left, so the link regex
    // cannot swallow one (no lookbehind needed — Safari < 16.4 lacks it).
    return seg.text
      .replace(EMBED_RE, (_m, id: string, caption?: string) => embedHtml(id, caption ?? ""))
      .replace(LINK_RE, (_m, name: string, anchor?: string, alias?: string) =>
        linkHtml(name, anchor ?? "", alias ?? "", byName));
  }).join("");
}

/** Media ids embedded in a note (used to resolve metadata before rendering). */
export function embedIds(src: string): string[] {
  const ids: string[] = [];
  for (const seg of splitCode(src)) {
    if (seg.code) continue;
    for (const m of seg.text.matchAll(EMBED_RE)) ids.push(m[1].trim());
  }
  return ids;
}

/** Full pipeline: wiki → markdown → sanitized HTML. */
export async function renderNote(text: string, notes: NoteRef[]): Promise<string> {
  const src = text || "";
  await ensureMedia(embedIds(src));
  const parsed = marked.parse(expandWiki(src, notes), { gfm: true, breaks: true });
  const html = typeof parsed === "string" ? parsed : await parsed;
  return DOMPurify.sanitize(html, {
    // `data-*` survives by default, but the wiki links are the whole point of
    // this renderer — pin them explicitly so a DOMPurify default change cannot
    // quietly turn every link into dead text.
    ADD_ATTR: ["data-note", "data-name", "data-media", "download", "target", "rel"],
  });
}
