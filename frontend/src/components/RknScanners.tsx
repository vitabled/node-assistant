import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Loader2, Save, ScanSearch, Download, Upload, Trash2,
  CheckCircle2, XCircle, ChevronDown,
} from "lucide-react";
import { toast } from "./infra/Toast";
import { deployJobsKey } from "../auth/store";
import type { DeployJobSummary } from "./DeployDashboard";
import { FlagChip } from "./common/FlagChip";
import { InputShell, EmptyState, Table } from "../theme/ui";

/**
 * «RKNscanner» — соседний с Fail2Ban раздел: адреса сканеров, найденных на
 * нодах, + центральный сбор со всех нод и рассылка блок-листа.
 *
 * Контракт backend (`backend/app/api/rkn_scanners.py`):
 *   GET    /api/rkn-scanners   → {entries, total, updatedAt}
 *   POST   /api/rkn-scanners/save  {entries:[...]} → {ok, entries, total, updatedAt}
 *   POST   /api/rkn-scanners/sync  {nodes:[{ip,ssh_port,ssh_user,ssh_password,
 *                                           since_hours?, collect?, apply?}],
 *                                   merge_collected?} → {results:[{ip,ok,collected,hits,
 *                                   applied,inSet,error?}], total, updatedAt,
 *                                   merged:{added,updated,hitsAdded}}
 *   DELETE /api/rkn-scanners   → {ok, entries:[], total, updatedAt}
 *
 * ⚠️ Формы запроса/ответа `/sync` в шапке задачи и в реализации разошлись
 * (в реализации `apply`/`since_hours` — на каждой ноде, ответ — `results`+`merged`,
 * центральный список возвращается только отдельным GET). Экран отправляет ОБЕ
 * формы ключей и читает ОБА варианта ответа, чтобы работать и по факту, и по
 * словесному контракту; после сбора таблица всегда перечитывается через GET.
 *
 * Список нод — тот же механизм, что в F2bList: серверные карточки
 * (GET /api/deploy-jobs, ssh-креды расшифрованы сервером) поверх localStorage
 * `deploy_jobs_<account>` как офлайн-фолбэка.
 */

// ── типы ────────────────────────────────────────────────────────────────

export interface RknEntry {
  ip: string;
  firstSeen?: string | number;
  lastSeen?: string | number;
  hits?: number;
  nodes?: string[];
  port?: number;
  chain?: string;
  source?: string;
}

/** Строка отчёта по одной ноде (обе формы ответа сводятся к ней). */
interface SyncRow {
  ip?: string;
  ok: boolean;
  detail?: string;
}

interface SyncSummary {
  collected: number;
  created: number;
  appliedOk: number;
  nodes: number;
  total: number;
}

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

type SortKey = "lastSeen" | "hits";
type SortDir = "asc" | "desc";
type Busy = "collect" | "apply" | "save" | "clear" | null;

// ── helpers ──────────────────────────────────────────────────────────────

function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/** Терпимая нормализация ответа: не-массив → null (не затираем таблицу мусором). */
function normalizeEntries(v: unknown): RknEntry[] | null {
  if (!Array.isArray(v)) return null;
  return v
    .map(item => {
      if (typeof item === "string") return { ip: item };
      if (!item || typeof item !== "object") return null;
      const o = item as Record<string, unknown>;
      const ip = typeof o.ip === "string" ? o.ip.trim() : "";
      if (!ip) return null;
      return {
        ...o,
        ip,
        nodes: Array.isArray(o.nodes) ? (o.nodes as unknown[]).map(n => String(n)) : [],
        hits: num(o.hits),
      } as RknEntry;
    })
    .filter((e): e is RknEntry => e !== null);
}

/** Отчёт по нодам из ответа /sync: `results` (реализация) или `applied` (контракт). */
function readSyncRows(d: Record<string, unknown>): SyncRow[] {
  const raw = Array.isArray(d.results) ? d.results
    : Array.isArray(d.applied) ? d.applied : [];
  return raw.map(item => {
    const o = (item ?? {}) as Record<string, unknown>;
    const parts = [
      typeof o.collected === "number" ? `собрано ${o.collected}` : "",
      typeof o.hits === "number" && o.hits > 0 ? `попаданий ${o.hits}` : "",
      typeof o.applied === "number" ? `применено ${o.applied}` : "",
      typeof o.detail === "string" ? o.detail : "",
      typeof o.error === "string" ? o.error : "",
    ].filter(Boolean);
    return {
      ip: typeof o.ip === "string" ? o.ip : undefined,
      ok: o.ok !== false,
      detail: parts.length ? parts.join(" · ") : undefined,
    };
  });
}

