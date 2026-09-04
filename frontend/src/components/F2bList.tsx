import { useEffect, useMemo, useState, type ReactNode, type Dispatch, type SetStateAction } from "react";
import {
  Loader2, Save, ShieldAlert, Download, Upload,
  FilePlus, FileDown, CheckCircle2, XCircle, ChevronDown, X,
} from "lucide-react";
import { toast } from "./infra/Toast";
import { deployJobsKey } from "../auth/store";
import type { DeployJobSummary } from "./DeployDashboard";
import { FlagChip } from "./common/FlagChip";

/**
 * «Fail2Ban list» (Wave-5 PR-2): список IP/CIDR, который backend применяет
 * на сервере при любом деплое (banip + персистентность, удалённое — unban).
 *
 * Rework (F2B list UX):
 *  - кнопки всегда видимы (ActionButton-стиль, иконка+текст), главная «Сохранить» — primary;
 *  - счётчик адресов + построчный поиск с предпросмотром совпадений (textarea остаётся редактируемой);
 *  - импорт (TXT/CSV/JSON) через inline-диалог с дедупом/тримом и пометкой подозрительных строк;
 *  - экспорт (TXT/CSV/JSON) через Blob + a.download;
 *  - сворачиваемая панель синхронизации с нодами (localStorage `deploy_jobs`), чекбоксы pull/push,
 *    массовый collect/push через POST /api/f2b-list/nodes/sync, результаты по каждой ноде.
 */

// ── типы ────────────────────────────────────────────────────────────────

/** Минимум из savedForm ноды, нужный для SSH. */
interface NodeForm {
  ssh_user?: string;
  ssh_password?: string;
  current_ssh_port?: string | number;
  new_ssh_port?: string | number;
  change_ssh_port?: boolean;
  country_code?: string;
}

interface NodeRef {
  taskId: string;
  label: string;          // domain || ip
  ip: string;
  ssh_user: string;
  ssh_password: string;
  ssh_port: number;
  country_code: string | null;
}

/** Элемент результата batch-синка (defensive: поля необязательны). */
interface SyncResult {
  ip?: string;
  ok?: boolean;
  error?: string;
  collected?: string[];
  ips?: string[];
  applied?: number;
  unbanned?: number;
}

type ImportFormat = "txt" | "csv" | "json";
type ExportFormat = "txt" | "csv" | "json";
type SyncOp = "collect" | "push";

// ── helpers ──────────────────────────────────────────────────────────────

/** Непустые строки (trim + filter). Без дедупа — так и уходит в PUT. */
function lines(text: unknown): string[] {
  const s = typeof text === "string" ? text : String(text ?? "");
  return s.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
}

