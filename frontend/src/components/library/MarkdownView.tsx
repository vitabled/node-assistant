// Rendered note: sanitized markdown + the behaviour that HTML alone cannot
// carry — wiki-link navigation, authenticated image loading, click-to-zoom.
import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { toast } from "../infra/Toast";
import { renderNote, mediaObjectUrl, downloadMedia, type NoteRef } from "./markdown";

// Scoped to `.lib-md` so it cannot leak into the rest of the panel. Colours are
// palette tokens only — the note has to read in every skin/theme.
const MD_CSS = `
.lib-md{font-size:13.5px;line-height:1.65;color:var(--t-mid);overflow-wrap:anywhere}
.lib-md>:first-child{margin-top:0}
.lib-md h1,.lib-md h2,.lib-md h3,.lib-md h4{color:var(--t-hi);font-weight:700;line-height:1.3;margin:1.25em 0 .5em}
.lib-md h1{font-size:19px}
.lib-md h2{font-size:16.5px;border-bottom:1px solid var(--line-soft);padding-bottom:.25em}
.lib-md h3{font-size:14.5px}
.lib-md h4{font-size:13.5px;color:var(--t-mid)}
.lib-md p{margin:.7em 0}
.lib-md a{color:var(--accent-hi);text-decoration:none}
.lib-md a:hover{text-decoration:underline}
.lib-md a.wikilink{border-bottom:1px dashed var(--accent-line);cursor:pointer;text-decoration:none}
.lib-md a.wikilink-missing{color:var(--t-low);border-bottom-style:dotted;border-bottom-color:var(--line)}
.lib-md a.md-file{display:inline-flex;align-items:center;gap:6px;font-size:12px;
  padding:3px 9px;border-radius:var(--r-sm);border:1px solid var(--line);
  background:var(--bg3);color:var(--t-mid);text-decoration:none}
.lib-md a.md-file:hover{color:var(--t-hi);border-color:var(--accent-line);text-decoration:none}
.lib-md a.md-file::before{content:"↓";opacity:.7}
.lib-md .md-missing{display:inline-block;font-size:12px;padding:2px 8px;border-radius:var(--r-sm);
  border:1px dashed var(--err-line);background:var(--err-dim);color:var(--err)}
.lib-md img.md-embed{max-width:100%;display:block;margin:.7em 0;border-radius:var(--r-md);
  border:1px solid var(--line-soft);cursor:zoom-in}
.lib-md code{font-family:var(--mono);font-size:12px;background:var(--bg3);
  border:1px solid var(--line-soft);border-radius:5px;padding:1px 5px;color:var(--t-hi)}
.lib-md pre{background:var(--bg1);border:1px solid var(--line-soft);border-radius:var(--r-md);
  padding:11px 13px;overflow-x:auto;margin:.8em 0}
.lib-md pre code{background:none;border:0;padding:0;font-size:12px;color:var(--t-mid)}
.lib-md ul,.lib-md ol{margin:.6em 0;padding-left:1.5em}
.lib-md li{margin:.25em 0}
.lib-md li::marker{color:var(--t-low)}
.lib-md blockquote{margin:.8em 0;padding:.1em 0 .1em 13px;border-left:3px solid var(--line);color:var(--t-low)}
.lib-md hr{border:0;border-top:1px solid var(--line);margin:1.3em 0}
.lib-md table{border-collapse:collapse;font-size:12.5px;margin:.8em 0;display:block;overflow-x:auto}
.lib-md th,.lib-md td{border:1px solid var(--line-soft);padding:5px 10px;text-align:left}
.lib-md th{background:var(--bg3);color:var(--t-hi);font-weight:600}
.lib-md input[type=checkbox]{accent-color:var(--accent);margin-right:6px}
.lib-md .empty{color:var(--t-faint);font-style:italic}
`;

export function MarkdownView({ text, notes, onOpenNote, onCreateNote, className = "", style }: {
  text: string;
  notes: NoteRef[];
  onOpenNote: (id: string) => void;
  /** A `[[link]]` whose target does not exist yet. */
  onCreateNote: (name: string) => void;
  className?: string;
  style?: React.CSSProperties;
}) {
  const [html, setHtml] = useState("");
  const [zoom, setZoom] = useState<{ src: string; alt: string } | null>(null);
  const box = useRef<HTMLDivElement | null>(null);

  // Re-render on the CONTENT of the note list, not on its identity: the parent
  // rebuilds that array on every keystroke, and depending on it directly would
  // re-run the pipeline for nothing.
  const notesKey = notes.map(n => `${n.id}\u0000${n.name}`).join("\u0001");
  const notesRef = useRef(notes);
  notesRef.current = notes;

  useEffect(() => {
    let alive = true;
    renderNote(text, notesRef.current)
      .then(out => { if (alive) setHtml(out); })
      .catch(() => { if (alive) setHtml('<p class="empty">Не удалось отрисовать заметку</p>'); });
    return () => { alive = false; };
  }, [text, notesKey]);

  // Embedded images carry only `data-media` — resolve each to an object URL
  // fetched with the account token (a bare <img src> would get a 401).
  useEffect(() => {
    const root = box.current;
    if (!root) return;
    let alive = true;
    root.querySelectorAll<HTMLImageElement>("img[data-media]").forEach(img => {
      const id = img.dataset.media;
      if (!id || img.getAttribute("src")) return;
      mediaObjectUrl(id)
        .then(url => { if (alive) img.src = url; })
        .catch(() => { if (alive) img.replaceWith(document.createTextNode(`[медиа ${id} недоступно]`)); });
    });
    return () => { alive = false; };
  }, [html]);

  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;

    const img = target.closest<HTMLImageElement>("img[data-media]");
    if (img) {
      if (img.src) setZoom({ src: img.src, alt: img.alt });
      return;
    }

    const a = target.closest<HTMLAnchorElement>("a");
    if (!a) return;

    if (a.dataset.note !== undefined) {          // wiki link
      e.preventDefault();
      if (a.dataset.note) onOpenNote(a.dataset.note);
      else onCreateNote(a.dataset.name || "");
      return;
    }
    if (a.dataset.media) {                        // attachment chip
      e.preventDefault();
      downloadMedia(a.dataset.media, a.dataset.name || "")
        .catch(err => toast(err instanceof Error ? err.message : "Не удалось скачать", "error"));
      return;
    }
    const href = a.getAttribute("href") || "";
    if (/^https?:/i.test(href)) {                 // never navigate the SPA away
      e.preventDefault();
      window.open(href, "_blank", "noopener,noreferrer");
    }
  };

  return (
    <>
      <style>{MD_CSS}</style>
      <div ref={box} className={`lib-md ${className}`} style={style} onClick={onClick}
        dangerouslySetInnerHTML={{ __html: html || '<p class="empty">Пусто</p>' }} />
      {zoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-6"
          role="dialog" aria-label={zoom.alt} tabIndex={-1} ref={el => el?.focus()}
          onMouseDown={e => e.target === e.currentTarget && setZoom(null)}
          onKeyDown={e => e.key === "Escape" && setZoom(null)}>
          <button className="btn" style={{ position: "absolute", top: 16, right: 16 }}
            onClick={() => setZoom(null)}><X size={16} /></button>
          <img src={zoom.src} alt={zoom.alt}
            style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", borderRadius: 8 }} />
        </div>
      )}
    </>
  );
}