/** Секунды (10 цифр) → миллисекунды; готовые ms и ISO-строки проходят как есть. */
function toMs(v: string | number | undefined): number | null {
  if (v === undefined || v === null || v === "") return null;
  if (typeof v === "number") return v < 1e12 ? v * 1000 : v;
  const t = Date.parse(v);
  if (!Number.isNaN(t)) return t;
  const n = Number(v);
  if (Number.isFinite(n) && n > 0) return n < 1e12 ? n * 1000 : n;
  return null;
}

/** Дата в локальном формате; неразобранное значение показываем как есть. */
function fmtTime(v: string | number | undefined): string {
  const ms = toMs(v);
  if (ms === null) return v === undefined || v === null || v === "" ? "—" : String(v);
  return new Date(ms).toLocaleString("ru-RU");
}

/** Русская плюрализация (1 сканер, 2 сканера, 5 сканеров). */
function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

/** Нестрогая проверка ip (IPv4 / IPv6-подобное) — только для ручного ввода. */
function isValidIp(s: string): boolean {
  return /^(\d{1,3})(\.\d{1,3}){3}$/.test(s) || /^[0-9a-fA-F:]{2,}$/.test(s);
}

/** Значение сортировки: отсутствующее время — «в самый конец» при desc. */
function sortValue(e: RknEntry, key: SortKey): number {
  if (key === "hits") return num(e.hits);
  return toMs(e.lastSeen) ?? 0;
}

/** Добавить/обновить запись по ip (ручной ввод). */
function upsert(list: RknEntry[], entry: RknEntry): RknEntry[] {
  const idx = list.findIndex(e => e.ip.toLowerCase() === entry.ip.toLowerCase());
  if (idx < 0) return [...list, entry];
  return list.map((e, i) => (i === idx ? { ...e, ...entry } : e));
}

function loadNodes(): NodeRef[] {
  let jobs: DeployJobSummary[] = [];
  try { jobs = JSON.parse(localStorage.getItem(deployJobsKey()) ?? "[]"); }
  catch { /* ignore malformed */ }
  if (!Array.isArray(jobs)) jobs = [];
  return nodesFromJobs(jobs);
}

/** Серверные карточки (GET /api/deploy-jobs) → ноды. savedForm приходит
 *  расшифрованным с ssh-кредами — сервер теперь источник правды. */
function nodesFromJobs(jobs: DeployJobSummary[]): NodeRef[] {
  return jobs
    .map(j => {
      const f = ((j.savedForm ?? {}) as NodeForm);
      const cur = parseInt(String(f.current_ssh_port ?? "22"), 10) || 22;
      const nxt = parseInt(String(f.new_ssh_port ?? "22"), 10) || 22;
      return {
        taskId: String(j.taskId ?? ""),
        label: (String(j.domain ?? "")).trim() || String(j.ip ?? ""),
        ip: String(j.ip ?? ""),
        ssh_user: f.ssh_user || "root",
        ssh_password: f.ssh_password || "",
        ssh_port: f.change_ssh_port ? nxt : cur,
        country_code: typeof f.country_code === "string" ? f.country_code.toUpperCase() : null,
      } as NodeRef;
    })
    .filter(n => !!n.ip && !!n.taskId);
}

// ── ActionButton-подобная кнопка (иконка+текст, primary/default/danger) ──
function Btn({ label, icon, onClick, variant = "default", disabled, loading, title }: {
  label: string;
  icon?: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger";
  disabled?: boolean;
  loading?: boolean;
  title?: string;
}) {
  const base = "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed select-none px-2.5 py-1.5 text-xs";
  const v = variant === "primary"
    ? "bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)]"
    : variant === "danger"
      ? "border border-[var(--err-line)] text-[var(--err)] hover:bg-[var(--err-dim)]"
      : "border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)] hover:text-[var(--t-hi)]";
  return (
    <button type="button" onClick={onClick} disabled={disabled || loading}
      title={title} className={`${base} ${v}`}>
      {loading ? <Loader2 size={13} className="animate-spin" /> : icon}
      <span>{label}</span>
    </button>
  );
}

// ── компонент ────────────────────────────────────────────────────────────