/** Дедуп без учёта регистра, порядок первого вхождения сохраняется. */
function dedupe(list: string[]): string[] {
  const seen = new Set<string>();
  return list.filter(s => {
    if (typeof s !== "string") return false;
    const k = s.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/** Русская плюрализация (1 адрес, 2 адреса, 5 адресов). */
function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

/** Нестрогая проверка ip/cidr (IPv4 с опц. префиксом, IPv6-подобное). */
function isValidIpOrCidr(s: string): boolean {
  return (
    /^(\d{1,3})(\.\d{1,3}){3}(\/\d{1,2})?$/.test(s) ||
    /^[0-9a-fA-F:]+(\/\d{1,3})?$/.test(s)
  );
}

interface ParsedImport {
  add: string[];       // строки для слияния (в txt — включая подозрительные)
  invalid: string[];   // подозрительные/пропущенные — только для показа
}

function parseImport(text: string, format: ImportFormat): ParsedImport {
  if (format === "json") {
    let data: unknown;
    try { data = JSON.parse(text); }
    catch { return { add: [], invalid: ["Некорректный JSON"] }; }
    if (!Array.isArray(data)) return { add: [], invalid: ["JSON должен быть массивом"] };
    const add: string[] = [];
    const invalid: string[] = [];
    for (const item of data) {
      let v: string | null = null;
      if (typeof item === "string") v = item.trim();
      else if (item && typeof item === "object") {
        const o = item as Record<string, unknown>;
        if (typeof o.ip === "string") v = o.ip.trim();
        else if (typeof o.cidr === "string") v = o.cidr.trim();
      }
      if (v) add.push(v);
      else invalid.push(JSON.stringify(item));
    }
    return { add, invalid };
  }

  if (format === "csv") {
    const add: string[] = [];
    const invalid: string[] = [];
    lines(text).forEach((row, idx) => {
      const cells = row.split(",").map(c => c.trim()).filter(Boolean);
      // Пропускаем строку-заголовок (ip/cidr/address).
      if (idx === 0 && cells.some(c => /^(ip|cidr|address|адрес)$/i.test(c))) return;
      const hit = cells.find(c => isValidIpOrCidr(c));
      if (hit) add.push(hit);
      else invalid.push(row);
    });
    return { add, invalid };
  }

  // txt: всё сохраняем как есть, подозрительные только помечаем.
  const add = lines(text);
  const invalid = add.filter(s => !isValidIpOrCidr(s));
  return { add, invalid };
}

function loadNodes(): NodeRef[] {
  let jobs: DeployJobSummary[] = [];
  try { jobs = JSON.parse(localStorage.getItem(deployJobsKey()) ?? "[]"); }
  catch { /* ignore malformed */ }
  if (!Array.isArray(jobs)) jobs = [];
  return jobs
    .map(j => {
      const f = (j.savedForm ?? {}) as NodeForm;
      const cur = parseInt(String(f.current_ssh_port ?? "22"), 10) || 22;
      const nxt = parseInt(String(f.new_ssh_port ?? "22"), 10) || 22;
      return {
        taskId: j.taskId,
        label: (j.domain || "").trim() || j.ip,
        ip: j.ip,
        ssh_user: f.ssh_user || "root",
        ssh_password: f.ssh_password || "",
        ssh_port: f.change_ssh_port ? nxt : cur,
        country_code: typeof f.country_code === "string" ? f.country_code.toUpperCase() : null,
      } as NodeRef;
    })
    .filter(n => !!n.ip);
}

function download(name: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ── ActionButton-подобная кнопка (иконка+текст, primary/default) ─────────
function Btn({ label, icon, onClick, variant = "default", disabled, loading, title }: {
  label: string;
  icon?: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary";
  disabled?: boolean;
  loading?: boolean;
  title?: string;
}) {
  const base = "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed select-none px-2.5 py-1.5 text-xs";
  const v = variant === "primary"
    ? "bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)]"
    : "border border-[var(--line)] bg-[var(--bg2)] text-[var(--t-mid)] hover:bg-[var(--bg3)]";
  return (
    <button type="button" onClick={onClick} disabled={disabled || loading}
      title={title} className={`${base} ${v}`}>
      {loading ? <Loader2 size={13} className="animate-spin" /> : icon}
      <span>{label}</span>
    </button>
  );
}

// ── компонент ────────────────────────────────────────────────────────────

export function F2bList() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [dirty, setDirty] = useState(false);

  // Поиск по строкам списка.
  const [search, setSearch] = useState("");

  // Импорт.
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importFormat, setImportFormat] = useState<ImportFormat>("txt");

  // Экспорт-дропдаун.
  const [exportOpen, setExportOpen] = useState(false);

  // Ноды (localStorage) + выбор pull/push (по умолчанию обе включены).
  const [nodes] = useState<NodeRef[]>(loadNodes);
  const [pullOff, setPullOff] = useState<Set<string>>(new Set());
  const [pushOff, setPushOff] = useState<Set<string>>(new Set());

  // Синк.
  const [busy, setBusy] = useState<SyncOp | null>(null);
  const [lastOp, setLastOp] = useState<SyncOp | null>(null);
  const [results, setResults] = useState<SyncResult[] | null>(null);
  const [collectedPreview, setCollectedPreview] = useState<string[] | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  useEffect(() => {
    fetch("/api/f2b-list").then(r => r.json())
      .then(d => setText((Array.isArray(d?.entries) ? d.entries : []).join("\n")))
      .catch(() => setErr("Не удалось загрузить список"))
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    setSaving(true); setErr("");
    try {
      const entries = lines(text);
      const res = await fetch("/api/f2b-list", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      setText((d.entries || []).join("\n"));
      setDirty(false);
      toast(`Fail2Ban list сохранён (${d.count})`, "success");
    } finally { setSaving(false); }
  };

  const pullNodes = nodes.filter(n => !pullOff.has(n.taskId));
  const pushNodes = nodes.filter(n => !pushOff.has(n.taskId));

  const toggle = (setter: Dispatch<SetStateAction<Set<string>>>, id: string) =>
    setter(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const sync = async (op: SyncOp) => {
    const selected = op === "collect" ? pullNodes : pushNodes;
    if (selected.length === 0) {
      toast(op === "collect" ? "Нет нод для сбора" : "Нет нод для загрузки", "error");
      return;
    }
    setBusy(op); setErr("");
    setResults(null); setCollectedPreview(null);
    try {
      const res = await fetch("/api/f2b-list/nodes/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nodes: selected.map(n => ({
            ip: n.ip, ssh_user: n.ssh_user, ssh_password: n.ssh_password,
            ssh_port: n.ssh_port, pull: op === "collect", push: op === "push",
          })),
          merge_collected: false,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      const rs = (Array.isArray(d.results) ? d.results : []) as SyncResult[];
      setResults(rs);
      setLastOp(op);
      setPanelOpen(true);
      if (op === "collect") {
        const all = dedupe(rs.flatMap(r => r.collected ?? r.ips ?? []));
        setCollectedPreview(all);
        toast(`Собрано ${all.length} адресов с ${selected.length} нод`, "success");
      } else {
        const applied = rs.reduce((s, r) => s + (r.applied ?? 0), 0);
        const unbanned = rs.reduce((s, r) => s + (r.unbanned ?? 0), 0);
        toast(`Загружено на ${selected.length} нод: применено ${applied}, разбанено ${unbanned}`, "success");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка синхронизации");
    } finally { setBusy(null); }
  };

  const mergeCollected = () => {
    if (!collectedPreview) return;
    setText(prev => dedupe([...lines(prev), ...collectedPreview]).join("\n"));
    setDirty(true);
    setCollectedPreview(null);
    toast("Собранные адреса слиты в список", "success");
  };

  const doImport = () => {
    const parsed = parseImport(importText, importFormat);
    if (parsed.add.length === 0) {
      toast(parsed.invalid[0] ?? "Пустой ввод", "error");
      return;
    }
    const before = lines(text).length;
    const merged = dedupe([...lines(text), ...parsed.add]);
    setText(merged.join("\n"));
    const added = merged.length - before;
    setDirty(true);
    setImportOpen(false);
    setImportText("");
    if (parsed.invalid.length > 0) {
      toast(`Импортировано ${added} (пропущено/подозрительно: ${parsed.invalid.length})`, "info");
    } else {
      toast(`Импортировано ${added} записей`, "success");
    }
  };

  const exportAs = (fmt: ExportFormat) => {
    const entries = lines(text);
    let name = "f2b-list.txt", content = entries.join("\n"), mime = "text/plain";
    if (fmt === "csv") {
      name = "f2b-list.csv"; mime = "text/csv";
      content = "ip\n" + entries.map(e => (e.includes(",") ? `"${e}"` : e)).join("\n");
    } else if (fmt === "json") {
      name = "f2b-list.json"; mime = "application/json";
      content = JSON.stringify(entries, null, 2);
    }
    download(name, content, mime);
    setExportOpen(false);
  };

  const allLines = lines(text);
  const uniqueCount = dedupe(allLines).length;
  const searchLower = String(search || "").trim().toLowerCase();
  const matched = searchLower
    ? allLines.filter(l => typeof l === "string" && l.toLowerCase().includes(searchLower))
    : [];

  const importPreview = useMemo(
    () => (importText.trim() ? parseImport(importText, importFormat) : null),
    [importText, importFormat],
  );

  const collectedCount = collectedPreview?.length ?? 0;

  return (
    <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="flex items-center gap-2">
        <ShieldAlert size={14} style={{ color: "var(--warn)" }} />
        <span className="micro">Fail2Ban list</span>
        {dirty && <span className="chip warn" style={{ fontSize: 10 }}>изменён</span>}
      </div>
      <p className="hint" style={{ marginTop: 0 }}>
        IP/CIDR по строке — автоматически банятся при любом деплое
        (нода/панель/SSL). Убрали из списка — при следующем деплое разбанится.
      </p>

      {loading ? (
        <Loader2 size={14} className="spin" style={{ color: "var(--t-faint)" }} />
      ) : (
        <>
          <div className="flex items-center gap-2">
            <span className="micro" style={{ color: "var(--t-faint)" }}>
              {uniqueCount} {plural(uniqueCount, "адрес", "адреса", "адресов")}
            </span>
            <div className="flex-1" />
            <input
              className="input text-xs"
              style={{ maxWidth: 180, paddingTop: 4, paddingBottom: 4 }}
              placeholder="Поиск по списку"
              value={search}
              onChange={e => setSearch(e.target.value)}
              aria-label="Поиск по списку"
            />
          </div>

          {searchLower && (
            <div className="rounded-md border p-2 flex flex-col gap-1"
                 style={{ borderColor: "var(--line-soft)", background: "var(--bg2)", maxHeight: 160, overflowY: "auto" }}>
              <span className="micro" style={{ color: "var(--t-faint)" }}>
                Совпадений: {matched.length} из {allLines.length}
              </span>
              {matched.length === 0 ? (
                <span className="hint" style={{ margin: 0 }}>Ничего не найдено</span>
              ) : (
                matched.map((m, i) => (
                  <span key={i} className="font-mono text-xs" style={{ color: "var(--t-mid)" }}>{m}</span>
                ))
              )}
            </div>
          )}

          <textarea className="input font-mono text-xs" rows={6} value={text}
            data-testid="f2b-textarea"
            onChange={e => { setText(e.target.value); setDirty(true); }}
            placeholder={"203.0.113.10\n198.51.100.0/24"} spellCheck={false} />
        </>
      )}

      {err && <p className="errmsg">{err}</p>}

      {/* Кнопки — всегда видимы. */}
      <div className="flex flex-wrap items-center gap-2">
        <Btn label="Сохранить список" icon={<Save size={13} />} variant="primary"
          onClick={save} loading={saving} disabled={loading} />
        <Btn label="Собрать с нод" icon={<Download size={13} />}
          onClick={() => sync("collect")} loading={busy === "collect"}
          disabled={loading || busy !== null || pullNodes.length === 0}
          title={pullNodes.length === 0 ? "Нет нод (localStorage deploy_jobs)" : "Собрать IP с отмеченных нод"} />
        <Btn label="Загрузить на ноды" icon={<Upload size={13} />}
          onClick={() => sync("push")} loading={busy === "push"}
          disabled={loading || busy !== null || pushNodes.length === 0}
          title={pushNodes.length === 0 ? "Нет нод (localStorage deploy_jobs)" : "Загрузить центральный список на отмеченные ноды"} />
        <Btn label="Импорт" icon={<FilePlus size={13} />}
          onClick={() => setImportOpen(v => !v)} disabled={loading} />
        <div style={{ position: "relative" }}>
          <Btn label="Экспорт" icon={<FileDown size={13} />}
            onClick={() => setExportOpen(v => !v)} disabled={loading || allLines.length === 0} />
          {exportOpen && (
            <div className="rounded-md border shadow-lg flex flex-col"
                 style={{ position: "absolute", top: "100%", left: 0, zIndex: 20, marginTop: 4,
                          background: "var(--bg1)", borderColor: "var(--line)" }}>
              {(["txt", "csv", "json"] as ExportFormat[]).map(fmt => (
                <button key={fmt} type="button" onClick={() => exportAs(fmt)}
                  className="text-xs px-3 py-2 text-left font-mono hover:bg-[var(--bg3)]"
                  style={{ color: "var(--t-mid)" }}>
                  .{fmt}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Импорт — inline-диалог. */}
      {importOpen && (
        <div className="rounded-md border p-3 flex flex-col gap-2"
             style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
          <div className="flex items-center justify-between">
            <span className="micro">Импорт списка</span>
            <button type="button" className="iconbtn" onClick={() => setImportOpen(false)} aria-label="Закрыть импорт">
              <X size={14} />
            </button>
          </div>
          <div className="flex items-center gap-2">
            <select className="input text-xs" value={importFormat}
              onChange={e => setImportFormat(e.target.value as ImportFormat)} aria-label="Формат импорта">
              <option value="txt">TXT (по строке)</option>
              <option value="csv">CSV (колонка ip/cidr)</option>
              <option value="json">JSON (массив)</option>
            </select>
            <span className="hint" style={{ margin: 0 }}>
              {importPreview
                ? `Распознано: ${importPreview.add.length}${importPreview.invalid.length ? ` · подозрительно: ${importPreview.invalid.length}` : ""}`
                : "Вставьте текст ниже"}
            </span>
          </div>
          <textarea className="input font-mono text-xs" rows={4} value={importText}
            onChange={e => setImportText(e.target.value)}
            placeholder="203.0.113.10&#10;198.51.100.0/24" spellCheck={false} />
          {importPreview && importPreview.invalid.length > 0 && (
            <div className="flex flex-col gap-0.5" style={{ maxHeight: 90, overflowY: "auto" }}>
              {importPreview.invalid.slice(0, 6).map((s, i) => (
                <span key={i} className="font-mono text-[11px]" style={{ color: "var(--warn)" }}>⚠ {s}</span>
              ))}
              {importPreview.invalid.length > 6 && (
                <span className="hint" style={{ margin: 0 }}>… и ещё {importPreview.invalid.length - 6}</span>
              )}
            </div>
          )}
          <div className="flex items-center gap-2">
            <Btn label="Слить в список" icon={<CheckCircle2 size={13} />} variant="primary"
              onClick={doImport} disabled={!importText.trim()} />
            <Btn label="Отмена" onClick={() => { setImportOpen(false); setImportText(""); }} />
          </div>
        </div>
      )}

      {/* Предложение слияния после «Собрать с нод». */}
      {lastOp === "collect" && collectedPreview && (
        <div className="rounded-md border p-3 flex flex-col gap-2"
             style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
          <span className="micro" style={{ color: "var(--t-faint)" }}>
            Собрано {collectedCount} уникальных адресов — в список пока не добавлены
          </span>
          <div className="flex flex-col gap-0.5 font-mono text-xs" style={{ maxHeight: 100, overflowY: "auto" }}>
            {collectedPreview.slice(0, 6).map((s, i) => (
              <span key={i} style={{ color: "var(--t-mid)" }}>{s}</span>
            ))}
            {collectedCount > 6 && (
              <span className="hint" style={{ margin: 0 }}>… и ещё {collectedCount - 6}</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Btn label={`Слить в список (${collectedCount})`} icon={<CheckCircle2 size={13} />} variant="primary"
              onClick={mergeCollected} />
            <Btn label="Отклонить" onClick={() => setCollectedPreview(null)} />
          </div>
        </div>
      )}

      {/* Сворачиваемая панель синхронизации с нодами. */}
      <details open={panelOpen} onToggle={e => setPanelOpen(e.currentTarget.open)}
        className="rounded-md border" style={{ borderColor: "var(--line-soft)" }}>
        <summary className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none"
                 style={{ color: "var(--t-mid)" }}>
          <ChevronDown size={14} style={{ transform: panelOpen ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform .15s" }} />
          <span className="micro">Синхронизация с нодами ({nodes.length})</span>
        </summary>

        <div className="px-3 pb-3 flex flex-col gap-2">
          {nodes.length === 0 ? (
            <p className="hint" style={{ margin: 0 }}>
              Нет нод — список деплоев (`deploy_jobs`) в этом браузере пуст.
            </p>
          ) : (
            <div className="flex flex-col gap-1">
              {nodes.map(n => {
                const pull = !pullOff.has(n.taskId);
                const push = !pushOff.has(n.taskId);
                return (
                  <div key={n.taskId} className="flex items-center gap-2 py-0.5">
                    <FlagChip code={n.country_code} size={15} />
                    <span className="text-xs flex-1 truncate" style={{ color: "var(--t-hi)" }}
                      title={`${n.label} (${n.ip})`}>
                      {n.label}
                      <span className="font-mono" style={{ color: "var(--t-faint)" }}> · {n.ip}</span>
                    </span>
                    <label className="flex items-center gap-1 cursor-pointer" title="Забрать (pull) список с этой ноды">
                      <input type="checkbox" checked={pull} onChange={() => toggle(setPullOff, n.taskId)}
                        className="accent-[var(--accent)]" />
                      <span className="text-[11px]" style={{ color: "var(--t-mid)" }}>забрать</span>
                    </label>
                    <label className="flex items-center gap-1 cursor-pointer" title="Отдать (push) центральный список на эту ноду">
                      <input type="checkbox" checked={push} onChange={() => toggle(setPushOff, n.taskId)}
                        className="accent-[var(--accent)]" />
                      <span className="text-[11px]" style={{ color: "var(--t-mid)" }}>отдать</span>
                    </label>
                  </div>
                );
              })}
            </div>
          )}

          {results && (
            <div className="flex flex-col gap-1 pt-1" style={{ borderTop: "1px solid var(--line-soft)" }}>
              {results.map((r, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  {r.ok === false ? (
                    <XCircle size={13} style={{ color: "var(--err)", flex: "none" }} />
                  ) : (
                    <CheckCircle2 size={13} style={{ color: "var(--ok)", flex: "none" }} />
                  )}
                  <span className="font-mono" style={{ color: "var(--t-mid)" }}>{r.ip ?? "?"}</span>
                  {lastOp === "collect" ? (
                    <span style={{ color: "var(--t-faint)" }}>+{(r.collected ?? r.ips ?? []).length}</span>
                  ) : (
                    <span style={{ color: "var(--t-faint)" }}>
                      прим. {r.applied ?? 0} / разбан. {r.unbanned ?? 0}
                    </span>
                  )}
                  {r.error && <span style={{ color: "var(--err)" }}>{r.error}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      </details>
    </div>
  );
}
