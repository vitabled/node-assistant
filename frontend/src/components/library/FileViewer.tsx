// Просмотр загруженного файла — в той же правой области, что и заметка.
//
// ⚠️ Почему не открыть файл ссылкой: сервер отдаёт файлы библиотеки только как
// вложение, и это намеренно — чужой HTML, отрисованный на НАШЕМ origin, получил
// бы доступ к сессии и localStorage, то есть это хранимая XSS. Поэтому HTML
// показываем в песочнице: `sandbox=""` (пустой список разрешений) отключает
// скрипты, формы и доступ к родителю, а `srcDoc` не даёт документу собственного
// адреса. Тот же приём, что у страниц подписок (`rw/SubPages.tsx`).
import { useEffect, useState } from "react";
import { X, Download, Loader2, FileText } from "lucide-react";
import { libraryApi, type LibItem } from "./api";
import { fmtSize } from "../common/MediaDrop";

/** Что вообще имеет смысл показывать: текстовые форматы. */
const TEXTY = /^(text\/|application\/(json|xml|x-yaml|yaml|javascript|xhtml))/i;
const HTMLISH = /^(text\/html|application\/xhtml)/i;

export function isViewable(item: LibItem): boolean {
  const mime = item.mime || "";
  const name = (item.filename || item.name || "").toLowerCase();
  return TEXTY.test(mime)
    || /\.(html?|md|markdown|txt|log|json|ya?ml|csv|conf|ini)$/.test(name);
}

function isHtml(item: LibItem): boolean {
  const name = (item.filename || item.name || "").toLowerCase();
  return HTMLISH.test(item.mime || "") || /\.html?$/.test(name);
}

export function FileViewer({ item, onClose, onDownload }: {
  item: LibItem;
  onClose: () => void;
  onDownload: () => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    setText(null); setErr("");
    if (!isViewable(item)) {
      // Бинарь показывать нечем — не тянем его в память ради этого.
      setErr("");
      return;
    }
    libraryApi.text(item)
      .then(t => { if (alive) setText(t); })
      .catch(e => { if (alive) setErr(e instanceof Error ? e.message : "Не удалось открыть"); });
    return () => { alive = false; };
  }, [item]);

  const html = isHtml(item);
  const viewable = isViewable(item);

  return (
    <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8, padding: "10px 12px",
        borderBottom: "1px solid var(--line-soft)",
      }}>
        <FileText size={14} style={{ color: "var(--t-low)", flex: "none" }} />
        <span className="trunc" style={{ flex: 1, fontSize: 13, fontWeight: 600, color: "var(--t-hi)" }}
          title={item.filename || item.name}>
          {item.name || item.filename}
        </span>
        <span className="micro" style={{ flex: "none" }}>
          {item.size == null ? "" : fmtSize(item.size)}
        </span>
        <button className="iconbtn" style={{ width: 24, height: 24 }} title="Скачать"
          onClick={onDownload}><Download size={13} /></button>
        <button className="iconbtn" style={{ width: 24, height: 24 }} title="Закрыть"
          onClick={onClose}><X size={13} /></button>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 12 }}>
        {!viewable && (
          <p className="hint" style={{ marginTop: 0 }}>
            Этот формат в панели не показывается — скачайте файл, чтобы открыть его
            в подходящей программе.
          </p>
        )}
        {viewable && err && <p className="hint" style={{ color: "var(--err)" }}>{err}</p>}
        {viewable && !err && text === null && (
          <p className="hint" style={{ marginTop: 0 }}>
            <Loader2 size={12} className="animate-spin" style={{ display: "inline" }} /> Загрузка…
          </p>
        )}
        {viewable && text !== null && (html ? (
          <iframe
            title={item.name || "предпросмотр"}
            sandbox=""
            srcDoc={text}
            style={{ width: "100%", height: "100%", minHeight: 420, border: "1px solid var(--line-soft)",
                     borderRadius: 8, background: "#fff" }}
          />
        ) : (
          <pre style={{ fontSize: 12, color: "var(--t-mid)", whiteSpace: "pre-wrap",
                        wordBreak: "break-word", margin: 0 }}>{text}</pre>
        ))}
      </div>

      {html && text !== null && (
        <p className="micro" style={{ padding: "0 12px 10px", color: "var(--t-faint)" }}>
          Страница показана в песочнице: скрипты и переходы отключены, поэтому
          интерактивные элементы работать не будут. Для полноценного просмотра
          скачайте файл.
        </p>
      )}
    </div>
  );
}