export function RknScanners() {
  const [entries, setEntries] = useState<RknEntry[]>([]);
  const [updatedAt, setUpdatedAt] = useState<string | number | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState<Busy>(null);

  // Поиск/фильтр + сортировка.
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("lastSeen");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // Ноды: сервер — источник правды (GET /api/deploy-jobs), localStorage — фолбэк.
  const [nodes, setNodes] = useState<NodeRef[]>(loadNodes);
  const [sinceHours, setSinceHours] = useState("24");
  const [nodesOpen, setNodesOpen] = useState(false);

  // Ручной ввод + подтверждения.
  const [manualIp, setManualIp] = useState("");
  const [manualPort, setManualPort] = useState("");
  const [manualChain, setManualChain] = useState("");
  const [confirmApply, setConfirmApply] = useState(false);
  const [clearStep, setClearStep] = useState(0);

  // Результаты последнего sync.
  const [summary, setSummary] = useState<SyncSummary | null>(null);
  const [rows, setRows] = useState<SyncRow[] | null>(null);

  /** Перечитать центральный список (после sync backend его не возвращает). */
  const refresh = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch("/api/rkn-scanners");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json() as { entries?: unknown; updatedAt?: string | number };
      const list = normalizeEntries(d?.entries);
      if (list) setEntries(list);
      setUpdatedAt(d?.updatedAt);
      return true;
    } catch {
      return false;
    }
  }, []);

  useEffect(() => {
    let alive = true;
    refresh().then(ok => {
      if (!alive) return;
      if (!ok) setErr("Не удалось загрузить список сканеров");
      setLoading(false);
    });
    return () => { alive = false; };
  }, [refresh]);

  // Свежий список карточек с сервера (с расшифрованными ssh-кредами) —
  // если пришёл, заменяет локальный; иначе остаётся localStorage-фолбэк.
  useEffect(() => {
    let alive = true;
    fetch("/api/deploy-jobs")
      .then(r => (r.ok ? r.json() : null))
      .then((d: { jobs?: DeployJobSummary[] } | null) => {
        if (!alive || !d || !Array.isArray(d.jobs) || d.jobs.length === 0) return;
        const serverNodes = nodesFromJobs(d.jobs as DeployJobSummary[]);
        if (serverNodes.length === 0) return;
        setNodes(prev => {
          const byId = new Map<string, NodeRef>();
          for (const n of serverNodes) byId.set(n.taskId, n);
          for (const n of prev) if (!byId.has(n.taskId)) byId.set(n.taskId, n);
          return [...byId.values()];
        });
      })
      .catch(() => { /* локальный фолбэк остаётся */ });
    return () => { alive = false; };
  }, []);

  const since = parseInt(sinceHours, 10);
  const sinceOk = Number.isFinite(since) && since > 0;

  const sync = async (apply: boolean) => {
    if (nodes.length === 0) { toast("Нет нод для сбора", "error"); return; }
    setBusy(apply ? "apply" : "collect"); setErr(""); setRows(null);
    try {
      const res = await fetch("/api/rkn-scanners/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nodes: nodes.map(n => ({
            ip: n.ip, ssh_port: n.ssh_port, ssh_user: n.ssh_user, ssh_password: n.ssh_password,
            // `since_hours` — реализация, `sinceHours` — словесный контракт раздела.
            ...(sinceOk ? { since_hours: since, sinceHours: since } : {}),
            // Раздача блок-листа — флаг на ноде (реализация) и общий флаг (контракт).
            collect: true, apply,
          })),
          apply,
          merge_collected: true,
        }),
      });
      const d = await res.json().catch(() => ({} as Record<string, unknown>));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }

      const body = d as Record<string, unknown>;
      const syncRows = readSyncRows(body);
      const merged = (body.merged ?? {}) as Record<string, unknown>;
      const collectedFromRows = Array.isArray(body.results)
        ? (body.results as Record<string, unknown>[]).reduce((s, r) => s + num(r?.collected), 0)
        : 0;

      setSummary({
        collected: num(body.collected) || collectedFromRows,
        created: num(body.new) || num(merged.added),
        appliedOk: syncRows.filter(r => r.ok).length,
        nodes: nodes.length,
        total: num(body.total),
      });
      setRows(syncRows);
      setNodesOpen(true);

      // Реализация возвращает лишь отчёт, а список правит на сервере — перечитываем;
      // если же ответ пришёл с entries (контракт), берём их как есть.
      const list = normalizeEntries(body.entries);
      if (list) setEntries(list);
      else if (!(await refresh())) setErr("Собрано, но список не перечитан — обновите страницу");

      setUpdatedAt(typeof body.updatedAt === "string" || typeof body.updatedAt === "number" ? body.updatedAt : Date.now());
      if (apply) toast(`Блок-лист разослан на ${nodes.length} ${plural(nodes.length, "ноду", "ноды", "нод")}`, "success");
      else toast(`Собрано ${num(body.collected) || collectedFromRows} записей`, "success");
      setConfirmApply(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка сбора");
    } finally { setBusy(null); }
  };

  const save = async () => {
    setBusy("save"); setErr("");
    try {
      let next = entries;
      const ip = manualIp.trim();
      if (ip && !isValidIp(ip)) { toast("Некорректный IP", "error"); return; }
      if (ip) {
        const port = parseInt(manualPort, 10);
        next = upsert(entries, {
          ip,
          ...(Number.isFinite(port) ? { port } : {}),
          ...(manualChain.trim() ? { chain: manualChain.trim() } : {}),
          source: "manual",
          firstSeen: Date.now(),
          lastSeen: Date.now(),
          hits: 1,
          nodes: [],
        });
      }
      const res = await fetch("/api/rkn-scanners/save", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: next }),
      });
      const d = await res.json().catch(() => ({} as Record<string, unknown>));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      const list = normalizeEntries(d.entries);
      setEntries(list ?? next);
      setManualIp(""); setManualPort(""); setManualChain("");
      setUpdatedAt(typeof d.updatedAt === "string" || typeof d.updatedAt === "number" ? d.updatedAt : Date.now());
      toast(`Сохранено (${(list ?? next).length})`, "success");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка сохранения");
    } finally { setBusy(null); }
  };

  const clearAll = async () => {
    setBusy("clear"); setErr("");
    try {
      const res = await fetch("/api/rkn-scanners", { method: "DELETE" });
      const d = await res.json().catch(() => ({} as Record<string, unknown>));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      setEntries(normalizeEntries(d.entries) ?? []);
      setSummary(null); setRows(null); setClearStep(0);
      toast("Список сканеров очищен", "success");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка очистки");
    } finally { setBusy(null); }
  };

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir(d => (d === "desc" ? "asc" : "desc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  const searchLower = search.trim().toLowerCase();
  const visible = useMemo(() => {
    const filtered = searchLower
      ? entries.filter(e =>
          [e.ip, e.chain ?? "", e.source ?? "", (e.nodes ?? []).join(" ")]
            .some(v => String(v).toLowerCase().includes(searchLower)))
      : entries;
    const dir = sortDir === "desc" ? -1 : 1;
    return [...filtered].sort((a, b) => {
      const d = sortValue(a, sortKey) - sortValue(b, sortKey);
      return d !== 0 ? d * dir : a.ip.localeCompare(b.ip);
    });
  }, [entries, searchLower, sortKey, sortDir]);

  const arrow = (key: SortKey) => (sortKey === key ? (sortDir === "desc" ? " ↓" : " ↑") : "");

  const busyAny = busy !== null;

  return (
    <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Заголовок — тот же паттерн, что у Fail2Ban list. */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <span style={{ width: 34, height: 34, borderRadius: 8, display: "grid", placeItems: "center", flex: "none",
          background: "var(--accent-dim)", border: "1px solid var(--accent-line)", color: "var(--accent-hi)" }}>
          <ScanSearch size={16} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <h1 style={{ fontSize: 15, fontWeight: 600, color: "var(--t-hi)", margin: 0 }}>RKNscanner</h1>
            {!loading && (
              <span className="chip accent" style={{ marginLeft: "auto", fontSize: 11 }}>
                {entries.length} {plural(entries.length, "сканер", "сканера", "сканеров")}
              </span>
            )}
          </div>
          <p className="sub" style={{ marginTop: 2 }}>
            Адреса сканеров, найденных на нодах. Центральный сбор со всех нод и рассылка
            блок-листа обратно на них. Обновлено: {fmtTime(updatedAt)}.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="ni-skeleton" style={{ height: 72 }} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <InputShell
              className="text-xs"
              style={{ maxWidth: 240, paddingTop: 4, paddingBottom: 4 }}
              placeholder="Поиск: IP, нода, цепочка"
              value={search}
              onChange={e => setSearch(e.target.value)}
              aria-label="Поиск по сканерам"
            />
            <span className="hint" style={{ margin: 0 }}>
              {searchLower ? `${visible.length} из ${entries.length}` : ""}
            </span>
          </div>

          {entries.length === 0 ? (
            <EmptyState
              compact
              icon={<ScanSearch size={16} />}
              title="Сканеры не найдены"
              hint="Нажмите «Собрать со всех нод» — центральный сбор опросит сохранённые карточки деплоев."
            />
          ) : (
            <div data-testid="rkn-table">
              <Table head={[
                "IP",
                "Впервые",
                <>Последний раз<button type="button" onClick={() => toggleSort("lastSeen")}
                  className="micro" style={{ marginLeft: 4, color: "var(--accent-hi)" }}
                  title="Сортировать по последнему появлению">сортировка{arrow("lastSeen")}</button></>,
                <>Попадания<button type="button" onClick={() => toggleSort("hits")}
                  className="micro" style={{ marginLeft: 4, color: "var(--accent-hi)" }}
                  title="Сортировать по числу попаданий">сортировка{arrow("hits")}</button></>,
                "Ноды",
                "Порт / цепочка",
              ]}>
                {visible.length === 0 ? (
                  <tr><td colSpan={6} className="hint" style={{ padding: 12 }}>Ничего не найдено</td></tr>
                ) : visible.map(e => (
                  <tr key={e.ip}>
                    <td className="font-mono" style={{ color: "var(--t-hi)" }}>{e.ip}</td>
                    <td style={{ color: "var(--t-mid)" }}>{fmtTime(e.firstSeen)}</td>
                    <td style={{ color: "var(--t-mid)" }}>{fmtTime(e.lastSeen)}</td>
                    <td className="font-mono" style={{ color: num(e.hits) > 0 ? "var(--warn)" : "var(--t-faint)" }}>
                      {num(e.hits)}
                    </td>
                    <td style={{ color: "var(--t-mid)" }}>
                      {(e.nodes ?? []).length === 0
                        ? <span className="hint" style={{ margin: 0 }}>—</span>
                        : (
                          <span className="flex flex-wrap items-center gap-1">
                            {(e.nodes ?? []).map(n => (
                              <span key={n} className="chip neutral" style={{ fontSize: 10 }}>{n}</span>
                            ))}
                          </span>
                        )}
                    </td>
                    <td className="font-mono" style={{ color: "var(--t-mid)" }}>
                      {e.chain ?? "—"}{e.port ? ` :${e.port}` : ""}
                    </td>
                  </tr>
                ))}
              </Table>
            </div>
          )}
        </>
      )}

      {err && <p className="errmsg">{err}</p>}

      {/* Ручной ввод — попадает в центральный список при «Сохранить вручную». */}
      <div className="flex flex-wrap items-center gap-2">
        <InputShell className="text-xs" style={{ width: 150 }}
          placeholder="IP вручную" value={manualIp} aria-label="IP вручную"
          onChange={e => setManualIp(e.target.value)} />
        <InputShell className="text-xs" style={{ width: 80 }}
          placeholder="Порт" value={manualPort} aria-label="Порт вручную"
          onChange={e => setManualPort(e.target.value)} />
        <InputShell className="text-xs" style={{ width: 130 }}
          placeholder="Цепочка" value={manualChain} aria-label="Цепочка вручную"
          onChange={e => setManualChain(e.target.value)} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Btn label="Собрать со всех нод" icon={<Download size={13} />}
          onClick={() => sync(false)} loading={busy === "collect"}
          disabled={loading || busyAny || nodes.length === 0}
          title={nodes.length === 0 ? "Нет нод — карточки деплоев в этом браузере пусты" : "Опрос всех сохранённых нод"} />
        <Btn label="Собрать и разослать блок-лист" icon={<Upload size={13} />}
          onClick={() => (confirmApply ? sync(true) : setConfirmApply(true))} loading={busy === "apply"}
          disabled={loading || busyAny || nodes.length === 0}
          title="Сбор со всех нод + применение блок-листа на них" />
        <Btn label="Сохранить вручную" icon={<Save size={13} />} variant="primary"
          onClick={save} loading={busy === "save"} disabled={loading || busyAny} />
        <Btn label="Очистить" icon={<Trash2 size={13} />} variant="danger"
          onClick={() => { if (clearStep === 0) setClearStep(1); else clearAll(); }}
          loading={busy === "clear"} disabled={loading || busyAny || entries.length === 0}
          title="Удалить весь центральный список сканеров" />
      </div>

      {/* Подтверждение рассылки (одно нажатие + «Подтвердить»). */}
      {confirmApply && (
        <div className="rounded-md border p-3 flex flex-col gap-2"
             style={{ borderColor: "var(--warn-line)", background: "var(--warn-dim)" }}>
          <span className="text-xs" style={{ color: "var(--warn)" }}>
            Собрать со всех нод и применить блок-лист на {nodes.length} {plural(nodes.length, "ноде", "нодах", "нодах")}?
          </span>
          <div className="flex items-center gap-2">
            <Btn label="Подтвердить рассылку" icon={<CheckCircle2 size={13} />} variant="primary"
              onClick={() => sync(true)} loading={busy === "apply"} />
            <Btn label="Отмена" onClick={() => setConfirmApply(false)} />
          </div>
        </div>
      )}

      {/* Двойное подтверждение очистки. */}
      {clearStep === 1 && (
        <div className="rounded-md border p-3 flex flex-col gap-2"
             style={{ borderColor: "var(--err-line)", background: "var(--err-dim)" }}>
          <span className="text-xs" style={{ color: "var(--err)" }}>
            Удалить все {entries.length} {plural(entries.length, "запись", "записи", "записей")} из центрального списка?
          </span>
          <div className="flex items-center gap-2">
            <Btn label="Да, очистить полностью" icon={<Trash2 size={13} />} variant="danger"
              onClick={clearAll} loading={busy === "clear"} />
            <Btn label="Отмена" onClick={() => setClearStep(0)} />
          </div>
        </div>
      )}

      {/* Итоги sync + отчёт по нодам. */}
      {summary && (
        <div className="rounded-md border p-3 flex flex-col gap-2"
             style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
          <span className="micro" style={{ color: "var(--t-faint)" }}>
            Собрано {summary.collected} · новых {summary.created} · нод {summary.nodes}
            {rows && rows.length > 0 ? ` · разослано на ${summary.appliedOk} ${plural(summary.appliedOk, "ноду", "ноды", "нод")}` : ""}
          </span>
          {rows && rows.length > 0 && (
            <div className="flex flex-col gap-1 pt-1" style={{ borderTop: "1px solid var(--line-soft)" }}>
              {rows.map((r, i) => (
                <div key={`${r.ip ?? "?"}-${i}`} className="flex items-center gap-2 text-xs">
                  {r.ok
                    ? <CheckCircle2 size={13} style={{ color: "var(--ok)", flex: "none" }} />
                    : <XCircle size={13} style={{ color: "var(--err)", flex: "none" }} />}
                  <span className="font-mono" style={{ color: "var(--t-mid)" }}>{r.ip ?? "?"}</span>
                  {r.detail && <span style={{ color: r.ok ? "var(--t-faint)" : "var(--err)" }}>{r.detail}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Ноды (тот же источник, что в Fail2Ban): сервер + localStorage-фолбэк. */}
      <details open={nodesOpen} onToggle={e => setNodesOpen(e.currentTarget.open)}
        className="rounded-md border" style={{ borderColor: "var(--line-soft)" }}>
        <summary className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none"
                 style={{ color: "var(--t-mid)" }}>
          <ChevronDown size={14} style={{ transform: nodesOpen ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform .15s" }} />
          <span className="micro">Ноды для сбора ({nodes.length})</span>
        </summary>
        <div className="px-3 pb-3 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-xs" style={{ color: "var(--t-mid)" }}>за последние</span>
            <InputShell className="text-xs" style={{ width: 70 }} aria-label="Часов назад"
              value={sinceHours} onChange={e => setSinceHours(e.target.value)} />
            <span className="text-xs" style={{ color: "var(--t-mid)" }}>ч</span>
          </div>
          {nodes.length === 0 ? (
            <p className="hint" style={{ margin: 0 }}>
              Нет нод — список деплоев (`deploy_jobs`) в этом браузере пуст.
            </p>
          ) : nodes.map(n => (
            <div key={n.taskId} className="flex items-center gap-2 py-0.5"
              style={{ borderLeft: "2px solid var(--line)", paddingLeft: 8 }}>
              <FlagChip code={n.country_code} size={15} />
              <span className="text-xs flex-1 truncate" style={{ color: "var(--t-hi)" }}
                title={`${n.label} (${n.ip})`}>
                {n.label}
                <span className="font-mono" style={{ color: "var(--t-faint)" }}> · {n.ip}</span>
              </span>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
