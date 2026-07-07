import { useEffect, useState, useRef, useCallback } from "react";
import { FileCode, Upload, Trash2, Loader2, Plus, Eye } from "lucide-react";

// Ф5 — catalogue of Orion subscription-page HTML files. Orion ships the
// subscription page as ONE build-less index.html; here the account uploads/pastes
// such pages, previews them safely, and (later, Ф6) picks one to mount into the
// remnawave/subscription-page container. Catalogue left · sandboxed preview right.

const MAX_HTML_BYTES = 512 * 1024; // keep in sync with backend subpage_store

interface Page {
  id: string;
  name: string;
  size: number;
  created_at: number;
}

const byteLen = (s: string) => new TextEncoder().encode(s).length;

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  return `${(bytes / 1024).toFixed(1)} КиБ`;
}

function fmtDate(ts: number): string {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleDateString("ru-RU", {
      day: "2-digit", month: "2-digit", year: "numeric",
    });
  } catch { return ""; }
}

// Stack the two columns on a narrow (mobile) viewport, ≤820px like the shell.
function useIsNarrow(): boolean {
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 820px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 820px)");
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

export function SubPages() {
  const [pages, setPages]           = useState<Page[]>([]);
  const [loading, setLoading]       = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewHtml, setPreviewHtml]     = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [err, setErr]     = useState("");
  const [busy, setBusy]   = useState(false);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteName, setPasteName] = useState("");
  const [pasteHtml, setPasteHtml] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const narrow  = useIsNarrow();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetch("/api/subpages").then(r => r.json());
      setPages(Array.isArray(data?.pages) ? data.pages : []);
    } catch { /* keep previous list on transient failure */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Fetch the selected page's raw HTML for the preview iframe.
  useEffect(() => {
    if (!selectedId) { setPreviewHtml(""); return; }
    let alive = true;
    setPreviewLoading(true);
    fetch(`/api/subpages/${selectedId}/raw`)
      .then(r => (r.ok ? r.text() : Promise.reject()))
      .then(t => { if (alive) { setPreviewHtml(t); setPreviewLoading(false); } })
      .catch(() => { if (alive) { setPreviewHtml(""); setPreviewLoading(false); setErr("Не удалось загрузить предпросмотр страницы"); } });
    return () => { alive = false; };
  }, [selectedId]);

  const submit = async (name: string, html: string) => {
    setErr("");
    if (!name.trim()) { setErr("Укажите имя страницы"); return; }
    if (!html) { setErr("Пустой HTML"); return; }
    if (byteLen(html) > MAX_HTML_BYTES) {
      setErr(`HTML превышает лимит ${MAX_HTML_BYTES / 1024} КиБ`); return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/subpages", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), html }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        setErr(typeof j.detail === "string" ? j.detail : "Ошибка загрузки");
        return;
      }
      const created: Page = await res.json();
      setPasteOpen(false); setPasteName(""); setPasteHtml("");
      await load();
      setSelectedId(created.id);
    } catch { setErr("Сеть недоступна"); }
    finally { setBusy(false); }
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // reset so re-selecting the same file fires onChange
    if (!file) return;
    setErr("");
    if (file.size > MAX_HTML_BYTES) {
      setErr(`Файл превышает лимит ${MAX_HTML_BYTES / 1024} КиБ`); return;
    }
    try {
      // Accept any file as text — a broken/non-HTML file just previews as-is.
      await submit(file.name, await file.text());
    } catch { setErr("Не удалось прочитать файл"); }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Удалить страницу?")) return;
    await fetch(`/api/subpages/${id}`, { method: "DELETE" }).catch(() => {});
    if (selectedId === id) { setSelectedId(null); setPreviewHtml(""); }
    await load();
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-6 ni-pagebody">
        <div className="flex items-center justify-between mb-6 ni-pagehead">
          <div>
            <h1 className="h1">Страницы подписок</h1>
            <p className="sub">Каталог HTML-страниц Orion для страницы подписок Remnawave</p>
          </div>
          <div className="flex items-center gap-2 ni-pagehead-actions">
            <button onClick={() => { setPasteOpen(o => !o); setErr(""); }} className="btn btn-soft">
              <Plus size={13} /> Вставить HTML
            </button>
            <button onClick={() => fileRef.current?.click()} className="btn btn-primary" disabled={busy}>
              {busy ? <Loader2 size={13} className="spin" /> : <Upload size={13} />} Загрузить HTML
            </button>
            <input ref={fileRef} type="file" accept=".html,.htm,text/html"
              style={{ display: "none" }} onChange={onFile} />
          </div>
        </div>

        {pasteOpen && (
          <div className="card card-p mb-4 flex flex-col gap-2">
            <input value={pasteName} onChange={e => { setPasteName(e.target.value); setErr(""); }}
              placeholder="Имя страницы, напр. orion-index.html" className="input"
              autoComplete="off" spellCheck={false} />
            <textarea value={pasteHtml} onChange={e => { setPasteHtml(e.target.value); setErr(""); }}
              placeholder="<!doctype html>…" rows={8} spellCheck={false}
              className="input font-mono text-xs" style={{ resize: "vertical" }} />
            <div className="flex justify-end gap-2">
              <button onClick={() => { setPasteOpen(false); setErr(""); }} className="btn btn-soft">Отмена</button>
              <button onClick={() => submit(pasteName, pasteHtml)} disabled={busy || !pasteName.trim() || !pasteHtml}
                className="btn btn-primary">
                {busy ? <><Loader2 size={13} className="spin" /> Сохранение…</> : "Сохранить"}
              </button>
            </div>
          </div>
        )}

        {err && <p className="errmsg mb-3">{err}</p>}

        <div style={{
          display: "grid", gap: 16, alignItems: "start",
          gridTemplateColumns: narrow ? "1fr" : "minmax(240px, 320px) 1fr",
        }}>
          {/* ── Catalogue ── */}
          <div className="card" style={{ overflow: "hidden" }}>
            <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
              <FileCode size={13} style={{ color: "var(--t-low)" }} />
              <span className="micro">Каталог</span>
              {pages.length > 0 && (
                <span className="text-[10px] tabular-nums" style={{ color: "var(--t-faint)", marginLeft: "auto" }}>
                  {pages.length}
                </span>
              )}
            </div>
            <div className="p-2 flex flex-col gap-1">
              {loading ? (
                <div className="flex items-center justify-center py-10">
                  <Loader2 size={18} className="spin" style={{ color: "var(--t-faint)" }} />
                </div>
              ) : pages.length === 0 ? (
                <p className="text-xs px-2 py-6 text-center" style={{ color: "var(--t-faint)" }}>
                  Каталог пуст — загрузите свой index.html.
                </p>
              ) : (
                pages.map(p => {
                  const on = p.id === selectedId;
                  return (
                    <div key={p.id} onClick={() => setSelectedId(p.id)}
                      className="flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer"
                      style={{ background: on ? "var(--accent-dim)" : "transparent" }}>
                      <FileCode size={13} style={{ color: on ? "var(--accent)" : "var(--t-low)", flex: "none" }} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm trunc" style={{ color: "var(--t-hi)" }}>{p.name}</p>
                        <p className="text-[10px] tabular-nums" style={{ color: "var(--t-faint)" }}>
                          {fmtSize(p.size)} · {fmtDate(p.created_at)}
                        </p>
                      </div>
                      <button onClick={e => { e.stopPropagation(); remove(p.id); }}
                        className="iconbtn danger" style={{ width: 22, height: 22, flex: "none" }} title="Удалить">
                        <Trash2 size={12} />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* ── Preview (sandboxed: no allow-scripts → HTML/CSS render, JS never runs) ── */}
          <div className="card" style={{ overflow: "hidden" }}>
            <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
              <Eye size={13} style={{ color: "var(--t-low)" }} />
              <span className="micro">Предпросмотр</span>
            </div>
            {!selectedId ? (
              <p className="text-xs px-3 py-10 text-center" style={{ color: "var(--t-faint)" }}>
                Выберите страницу в каталоге для предпросмотра.
              </p>
            ) : previewLoading ? (
              <div className="flex items-center justify-center" style={{ height: "70vh", minHeight: 320 }}>
                <Loader2 size={18} className="spin" style={{ color: "var(--t-faint)" }} />
              </div>
            ) : (
              <iframe title="Предпросмотр страницы подписок" sandbox="" srcDoc={previewHtml}
                style={{ width: "100%", height: "70vh", minHeight: 320, border: "none", background: "#fff" }} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
