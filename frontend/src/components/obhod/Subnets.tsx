import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, ArrowDownToLine, BookOpen, Check, ChevronDown, ChevronLeft, ChevronRight, Download, FolderKanban, GripVertical, ImageUp, Loader2, Pencil, Plus, RefreshCw, Sparkles, Table2,
  Trash2, Upload, X,
} from "lucide-react";
import { Page, PageHeader } from "../../theme/ui";
import { toast } from "../infra/Toast";

/**
 * «Подсети» (Обходы БС, Wave-5 PR-5): справочник подсетей/IP.
 * Дерево: Провайдер → Списки. Список — таблица (подсеть, версия IP, ASN,
 * название ASN, дата, Операторы + пользовательские столбцы). Кнопка
 * «Редактировать таблицу» включает режим правки: операции со столбцами
 * (добавить/переименовать/удалить/перетащить) и чекбоксы иконок операторов.
 * ASN дозаполняется автоматически через backend (ip-api).
 *
 * Latency Lab: кнопка «Скан Latency» (видна, если интеграция включена в
 * настройках) открывает панель — выбор строк/оператора, асинхронный job с
 * поллингом статуса и выводом результата.
 *
 * Импорт/экспорт: кнопка «Импорт/экспорт» открывает панель — выгрузка списка
 * в JSON/CSV/TXT/Excel (скачивание через fetch→blob, чтобы уехал bearer-токен)
 * и загрузка файла (merge/replace, опционально в новый список) с показом итога.
 */

interface Col { key: string; title: string }
interface Op { key: string; label: string }
interface Row { id: string; values: Record<string, string>; operators: Record<string, boolean> }
interface Lst { id: string; name: string; icon?: string; columns: Col[]; rows: Row[] }
interface Prov { id: string; name: string; icon?: string; lists: Lst[] }

interface LatOp { id: string; label: string; online: boolean; configured: boolean }
interface ScanItem {
  row_id?: string; subnet?: string; operator?: string;
  alive_count?: number; available?: boolean; status_text?: string; reachable_ips?: string[];
}
interface ScanResult extends ScanItem { rows?: ScanItem[] }
type ScanStatus = "pending" | "done" | "cancelled" | "error";

/** Статус скана конкретной строки: значок слева от подсети. */
type RowScanState = "none" | "running" | "ok" | "unavailable" | "error";

/** Одна порция скана: все req_id, которые вернул один POST /latency-scan.
 *  rowIds — строки, которые реально ушли в порцию НА МОМЕНТ СТАРТА (для
 *  all:true — все строки списка тогда). Состав фиксируется при старте,
 *  чтобы статусы не пересчитывались от текущего выбора (после отмены
 *  новые выбранные строки не должны получать статусы старого скана).
 *  Порция готова, когда готовы ВСЕ её req_id. */
interface ScanPart { reqIds: string[]; rowIds: string[]; status: ScanStatus }

type ExportFormat = "json" | "csv" | "txt" | "xlsx";
type ImportMode = "merge" | "replace";
interface ImportResult { imported: number; skipped: number; errors: string[] }

/** Цвет строк таблицы: off — цветов нет; groups — только при группировке
 *  (заголовки групп + строки групп); all — строки окрашены по провайдеру
 *  даже в плоском списке. В режиме правки цветов нет всегда. */
type ColorMode = "off" | "groups" | "all";

/** Запись справочника ASN (GET /api/subnets/asns). icon — имя файла иконки
 *  записи (если загружена; файл отдаёт GET /asns/{asn}/icon). netname —
 *  имя сети из строк подсетей (переносится в values.netname через apply). */
interface AsnRec { asn: string; name: string; note?: string; icon?: string; netname?: string }

const api = (path: string, init?: RequestInit) =>
  fetch(`/api/subnets${path}`, init ? { headers: { "Content-Type": "application/json" }, ...init } : init);

/** Потолок пачки на бэкенде — фронт режет большие выборки на порции по 750. */
const SCAN_CHUNK = 750;

/** Порции row_id'ов для скана. «Все строки» до 750 включительно — один пустой
 *  чанк (= all:true на бэкенде); больше (или выбранные строки) — по 750. */
function scanChunks(rows: Row[], picked: string[]): string[][] {
  const all = picked.length === 0;
  const targets = all ? rows.map(r => r.id) : picked;
  if (all && targets.length <= SCAN_CHUNK) return [[]];
  const out: string[][] = [];
  for (let i = 0; i < targets.length; i += SCAN_CHUNK)
    out.push(targets.slice(i, i + SCAN_CHUNK));
  return out;
}

const ICON_BOX = { width: 12, height: 12, flex: "none" } as const;

/** IPv4 → uint32 (беззнаковый) или null (IPv6/мусор). */
function ipv4ToUint32(ip: string): number | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec((ip ?? "").trim());
  if (!m) return null;
  const o = m.slice(1).map(Number);
  if (o.some(x => x > 255)) return null;
  return ((((o[0] << 24) | (o[1] << 16) | (o[2] << 8) | o[3])) >>> 0);
}

/** uint32 → «a.b.c.d». */
function uint32ToIpv4(n: number): string {
  return `${(n >>> 24) & 255}.${(n >>> 16) & 255}.${(n >>> 8) & 255}.${n & 255}`;
}

/** Минимальная CIDR-подсеть, покрывающая все переданные IP (агрегация живых
 *  адресов результата скана). IPv4: uint32 + первый отличающийся бит
 *  (Math.clz32(xor) = общий префикс). IPv6, смесь версий или пусто → null.
 *  Примеры: [10.0.0.1, 10.0.0.10] → 10.0.0.0/28; [10.0.0.1, 10.0.1.1] →
 *  10.0.0.0/23; один IP → сам как /32. */
export function aggregateSubnet(ips: string[]): string | null {
  if (!Array.isArray(ips) || ips.length === 0) return null;
  let min = 0xFFFFFFFF;
  let max = 0;
  for (const ip of ips) {
    const n = ipv4ToUint32(ip);
    if (n === null) return null; // IPv6 или мусор — агрегировать не умеем
    if (n < min) min = n;
    if (n > max) max = n;
  }
  const diff = (min ^ max) >>> 0;
  const prefix = diff === 0 ? 32 : Math.clz32(diff);
  const mask = prefix === 0 ? 0 : (~0 << (32 - prefix)) >>> 0;
  return `${uint32ToIpv4((min & mask) >>> 0)}/${prefix}`;
}

/** Подсеть строки результата скана для показа:
 *  - точечный IP (subnet без «/N») — как есть, с пометкой host (/32);
 *  - сеть с живыми IP — минимальная CIDR-агрегация reachable_ips, если она
 *    уже исходной (жива половина /24 → показываем /25). Данные не меняются —
 *    только отображение результата скана. */
function scanSubnetDisplay(it: ScanItem, fallback: string):
  { label: string; host: boolean; orig: string } {
  const orig = it.subnet ?? fallback ?? "";
  const host = !!orig && !orig.includes("/");
  if (host) return { label: orig, host: true, orig };
  const agg = Array.isArray(it.reachable_ips) && it.reachable_ips.length > 0
    ? aggregateSubnet(it.reachable_ips) : null;
  return { label: agg && agg !== orig ? agg : orig, host: false, orig };
}

/** Канонический статус job'а Latency Lab: API местами отдаёт синонимы
 *  («success» вместо «done», «failed» вместо «error») — поллинг ждёт ровно
 *  done/error/cancelled, иначе скан с одной подсетью крутился бы вечно. */
function normalizeScanStatus(s: unknown): ScanStatus {
  const st = String(s ?? "").trim().toLowerCase();
  if (st === "done" || st === "success" || st === "completed"
    || st === "finished" || st === "ok") return "done";
  if (st === "error" || st === "failed") return "error";
  if (st === "cancelled" || st === "canceled") return "cancelled";
  if (!st) return "error"; // статуса нет — считаем ошибкой (как раньше ?? "error")
  return "pending"; // running/queued/… — ещё идёт, поллим дальше
}

/** Значок статуса скана строки. Только визуал: родитель гасит pointer-events,
 *  чтобы значок не перехватывал клики строки/выделения/редактирования. */
function ScanRowIcon({ id, state }: { id: string; state: RowScanState }) {
  const tid = `scan-icon-${id}-${state}`;
  if (state === "running")
    return <Loader2 size={11} className="spin" data-testid={tid}
      style={{ ...ICON_BOX, color: "var(--t-low)" }} />;
  if (state === "ok")
    return <Check size={12} data-testid={tid}
      style={{ ...ICON_BOX, color: "var(--ok)" }} />;
  if (state === "unavailable" || state === "error")
    return <X size={12} data-testid={tid}
      style={{ ...ICON_BOX, color: "var(--err)" }} />;
  // нет скана — тонкий плейсхолдер, держит ширину колонки
  return <span data-testid={tid} style={ICON_BOX} />;
}

const NO_PROVIDER_KEY = "__none__";

/** Палитра групп (по индексу группы): контрастные акценты, хорошо различимые
 *  и на светлом, и на тёмном фоне. Используются как АКЦЕНТЫ: фон заголовка
 *  группы — rgba(цвет, 0.14), строк — rgba(цвет, 0.06), текст/иконка/полоса
 *  слева — полным цветом. */
const GROUP_COLORS = [
  "#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#14b8a6",
];

/** HEX → rgba(..., alpha) — лёгкий фон строк группы (без opacity на весь tr). */
function hexToRgba(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/** Чип колонки «Тип ASN»: isp=синий (accent), hosting=зелёный (ok), business=янтарный (warn). */
const ASN_TYPE_META: Record<string, { label: string; cls: string }> = {
  isp: { label: "ISP", cls: "accent" },
  hosting: { label: "Hosting", cls: "ok" },
  business: { label: "Business", cls: "warn" },
};

/** Группирует строки по провайдеру (row.values.provider): сортировка по
 *  имени (ru), пустой/отсутствующий провайдер — «Без провайдера» последней.
 *  Каждая группа получает цвет из палитры по индексу (до сортировки — цвет
 *  стабилен и не зависит от порядка отображения). */
function groupRows(rows: Row[]) {
  const byProvider = new Map<string, Row[]>();
  for (const r of rows) {
    const p = (r.values?.provider ?? "").trim();
    const key = p || NO_PROVIDER_KEY;
    const arr = byProvider.get(key);
    if (arr) arr.push(r);
    else byProvider.set(key, [r]);
  }
  return [...byProvider.entries()]
    .map(([id, rs], i) => ({
      id,
      label: id === NO_PROVIDER_KEY ? "Без провайдера" : id,
      rows: rs,
      color: GROUP_COLORS[i % GROUP_COLORS.length],
    }))
    .sort((a, b) => {
      if (a.id === NO_PROVIDER_KEY) return 1;
      if (b.id === NO_PROVIDER_KEY) return -1;
      return a.label.localeCompare(b.label, "ru");
    });
}

export function Subnets() {
  const [providers, setProviders] = useState<Prov[]>([]);
  const [operators, setOperators] = useState<Op[]>([]);
  const [sel, setSel] = useState<{ pid: string; lid: string } | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [newSubnet, setNewSubnet] = useState("");
  const [busy, setBusy] = useState(false);
  const dragCol = useRef<string | null>(null);
  // UX: рамка дерева провайдеров/списков сворачивается в узкую полоску-переключатель.
  const [treeOpen, setTreeOpen] = useState(true);
  // Группировка строк таблицы по провайдеру (по умолчанию выключена —
  // плоский список). Свёрнутые группы: Set id'ов, по умолчанию всё развёрнуто.
  const [groupByProvider, setGroupByProvider] = useState(false);
  // Цвет строк: off/groups/all (по умолчанию «groups» — как было раньше).
  const [colorMode, setColorMode] = useState<ColorMode>("groups");
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  // Справочник ASN (per-account): asn → название, синхронизируется с asnname
  // в строках подсетей на сервере при upsert. Кнопка «Справочник» под деревом
  // открывает справа полную таблицу (asnView) — как таблица подсетей.
  const [asns, setAsns] = useState<AsnRec[]>([]);
  const [asnNewAsn, setAsnNewAsn] = useState("");
  const [asnNewName, setAsnNewName] = useState("");
  const [asnNewNetname, setAsnNewNetname] = useState("");
  const [asnView, setAsnView] = useState(false);
  const [syncBusy, setSyncBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);

  // ── Latency Lab ──
  const [latEnabled, setLatEnabled] = useState(false);
  const [latOps, setLatOps] = useState<LatOp[]>([]);
  const [scanOpen, setScanOpen] = useState(false);
  const [scanOp, setScanOp] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [scanParts, setScanParts] = useState<ScanPart[]>([]);
  const [scanErrors, setScanErrors] = useState<string[]>([]);
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanBusy, setScanBusy] = useState(false);
  // «Отменено во время отправки порций»: cancelScan ставит, цикл отправки
  // проверяет между POST'ами (актуальное state в замыкании не прочитать).
  const cancelRef = useRef(false);
  // Защита от наложения тиков поллинга (медленная сеть, много req_id).
  const pollBusy = useRef(false);

  // ── Импорт/экспорт ──
  const [ioOpen, setIoOpen] = useState(false);
  const [expFormat, setExpFormat] = useState<ExportFormat>("json");
  const [impMode, setImpMode] = useState<ImportMode>("merge");
  const [impNewList, setImpNewList] = useState(false);
  const [impFile, setImpFile] = useState<File | null>(null);
  const [impBusy, setImpBusy] = useState(false);
  const [impResult, setImpResult] = useState<ImportResult | null>(null);
  const filePick = useRef<HTMLInputElement | null>(null);
  // «Разметить провайдеров»: обогащение всех неразмеченных строк списка.
  const [enrichBusy, setEnrichBusy] = useState(false);
  const [enrichResult, setEnrichResult] = useState<{ updated: number; of: number; skipped: number } | null>(null);
  // «Типы ASN»: эвристика по текущим данным строк (без ip-api).
  const [typesBusy, setTypesBusy] = useState(false);
  const [typesResult, setTypesResult] = useState<{ updated: number; of: number } | null>(null);
  // Загрузка иконки ASN: один скрытый input, цель (номер ASN) — в ref.
  const iconInput = useRef<HTMLInputElement | null>(null);
  const iconAsnTarget = useRef<string | null>(null);

  const load = useCallback(() => {
    api("").then(r => r.json()).then(d => {
      setProviders(d.providers || []);
      setOperators(d.operators || []);
    }).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const loadAsns = useCallback(() => {
    api("/asns").then(r => r.json()).then(d => {
      setAsns(Array.isArray(d?.asns) ? d.asns : []);
    }).catch(() => setAsns([]));
  }, []);
  useEffect(() => { loadAsns(); }, [loadAsns]);

  // Интеграция может быть выключена — тогда UI скана не показываем вовсе.
  useEffect(() => {
    fetch("/api/latency/config").then(r => r.json()).then(d => {
      setLatEnabled(!!d?.enabled);
      if (d?.default_operator) setScanOp(String(d.default_operator));
    }).catch(() => {});
  }, []);

  const current: Lst | null = sel
    ? providers.find(p => p.id === sel.pid)?.lists.find(l => l.id === sel.lid) ?? null
    : null;

  // В режиме правки группировка игнорируется — правка строк идёт по плоскому
  // списку как раньше; toggle на шапке при этом заблокирован.
  const grouped = groupByProvider && !editMode;
  const groups = grouped ? groupRows(current?.rows ?? []) : [];
  // Цвета активны вне режима правки (и «off», и editMode гасят всё).
  const colored = colorMode !== "off" && !editMode;

  // Цвет строки в плоском списке (colorMode === "all"): по провайдеру строки.
  // Та же палитра и тот же порядок назначения, что у групп (первое появление
  // провайдера в списке), поэтому цвета не «прыгают» при переключении режимов.
  const rowColor = useMemo(() => {
    const m = new Map<string, string>();
    let i = 0;
    for (const r of current?.rows ?? []) {
      const p = (r.values?.provider ?? "").trim();
      if (!p || m.has(p)) continue;
      m.set(p, GROUP_COLORS[i++ % GROUP_COLORS.length]);
    }
    return m;
  }, [current]);

  // asn → запись справочника: fallback asnname (когда у строки пустое
  // значение) + иконка ASN для строк таблицы (если у записи она есть).
  const asnMap = useMemo(() => {
    const m = new Map<string, AsnRec>();
    for (const a of asns) m.set(a.asn, a);
    return m;
  }, [asns]);

  const toggleGroup = (id: string) =>
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // Статус скана для каждой строки: derived из порций + объединённого
  // результата. Состав скана зафиксирован при старте (ScanPart.rowIds):
  // текущий выбор (picked) на статусы НЕ влияет — строки вне скана всегда
  // «none», после отмены значки сбрасываются в «none» (отмена ≠ ошибка).
  const rowScanStatus = useMemo(() => {
    const map = new Map<string, RowScanState>();
    if (!current) return map;
    const rows = current.rows;
    const bySubnet = new Map(rows.map(r => [r.values?.subnet ?? "", r.id]));
    const resultRows = new Map<string, ScanItem>();
    const list = Array.isArray(scanResult?.rows) ? scanResult.rows
      : scanResult ? [scanResult] : [];
    for (const it of list) {
      const rid = it?.row_id ?? (it?.subnet ? bySubnet.get(it.subnet) : undefined);
      if (rid) resultRows.set(rid, it);
    }
    const errSubs = new Set(scanErrors.map(e => String(e).split(":")[0].trim()));
    // Порция, покрывающая строку, — по rowIds, замороженным при старте.
    const partOf = (rid: string): ScanPart | undefined =>
      scanParts.find(p => p.rowIds.includes(rid));
    for (const r of rows) {
      const subnet = r.values?.subnet ?? "";
      if (errSubs.has(subnet)) { map.set(r.id, "error"); continue; }
      const part = partOf(r.id);
      if (!part) { map.set(r.id, "none"); continue; } // строка не в скане
      if (part.status === "error") { map.set(r.id, "error"); continue; }
      if (part.status === "cancelled") { map.set(r.id, "none"); continue; } // отмена → сброс
      if (scanStatus === "pending" && part.status !== "done") {
        map.set(r.id, "running"); continue;
      }
      const it = resultRows.get(r.id);
      if (it) { map.set(r.id, it.available ? "ok" : "unavailable"); continue; }
      // порция завершилась, но замера строки нет — скан не дал ответа
      if (scanStatus === "done" || scanStatus === "error") {
        map.set(r.id, "error"); continue;
      }
      map.set(r.id, "none");
    }
    return map;
  }, [current, scanStatus, scanResult, scanParts, scanErrors]);

  // Строка таблицы — общая для плоского списка и групп (группы — только
  // визуальная обёртка: добавление/выделение/редактирование не меняются).
  // groupColor — цвет строки (лёгкий rgba-фон акцента группы/провайдера).
  const renderRow = (r: Row, groupColor?: string) => {
    return (
    <tr key={r.id} data-testid={`subnets-row-${r.id}`}
      style={groupColor ? { background: hexToRgba(groupColor, 0.06) } : undefined}>
      {scanOpen && (
        <td>
          <input type="checkbox" data-testid={`latency-pick-${r.id}`}
            aria-label={`Выбрать ${r.values?.subnet ?? r.id}`}
            checked={picked.includes(r.id)}
            onChange={() => togglePick(r.id)} />
        </td>
      )}
      {current!.columns.map(c => (
        <td key={c.key}>
          {c.key === "operators" ? (
            <span className="flex items-center gap-1.5">
              {operators.map(op => (
                editMode ? (
                  <label key={op.key} className="flex items-center gap-0.5"
                    title={`${op.label}: показывать иконку`}
                    style={{ cursor: "pointer" }}>
                    <input type="checkbox"
                      checked={r.operators?.[op.key] !== false}
                      onChange={e => void mutate(
                        `/providers/${sel!.pid}/lists/${sel!.lid}/rows/${r.id}/operator/${op.key}`,
                        "PATCH", { on: e.target.checked })} />
                    <OpIcon op={op.key} dim={r.operators?.[op.key] === false} />
                  </label>
                ) : (
                  r.operators?.[op.key] !== false && <OpIcon key={op.key} op={op.key} />
                )
              ))}
            </span>
          ) : c.key === "subnet" ? (
            (() => {
              // Иконка ASN слева от подсети: только если values.asn есть в
              // справочнике и у записи загружена иконка (иконки теперь
              // задаются у ASN, а не у провайдеров/списков).
              const asn = (r.values?.asn ?? "").trim();
              const rec = asn ? asnMap.get(asn) : undefined;
              return (
              <span className="flex items-center gap-1.5" style={{ pointerEvents: "none" }}>
                {rec?.icon && (
                  <img src={`/api/subnets/asns/${encodeURIComponent(asn)}/icon`} alt=""
                    width={14} height={14} data-testid={`asn-row-icon-${r.id}`}
                    style={{ borderRadius: 3, objectFit: "contain", flex: "none" }} />
                )}
                <ScanRowIcon id={r.id} state={rowScanStatus.get(r.id) ?? "none"} />
                <span className="trunc" title={r.values?.[c.key] || ""}
                  style={{ color: "var(--t-hi)" }}>
                  {r.values?.[c.key] || "—"}
                </span>
              </span>
              );
            })()
          ) : c.key === "asn_type" ? (
            <span>
              {r.values?.[c.key] ? (
                <span className={`chip ${ASN_TYPE_META[r.values[c.key]]?.cls ?? "neutral"}`}
                  style={{ fontSize: 10 }}
                  data-testid={`asn-type-${r.id}`}>
                  {ASN_TYPE_META[r.values[c.key]]?.label ?? r.values[c.key]}
                </span>
              ) : "—"}
            </span>
          ) : (
            <span className="trunc" title={r.values?.[c.key] || ""}
              style={{ color: "var(--t-mid)" }}>
              {/* asnname: пустое значение строки → название из справочника ASN */}
              {c.key === "asnname"
                ? (r.values?.[c.key] || asnMap.get(r.values?.asn ?? "")?.name || "—")
                : (r.values?.[c.key] || "—")}
            </span>
          )}
        </td>
      ))}
      <td>
        <button className="iconbtn danger" title="Удалить строку"
          onClick={() => void mutate(
            `/providers/${sel!.pid}/lists/${sel!.lid}/rows/${r.id}`, "DELETE")}>
          <Trash2 size={12} />
        </button>
      </td>
    </tr>
    );
  };

  const mutate = async (path: string, method: string, body?: unknown, msg?: string) => {
    const res = await api(path, { method, body: body !== undefined ? JSON.stringify(body) : undefined });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
      return false;
    }
    if (msg) toast(msg, "success");
    load();
    return true;
  };

  // ── Справочник ASN ───────────────────────────────────────────
  // После любого изменения — load() (сервер при upsert переписал asnname
  // в строках подсетей) + loadAsns() (сам справочник).
  const mutateAsn = async (path: string, method: string, body?: unknown, msg?: string) => {
    const res = await api(path, { method, body: body !== undefined ? JSON.stringify(body) : undefined });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
      return false;
    }
    if (msg) toast(msg, "success");
    load();
    loadAsns();
    return true;
  };
  const addAsn = () => {
    const asn = asnNewAsn.trim();
    if (!asn) return;
    void mutateAsn("/asns", "POST",
      { asn, name: asnNewName.trim(), netname: asnNewNetname.trim() }, "ASN добавлен")
      .then(ok => { if (ok) { setAsnNewAsn(""); setAsnNewName(""); setAsnNewNetname(""); } });
  };
  const editAsn = (a: AsnRec) => {
    const name = window.prompt("Название", a.name) ?? a.name;
    const netname = window.prompt("Netname", a.netname ?? "") ?? (a.netname ?? "");
    const note = window.prompt("Примечание", a.note ?? "") ?? (a.note ?? "");
    void mutateAsn("/asns", "POST", { asn: a.asn, name, netname, note }, "ASN обновлён");
  };
  const removeAsn = (a: AsnRec) => {
    if (!window.confirm(`Удалить ASN «${a.asn}» из справочника?`)) return;
    void mutateAsn(`/asns/${encodeURIComponent(a.asn)}`, "DELETE", undefined, "ASN удалён");
  };
  const applyAsns = async () => {
    // Перенести ВЕСЬ справочник в строки подсетей (POST /asns/apply):
    // name → values.asnname, netname → values.netname у всех строк с этим
    // ASN. Справочник авторитетнее — значения перезаписываются.
    setApplyBusy(true);
    try {
      const res = await api("/asns/apply", { method: "POST" });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
        return;
      }
      toast(`Применено к ${d.updated_rows ?? 0} строкам`, "success");
      loadAsns();
      load();
    } finally {
      setApplyBusy(false);
    }
  };
  const syncAsns = async () => {
    // Собрать ASN/названия из ВСЕХ списков подсетей в справочник (POST
    // /asns/sync): добавляет отсутствующие, заполняет пустые name, не
    // перезаписывает существующие. Строки подсетей сервер не меняет.
    setSyncBusy(true);
    try {
      const res = await api("/asns/sync", { method: "POST" });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
        return;
      }
      toast(`Синхронизировано: добавлено ${d.added ?? 0}, заполнено ${d.filled ?? 0}`, "success");
      loadAsns();
      load();
    } finally {
      setSyncBusy(false);
    }
  };

  const addProvider = () => {
    const name = window.prompt("Название провайдера", "Новый провайдер");
    if (name) void mutate("/providers", "POST", { name });
  };
  const addList = (pid: string) => {
    const name = window.prompt("Название списка", "Новый список");
    if (name) void mutate(`/providers/${pid}/lists`, "POST", { name });
  };
  const rename = (kind: "providers" | "lists", pid: string, lid: string | null, current: string) => {
    const name = window.prompt("Новое название", current);
    if (!name || name === current) return;
    void mutate(lid ? `/providers/${pid}/lists/${lid}` : `/providers/${pid}`, "PATCH", { name });
  };
  const remove = (kind: "providers" | "lists", pid: string, lid: string | null, name: string) => {
    if (!window.confirm(`Удалить «${name}»${lid ? "" : " со всеми списками"}?`)) return;
    void mutate(lid ? `/providers/${pid}/lists/${lid}` : `/providers/${pid}`, "DELETE")
      .then(ok => { if (ok && lid && sel?.lid === lid) setSel(null); });
  };

  const addRows = async () => {
    const subnets = newSubnet.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    if (!subnets.length || !sel) return;
    setBusy(true);
    try {
      const res = await api(`/providers/${sel.pid}/lists/${sel.lid}/rows`, {
        method: "POST", body: JSON.stringify({ subnets }),
      });
      const d = await res.json();
      if (!res.ok) { toast(typeof d.detail === "string" ? d.detail : "Ошибка", "error"); return; }
      if (d.errors?.length) toast(`Пропущено: ${d.errors.join("; ")}`, "error");
      setNewSubnet("");
      load();
      // автообогащение ASN свежими строками
      const after = await api("").then(r => r.json());
      const rows: Row[] = after.providers
        ?.find((p: Prov) => p.id === sel.pid)?.lists
        ?.find((l: Lst) => l.id === sel.lid)?.rows ?? [];
      const missing = rows.filter(r => d.added?.includes(r.values?.subnet) && !r.values?.asn);
      if (missing.length) {
        await api(`/providers/${sel.pid}/lists/${sel.lid}/enrich`, {
          method: "POST", body: JSON.stringify({ row_ids: missing.map(r => r.id) }),
        });
        load();
      }
    } finally { setBusy(false); }
  };

  // ── Импорт/экспорт ────────────────────────────────────────────
  // Скачиваем через fetch→blob, а не простой <a href>: запросы к /api
  // подписываются bearer-токеном в интерцепторе (auth/apiClient.ts),
  // навигация браузера этот заголовок не несёт и вернула бы 401.
  const doExport = async () => {
    if (!sel) return;
    const qs = new URLSearchParams({ provider_id: sel.pid, list_id: sel.lid, format: expFormat });
    try {
      const res = await api(`/export?${qs.toString()}`);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
        return;
      }
      const cd = res.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename="?([^";]+)"?/);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = m ? m[1] : `subnets-${current?.name ?? sel.lid}.${expFormat}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  // list_id не передаём, когда просят новый список — backend создаст его сам.
  const doImport = async () => {
    if (!sel || !impFile) return;
    setImpBusy(true);
    setImpResult(null);
    try {
      const fd = new FormData();
      fd.append("file", impFile);
      fd.append("provider_id", sel.pid);
      if (!impNewList) fd.append("list_id", sel.lid);
      fd.append("mode", impMode);
      const res = await fetch("/api/subnets/import", { method: "POST", body: fd });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d.ok === false) {
        toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
        return;
      }
      setImpResult({
        imported: Number(d.imported ?? 0),
        skipped: Number(d.skipped ?? 0),
        errors: Array.isArray(d.errors) ? d.errors.map(String) : [],
      });
      if (d.errors?.length) toast(`Пропущено: ${d.errors.join("; ")}`, "error");
      else toast(`Импортировано ${d.imported ?? 0}`, "success");
      setImpFile(null);
      if (filePick.current) filePick.current.value = "";
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally { setImpBusy(false); }
  };

  // Обогащение неразмеченных: одним действием заполнить provider/country/asn
  // у всех строк списка без провайдера (backend пачками по 40 с паузой).
  const doEnrichMissing = async () => {
    if (!sel) return;
    setEnrichBusy(true);
    setEnrichResult(null);
    try {
      const res = await api(`/providers/${sel.pid}/lists/${sel.lid}/enrich-missing`, {
        method: "POST", body: JSON.stringify({}),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
        return;
      }
      const upd = Number(d.updated ?? 0);
      const of = Number(d.of ?? 0);
      const skipped = Number(d.skipped ?? 0);
      setEnrichResult({ updated: upd, of, skipped });
      toast(`Обновлено: ${upd}${skipped ? ` (пропущено ${skipped})` : ""}`, "success");
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally { setEnrichBusy(false); }
  };

  // Тип ASN (isp/hosting/business) по ТЕКУЩИМ данным строк — без ip-api:
  // backend создаёт колонку asn_type (если её нет) и заполняет её.
  const doEnrichTypes = async () => {
    if (!sel) return;
    setTypesBusy(true);
    setTypesResult(null);
    try {
      const res = await api(`/providers/${sel.pid}/lists/${sel.lid}/enrich-types`, {
        method: "POST", body: JSON.stringify({}),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
        return;
      }
      const upd = Number(d.updated ?? 0);
      const of = Number(d.of ?? 0);
      setTypesResult({ updated: upd, of });
      toast(`Типы ASN: ${upd} из ${of}`, "success");
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally { setTypesBusy(false); }
  };

  // ── иконки записей ASN ───────────────────────────────────────
  // Один скрытый input на всю панель: кнопка запоминает цель (номер ASN),
  // выбор файла шлёт multipart POST /asns/{asn}/icon, после — loadAsns()
  // обновит справочник (иконки сами подтянутся к строкам с этим ASN).
  const pickAsnIcon = (asn: string) => {
    iconAsnTarget.current = asn;
    iconInput.current?.click();
  };

  const uploadAsnIcon = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    const asn = iconAsnTarget.current;
    e.target.value = "";
    if (!file || !asn) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`/api/subnets/asns/${encodeURIComponent(asn)}/icon`,
        { method: "POST", body: fd });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
        return;
      }
      toast("Иконка загружена", "success");
      loadAsns();
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };

  // ── Latency-скан ──────────────────────────────────────────────
  // Панель открывается по кнопке; список операторов тянем лениво, чтобы не
  // дёргать внешний сервис на каждом заходе в раздел.
  const openScan = async () => {
    setScanOpen(true);
    setScanResult(null);
    setScanStatus(null);
    setScanParts([]);
    setScanErrors([]);
    try {
      const d = await fetch("/api/latency/operators").then(r => r.json());
      setLatOps(Array.isArray(d?.operators) ? d.operators : []);
    } catch { setLatOps([]); }
  };

  const closeScan = () => {
    setScanOpen(false);
    setScanParts([]);
    setScanErrors([]);
    setScanStatus(null);
    setScanResult(null);
    setPicked([]);
  };

  const togglePick = (id: string) =>
    setPicked(p => (p.includes(id) ? p.filter(x => x !== id) : [...p, id]));
  const toggleAll = () =>
    setPicked(p => (current && p.length === current.rows.length ? [] : (current?.rows ?? []).map(r => r.id)));
  // Чекбокс в заголовке группы: выбрать/снять ВСЕ строки группы.
  const toggleGroupPick = (gid: string) => {
    const g = groups.find(x => x.id === gid);
    if (!g || g.rows.length === 0) return;
    const ids = g.rows.map(r => r.id);
    setPicked(p => {
      if (ids.every(id => p.includes(id))) return p.filter(x => !ids.includes(x));
      const next = new Set(p);
      for (const id of ids) next.add(id);
      return [...next];
    });
  };

  // Поллинг порций: единственный источник статуса — GET по каждому req_id.
  // Порция готова, когда готовы ВСЕ её req_id; общий статус — по всем порциям
  // (done — только когда все готовы, error — при любой ошибке). Тики идут
  // цепочкой (следующий — через 1.5с после предыдущего), без наложения.
  useEffect(() => {
    if (scanParts.length === 0 || scanStatus !== "pending") return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (pollBusy.current) return;
      pollBusy.current = true;
      try {
        const snaps = await Promise.all(scanParts.map(async part => {
          const results = await Promise.all(part.reqIds.map(async id => {
            try {
              const res = await api(`/latency-scan/${id}`);
              const d = await res.json().catch(() => ({}));
              // Статусы-синонимы Latency Lab нормализуем до done/error —
              // иначе скан с одной подсетью крутился бы вечно.
              return { status: normalizeScanStatus(d?.status), result: d?.result ?? null, error: d?.error };
            } catch { return { status: "pending" as ScanStatus, result: null }; }
          }));
          // порция без req_id (упала при запуске) — статус уже error/cancelled
          const st: ScanStatus = part.reqIds.length === 0 ? part.status
            : results.some(r => r.status === "error") ? "error"
            : results.some(r => r.status === "cancelled") ? "cancelled"
            : results.every(r => r.status === "done") ? "done" : "pending";
          return { part, st, results };
        }));
        if (stop) return;
        // объединяем результат всех порций: по row_id (запас — по subnet), без дублей
        const rows: ScanItem[] = [];
        const seen = new Set<string>();
        for (const s of snaps) {
          for (const r of s.results) {
            const list = Array.isArray(r.result?.rows) ? r.result.rows
              : r.result ? [r.result] : [];
            for (const it of list) {
              const key = it?.row_id ?? it?.subnet ?? "";
              if (key && seen.has(key)) continue;
              if (key) seen.add(key);
              rows.push(it);
            }
          }
        }
        setScanParts(snaps.map(s => ({ reqIds: s.part.reqIds, rowIds: s.part.rowIds, status: s.st })));
        setScanResult({ rows });
        const anyErr = snaps.some(s => s.st === "error");
        const anyCancelled = snaps.some(s => s.st === "cancelled");
        const allDone = snaps.every(s => s.st === "done");
        if (anyErr) {
          setScanStatus("error");
          setScanBusy(false);
          toast("Скан завершился с ошибкой", "error");
        } else if (anyCancelled) {
          setScanStatus("cancelled");
          setScanBusy(false);
        } else if (allDone) {
          setScanStatus("done");
          setScanBusy(false);
          toast("Скан завершён", "success");
        } else {
          setScanStatus("pending");
        }
      } finally {
        pollBusy.current = false;
        if (!stop) timer = setTimeout(() => void tick(), 1500);
      }
    };
    timer = setTimeout(() => void tick(), 1500);
    return () => { stop = true; clearTimeout(timer); };
  }, [scanParts, scanStatus]);

  const startScan = async () => {
    if (!sel || !current) return;
    cancelRef.current = false;
    setScanBusy(true);
    setScanStatus("pending");
    setScanParts([]);
    setScanResult(null);
    setScanErrors([]);
    const chunks = scanChunks(current.rows, picked);
    const parts: ScanPart[] = [];
    try {
      // Порции шлём ПОСЛЕДОВАТЕЛЬНО: параллельные пачки сожгли бы лимит разом.
      for (const chunk of chunks) {
        if (cancelRef.current) break; // отмена во время отправки порций
        // Состав порции фиксируем ЗДЕСЬ: статусы строк не зависят от того,
        // что пользователь выберет (или отменит) после старта.
        const rowIds = chunk.length === 0 ? current.rows.map(r => r.id) : chunk;
        const res = await api("/latency-scan", {
          method: "POST",
          body: JSON.stringify({
            provider_id: sel.pid,
            list_id: sel.lid,
            ...(chunk.length === 0 ? { all: true } : { row_ids: chunk }),
            ...(scanOp ? { operator: scanOp } : {}),
            async_: true,
          }),
        });
        const d = await res.json().catch(() => ({}));
        if (d.errors?.length) setScanErrors(prev => [...prev, ...d.errors]);
        if (!res.ok || d.ok === false) {
          const msg = typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`;
          toast(msg, "error");
          if (parts.length === 0) { setScanStatus(null); setScanBusy(false); return; }
          parts.push({ reqIds: [], rowIds, status: "error" });
          continue;
        }
        const got: string[] = [];
        if (Array.isArray(d.jobs)) for (const j of d.jobs) if (j?.req_id) got.push(j.req_id);
        if (!got.length && d.req_id) got.push(d.req_id);
        parts.push(got.length ? { reqIds: got, rowIds, status: "pending" }
                             : { reqIds: [], rowIds, status: "error" });
      }
      if (cancelRef.current) {
        if (parts.length) setScanParts(parts); // чтобы «Отменить» мог дослать cancel
        setScanBusy(false);
        return;
      }
      if (parts.length === 0) { setScanBusy(false); return; }
      setScanParts(parts);
      setScanStatus("pending");
    } catch (e) {
      toast((e as Error).message, "error");
      setScanStatus(null);
      setScanBusy(false);
    }
  };

  const cancelScan = async () => {
    cancelRef.current = true;
    await Promise.all(scanParts.flatMap(p => p.reqIds).map(id =>
      api("/latency-scan/cancel", { method: "POST", body: JSON.stringify({ req_id: id }) })
        .catch(() => {})
    ));
    setScanStatus("cancelled");
    setScanBusy(false);
  };

  return (
    <Page max={1100}>
      <PageHeader icon={<Table2 size={16} />} title="Подсети"
        subtitle="Справочник подсетей/IP по провайдерам — разметка для обходов БС" />

      <div style={{ display: "grid", gap: 14, gridTemplateColumns: treeOpen ? "240px 1fr" : "34px 1fr", alignItems: "stretch" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
        {/* ── дерево ── */}
        <div className={treeOpen ? "card card-p" : "card"}
          style={{
            display: "flex", flexDirection: "column", gap: 6,
            maxHeight: treeOpen ? "max(240px, calc(100vh - 460px))" : undefined,
            overflowY: treeOpen ? "auto" : "hidden",
            ...(treeOpen ? {} : { alignItems: "center", justifyContent: "center" }),
          }}>
          {treeOpen ? (
            <>
          <div className="flex items-center gap-2">
            <FolderKanban size={13} style={{ color: "var(--t-low)" }} />
            <span className="micro">Провайдеры</span>
            <button className="iconbtn" style={{ marginLeft: "auto", width: 22, height: 22 }}
              title="Добавить провайдера" onClick={addProvider}><Plus size={13} /></button>
            <button className="iconbtn" style={{ width: 22, height: 22 }}
              title="Свернуть дерево" data-testid="tree-toggle"
              onClick={() => setTreeOpen(false)}><ChevronLeft size={13} /></button>
          </div>
          {providers.length === 0 && (
            <p className="hint">Создайте провайдера, а в нём — список подсетей.</p>
          )}
          {providers.map(p => (
            <div key={p.id}>
              <div className="flex items-center gap-1 group" style={{ padding: "2px 4px" }}>
                <span className="text-xs font-semibold trunc" style={{ color: "var(--t-hi)", flex: 1 }}>{p.name}</span>
                <button className="iconbtn" style={{ width: 20, height: 20 }} title="Новый список"
                  onClick={() => addList(p.id)}><Plus size={11} /></button>
                <button className="iconbtn" style={{ width: 20, height: 20 }} title="Переименовать"
                  onClick={() => rename("providers", p.id, null, p.name)}><Pencil size={11} /></button>
                <button className="iconbtn danger" style={{ width: 20, height: 20 }} title="Удалить"
                  onClick={() => remove("providers", p.id, null, p.name)}><Trash2 size={11} /></button>
              </div>
              {p.lists.map(l => (
                <div key={l.id}
                  className="flex items-center gap-1 pl-4 group rounded-md"
                  style={{
                    padding: "4px 4px 4px 16px", cursor: "pointer",
                    background: sel?.lid === l.id ? "var(--accent-dim)" : "transparent",
                  }}
                  onClick={() => { setSel({ pid: p.id, lid: l.id }); setEditMode(false); setAsnView(false); }}>
                  <span className="text-xs trunc" style={{ color: "var(--t-mid)", flex: 1 }}>{l.name}</span>
                  <button className="iconbtn" style={{ width: 20, height: 20 }}
                    onClick={e => { e.stopPropagation(); rename("lists", p.id, l.id, l.name); }}><Pencil size={10} /></button>
                  <button className="iconbtn danger" style={{ width: 20, height: 20 }}
                    onClick={e => { e.stopPropagation(); remove("lists", p.id, l.id, l.name); }}><Trash2 size={10} /></button>
                </div>
              ))}
            </div>
          ))}
            </>
          ) : (
            <button className="iconbtn" style={{ width: 22, height: 22 }}
              title="Развернуть дерево" data-testid="tree-toggle"
              onClick={() => setTreeOpen(true)}><ChevronRight size={13} /></button>
          )}
        </div>

        {/* ── справочник ASN: большая кнопка под деревом → таблица справа ── */}
        {/* Кнопка всегда видна (и при свёрнутом дереве — узкая, иконкой);
            активная подсвечена (asnView). Сама таблица справочника — справа,
            как таблица подсетей (asn-view). */}
        <button className={asnView ? "btn btn-primary" : "btn btn-soft"}
          data-testid="asn-dir-btn" title="Справочник ASN"
          onClick={() => setAsnView(true)}
          style={treeOpen
            ? { padding: "8px 10px", fontSize: 12 }
            : { width: 30, height: 30, padding: 0, justifyContent: "center" }}>
          <BookOpen size={13} />
          {treeOpen && (
            <>
              <span style={{ flex: 1, textAlign: "left" }}>Справочник</span>
              <span className="text-[10px]" style={{ color: "var(--t-faint)" }}>{asns.length}</span>
            </>
          )}
        </button>
        </div>

        {/* ── справа: справочник ASN (asnView) или таблица подсетей ── */}
        {asnView ? (
          <div className="card" data-testid="asn-view" style={{ overflow: "hidden", display: "flex", flexDirection: "column",
            maxHeight: "max(280px, calc(100vh - 200px))", minHeight: 0 }}>
            <div className="flex items-center gap-2 px-3 py-2.5 flex-wrap" style={{ borderBottom: "1px solid var(--line-soft)", flex: "none" }}>
              <button className="btn btn-soft btn-sm" style={{ fontSize: 11 }}
                data-testid="asn-back"
                onClick={() => setAsnView(false)}>
                <ChevronLeft size={11} /> Назад
              </button>
              <BookOpen size={13} style={{ color: "var(--t-low)" }} />
              <span className="micro">Справочник ASN</span>
              <span className="text-[10px]" style={{ color: "var(--t-faint)" }}>{asns.length}</span>
              <button className="btn btn-soft btn-sm" style={{ fontSize: 11 }}
                data-testid="asn-sync"
                title="Собрать ASN и названия из списков подсетей в справочник"
                onClick={() => void syncAsns()} disabled={syncBusy}>
                {syncBusy ? <Loader2 size={11} className="spin" /> : <RefreshCw size={11} />}
                Синхронизировать
              </button>
              <button className="btn btn-soft btn-sm" style={{ fontSize: 11 }}
                data-testid="asn-apply"
                title="Перенести названия и netname из справочника в строки подсетей"
                onClick={() => void applyAsns()} disabled={applyBusy}>
                {applyBusy ? <Loader2 size={11} className="spin" /> : <ArrowDownToLine size={11} />}
                Применить из справочника
              </button>
              <button className="btn btn-primary btn-sm" style={{ marginLeft: "auto", fontSize: 11 }}
                data-testid="asn-add"
                onClick={addAsn} disabled={!asnNewAsn.trim()}>
                <Plus size={11} /> Добавить ASN
              </button>
            </div>

            <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)", flex: "none" }}>
              <input className="input font-mono text-xs" style={{ width: 110, flex: "none" }}
                data-testid="asn-new-asn" aria-label="Номер ASN" placeholder="AS12345"
                value={asnNewAsn} onChange={e => setAsnNewAsn(e.target.value)} />
              <input className="input text-xs" style={{ flex: 1, minWidth: 0 }}
                data-testid="asn-new-name" aria-label="Название ASN"
                placeholder="Название (например Яндекс)"
                value={asnNewName} onChange={e => setAsnNewName(e.target.value)} />
              <input className="input font-mono text-xs" style={{ width: 140, flex: "none" }}
                data-testid="asn-new-netname" aria-label="Netname ASN"
                placeholder="Netname (например RU-YANDEX)"
                value={asnNewNetname} onChange={e => setAsnNewNetname(e.target.value)} />
              <button className="btn btn-primary btn-sm" style={{ fontSize: 11, flex: "none" }}
                onClick={addAsn} disabled={!asnNewAsn.trim()}>
                <Plus size={11} /> Добавить
              </button>
            </div>

            <div className="flex-1 min-h-0 overflow-auto">
              <table className="tbl colborders text-xs" style={{ borderCollapse: "separate", borderSpacing: 0 }}>
                <thead>
                  <tr>
                    <th className="sticky top-0 z-10" style={{ width: 40, background: "var(--bg2)" }}>Иконка</th>
                    <th className="sticky top-0 z-10" style={{ background: "var(--bg2)" }}>ASN</th>
                    <th className="sticky top-0 z-10" style={{ background: "var(--bg2)" }}>Название</th>
                    <th className="sticky top-0 z-10" style={{ background: "var(--bg2)" }}>Netname</th>
                    <th className="sticky top-0 z-10" style={{ background: "var(--bg2)" }}>Примечание</th>
                    <th className="sticky top-0 z-10" style={{ width: 88, background: "var(--bg2)" }}>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {asns.length === 0 && (
                    <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--t-faint)", padding: 16 }}>
                      Пусто — добавьте ASN (например 12345 → Яндекс).
                    </td></tr>
                  )}
                  {asns.map(a => (
                    <tr key={a.asn} data-testid={`asn-row-${a.asn}`}>
                      <td>
                        {a.icon ? (
                          <img src={`/api/subnets/asns/${encodeURIComponent(a.asn)}/icon`} alt=""
                            width={14} height={14}
                            style={{ borderRadius: 3, objectFit: "contain", flex: "none" }} />
                        ) : (
                          <span style={{ width: 14, flex: "none" }} />
                        )}
                      </td>
                      <td><span className="font-mono" style={{ color: "var(--t-hi)" }}>{a.asn}</span></td>
                      <td><span className="trunc" title={a.name || ""}
                        style={{ color: "var(--t-mid)" }}>{a.name || "—"}</span></td>
                      <td><span className="trunc" title={a.netname || ""}
                        style={{ color: "var(--t-mid)" }}>{a.netname || "—"}</span></td>
                      <td><span className="trunc" title={a.note || ""}
                        style={{ color: "var(--t-faint)" }}>{a.note || "—"}</span></td>
                      <td>
                        <span className="flex items-center gap-1">
                          <button className="iconbtn" style={{ width: 20, height: 20 }} title="Иконка"
                            data-testid={`asn-icon-upload-${a.asn}`}
                            onClick={() => pickAsnIcon(a.asn)}><ImageUp size={10} /></button>
                          <button className="iconbtn" style={{ width: 20, height: 20 }} title="Изменить"
                            onClick={() => editAsn(a)}><Pencil size={10} /></button>
                          <button className="iconbtn danger" style={{ width: 20, height: 20 }} title="Удалить"
                            onClick={() => removeAsn(a)}><Trash2 size={10} /></button>
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : !current ? (
          <div className="card card-p" style={{ textAlign: "center", color: "var(--t-faint)", fontSize: 13 }}>
            Выберите список слева или создайте нового провайдера.
          </div>
        ) : (
          <div className="card" style={{ overflow: "hidden", display: "flex", flexDirection: "column",
            maxHeight: "max(280px, calc(100vh - 200px))", minHeight: 0 }}>
            <div className="flex items-center gap-2 px-3 py-2.5 flex-wrap" style={{ borderBottom: "1px solid var(--line-soft)", flex: "none" }}>
              <Table2 size={13} style={{ color: "var(--t-low)" }} />
              <span className="micro">{current.name}</span>
              <span className="text-[10px]" style={{ color: "var(--t-faint)" }}>{current.rows.length} строк</span>
              <button className={`btn btn-sm ${ioOpen ? "btn-primary" : "btn-soft"}`}
                style={{ marginLeft: "auto", fontSize: 11 }}
                data-testid="subnets-io-toggle"
                onClick={() => { setIoOpen(o => !o); setImpResult(null); }}>
                <Download size={11} /> Импорт/экспорт
              </button>
              {latEnabled && (
                <button className={`btn btn-sm ${scanOpen ? "btn-primary" : "btn-soft"}`}
                  style={{ fontSize: 11 }}
                  data-testid="latency-scan-toggle"
                  onClick={() => (scanOpen ? closeScan() : void openScan())}>
                  <Activity size={11} /> Скан Latency
                </button>
              )}
              <button className={`btn btn-sm ${groupByProvider ? "btn-primary" : "btn-soft"}`}
                style={{ fontSize: 11 }}
                data-testid="group-toggle"
                disabled={editMode}
                title={editMode ? "Недоступно в режиме правки" : "Группировать строки по провайдеру"}
                onClick={() => setGroupByProvider(g => !g)}>
                {groupByProvider ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                {groupByProvider ? "Группировать: Провайдер" : "Группировать: Выкл"}
              </button>
              <select className="selectbox text-xs" style={{ width: 116 }}
                data-testid="color-mode-select" aria-label="Цвет строк"
                value={colorMode}
                disabled={editMode}
                title={editMode ? "Недоступно в режиме правки"
                  : "Цвет строк: выкл / только при группировке / по провайдеру везде"}
                onChange={e => setColorMode(e.target.value as ColorMode)}>
                <option value="off">Цвет: Выкл</option>
                <option value="groups">Цвет: Группы</option>
                <option value="all">Цвет: Везде</option>
              </select>
              <button className={`btn btn-sm ${editMode ? "btn-primary" : "btn-soft"}`}
                style={{ fontSize: 11 }}
                data-testid="table-edit-toggle"
                onClick={() => setEditMode(m => !m)}>
                {editMode ? <Check size={11} /> : <Pencil size={11} />}
                {editMode ? "Готово" : "Редактировать таблицу"}
              </button>
            </div>

            {ioOpen && (
              <div className="flex flex-col gap-2 px-3 py-2.5" data-testid="subnets-io-panel"
                style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)", flex: "none" }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="micro" style={{ margin: 0 }}>Экспорт</span>
                  <select className="selectbox text-xs" style={{ width: 120 }}
                    data-testid="export-format" aria-label="Формат экспорта" value={expFormat}
                    onChange={e => setExpFormat(e.target.value as ExportFormat)}>
                    <option value="json">JSON</option>
                    <option value="csv">CSV</option>
                    <option value="txt">TXT</option>
                    <option value="xlsx">Excel</option>
                  </select>
                  <button className="btn btn-soft btn-sm" style={{ fontSize: 11 }}
                    data-testid="export-run" onClick={() => void doExport()}>
                    <Download size={11} /> Скачать
                  </button>
                </div>

                <div className="flex items-center gap-2 flex-wrap">
                  <span className="micro" style={{ margin: 0 }}>Импорт</span>
                  <input ref={filePick} type="file" className="input text-xs" style={{ width: 220 }}
                    data-testid="import-file" aria-label="Файл импорта"
                    accept=".json,.csv,.txt"
                    onChange={e => { setImpFile(e.target.files?.[0] ?? null); setImpResult(null); }} />
                  <select className="selectbox text-xs" style={{ width: 150 }}
                    data-testid="import-mode" aria-label="Режим импорта" value={impMode}
                    onChange={e => setImpMode(e.target.value as ImportMode)}>
                    <option value="merge">Дополнить</option>
                    <option value="replace">Заменить</option>
                  </select>
                  <label className="flex items-center gap-1 text-xs" style={{ color: "var(--t-mid)", cursor: "pointer" }}>
                    <input type="checkbox" data-testid="import-new-list"
                      checked={impNewList} onChange={e => setImpNewList(e.target.checked)} />
                    в новый список
                  </label>
                  <button className="btn btn-primary btn-sm" style={{ fontSize: 11 }}
                    data-testid="import-run" onClick={() => void doImport()}
                    disabled={impBusy || !impFile}>
                    {impBusy ? <Loader2 size={11} className="spin" /> : <Upload size={11} />} Импортировать
                  </button>
                </div>

                <div className="flex items-center gap-2 flex-wrap">
                  <span className="micro" style={{ margin: 0 }}>Разметка</span>
                  <button className="btn btn-soft btn-sm" style={{ fontSize: 11 }}
                    data-testid="enrich-missing-run" onClick={() => void doEnrichMissing()}
                    disabled={enrichBusy}
                    title="Заполнить провайдера/страну/ASN у всех строк списка без провайдера (ip-api, пачками)">
                    {enrichBusy ? <Loader2 size={11} className="spin" /> : <Sparkles size={11} />} Разметить провайдеров
                  </button>
                  <button className="btn btn-soft btn-sm" style={{ fontSize: 11 }}
                    data-testid="enrich-types-run" onClick={() => void doEnrichTypes()}
                    disabled={typesBusy}
                    title="Определить тип ASN (ISP/Hosting/Business) по названию организации/ASN — без ip-api, создаёт колонку «Тип ASN»">
                    {typesBusy ? <Loader2 size={11} className="spin" /> : <Sparkles size={11} />} Типы ASN
                  </button>
                  {typesResult && (
                    <span className="chip accent" style={{ fontSize: 10 }} data-testid="enrich-types-result">
                      Типы: {typesResult.updated} из {typesResult.of}
                    </span>
                  )}
                  {enrichResult && (
                    <span className="chip ok" style={{ fontSize: 10 }} data-testid="enrich-missing-result">
                      Обновлено: {enrichResult.updated} из {enrichResult.of}
                      {enrichResult.skipped > 0 ? ` (пропущено ${enrichResult.skipped})` : ""}
                    </span>
                  )}
                </div>

                {impResult && (
                  <div className="flex items-center gap-2 flex-wrap text-xs" data-testid="import-result"
                    style={{ color: "var(--t-mid)" }}>
                    <span className="chip ok" style={{ fontSize: 10 }}>
                      Импортировано {impResult.imported}, пропущено {impResult.skipped}
                    </span>
                    {!!impResult.errors.length && (
                      <span className="trunc" style={{ color: "var(--t-faint)", maxWidth: 420 }}
                        title={impResult.errors.join("; ")}>
                        {impResult.errors.join("; ")}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}

            {editMode && (
              <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)", flex: "none" }}>
                <span className="hint" style={{ margin: 0 }}>Столбцы: перетаскивайте заголовки.</span>
                <button className="btn btn-soft" style={{ padding: "3px 10px", fontSize: 11 }}
                  onClick={() => {
                    const title = window.prompt("Название столбца", "Комментарий");
                    if (title) void mutate(`/providers/${sel!.pid}/lists/${sel!.lid}/columns`, "POST", { title });
                  }}>
                  <Plus size={11} /> Столбец
                </button>
              </div>
            )}

            {scanOpen && (
              <div className="flex flex-col gap-2 px-3 py-2.5" data-testid="latency-scan-panel"
                style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)", flex: "none" }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="micro" style={{ margin: 0 }}>Скан Latency</span>
                  <span className="text-[10px]" style={{ color: "var(--t-faint)" }}>
                    {picked.length ? `выбрано ${picked.length}` : "все строки"}
                  </span>
                  <select className="selectbox text-xs" style={{ width: 180 }}
                    data-testid="latency-operator" value={scanOp}
                    onChange={e => setScanOp(e.target.value)}>
                    <option value="">Все операторы (multiscan)</option>
                    {latOps.map(o => (
                      <option key={o.id} value={o.id} disabled={!o.configured}>
                        {o.label}{o.online ? "" : " (offline)"}
                      </option>
                    ))}
                  </select>
                  {scanBusy || scanStatus === "pending" ? (
                    <button className="btn btn-soft btn-sm" style={{ fontSize: 11 }}
                      data-testid="latency-cancel" onClick={cancelScan}>
                      <X size={11} /> Отменить
                    </button>
                  ) : (
                    <button className="btn btn-primary btn-sm" style={{ fontSize: 11 }}
                      data-testid="latency-start" onClick={startScan}
                      disabled={current.rows.length === 0}>
                      <Activity size={11} /> Запустить
                    </button>
                  )}
                  {scanStatus === "pending" && (
                    <span className="chip accent" style={{ fontSize: 10 }} data-testid="latency-progress">
                      <Loader2 size={10} className="spin" />
                      {scanParts.length > 1
                        ? ` Порция ${Math.min(scanParts.filter(p => p.status === "done").length + 1, scanParts.length)}/${scanParts.length}`
                        : " Сканирование…"}
                    </span>
                  )}
                  {scanStatus === "cancelled" && (
                    <span className="chip warn" style={{ fontSize: 10 }}>Отменено</span>
                  )}
                  {scanStatus === "error" && (
                    <span className="chip err" style={{ fontSize: 10 }}>Ошибка</span>
                  )}
                </div>

                {scanStatus === "done" && (
                  <div data-testid="latency-result" className="flex flex-col gap-1">
                    {(scanResult?.rows?.length ? scanResult.rows : scanResult ? [scanResult] : []).map((it, i) => {
                      const disp = scanSubnetDisplay(it,
                        current.rows.find(r => r.id === it.row_id)?.values?.subnet ?? "");
                      return (
                      <div key={it.row_id ?? i} className="flex items-center gap-2 flex-wrap text-xs"
                        style={{ color: "var(--t-mid)" }}>
                        {disp.host ? (
                          // Точечный IP (subnet без маски) — отдельная запись с чипом /32.
                          <span className="flex items-center gap-1.5" style={{ color: "var(--t-hi)" }}>
                            {disp.label || "—"}
                            <span className="chip neutral" style={{ fontSize: 10 }}
                              data-testid={`scan-host-${it.row_id ?? i}`}>/32</span>
                          </span>
                        ) : (
                          <span style={{ color: "var(--t-hi)" }}>{disp.label || "—"}</span>
                        )}
                        {/* Живые IP агрегированы в минимальную подсеть (например /25 из /24). */}
                        {!disp.host && disp.label !== disp.orig && (
                          <span className="chip neutral" style={{ fontSize: 10 }}
                            data-testid={`scan-agg-${it.row_id ?? i}`}>
                            из {disp.orig}
                          </span>
                        )}
                        {it.operator && <span className="chip neutral" style={{ fontSize: 10 }}>{it.operator}</span>}
                        <span className={`chip ${it.available ? "ok" : "err"}`} style={{ fontSize: 10 }}>
                          {it.available ? "доступна" : "недоступна"}
                        </span>
                        <span>живых IP: {it.alive_count ?? 0}</span>
                        {it.status_text && <span style={{ color: "var(--t-low)" }}>{it.status_text}</span>}
                        {!!it.reachable_ips?.length && (
                          <span className="font-mono trunc" style={{ color: "var(--t-faint)", maxWidth: 320 }}
                            title={it.reachable_ips.join(", ")}>
                            {it.reachable_ips.join(", ")}
                          </span>
                        )}
                      </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            <div className="flex-1 min-h-0 overflow-auto" data-testid="subnets-table-scroll">
              <table className="tbl colborders text-xs" style={{ borderCollapse: "separate", borderSpacing: 0 }}>
                <thead>
                  <tr>
                    {scanOpen && (
                      <th className="sticky top-0 z-10" style={{ width: 28, background: "var(--bg2)" }}>
                        <input type="checkbox" data-testid="latency-pick-all"
                          aria-label="Выбрать все строки"
                          checked={current.rows.length > 0 && picked.length === current.rows.length}
                          onChange={toggleAll} />
                      </th>
                    )}
                    {current.columns.map(c => (
                      <th key={c.key} className="sticky top-0 z-10"
                        draggable={editMode}
                        onDragStart={() => { dragCol.current = c.key; }}
                        onDragOver={editMode ? e => e.preventDefault() : undefined}
                        onDrop={editMode ? e => {
                          e.preventDefault();
                          const from = dragCol.current;
                          if (!from || from === c.key) return;
                          const order = current.columns.map(x => x.key);
                          order.splice(order.indexOf(from), 1);
                          order.splice(order.indexOf(c.key), 0, from);
                          void mutate(`/providers/${sel!.pid}/lists/${sel!.lid}/columns-order`, "PUT", { order });
                        } : undefined}
                        style={{ cursor: editMode ? "grab" : undefined, background: "var(--bg2)" }}>
                        <span className="flex items-center gap-1">
                          {editMode && <GripVertical size={10} style={{ color: "var(--t-faint)" }} />}
                          {c.title}
                          {editMode && c.key !== "subnet" && (
                            <>
                              <button className="iconbtn" style={{ width: 16, height: 16 }} title="Переименовать"
                                onClick={() => {
                                  const title = window.prompt("Название столбца", c.title);
                                  if (title) void mutate(`/providers/${sel!.pid}/lists/${sel!.lid}/columns/${c.key}`, "PATCH", { title });
                                }}><Pencil size={9} /></button>
                              <button className="iconbtn danger" style={{ width: 16, height: 16 }} title="Удалить столбец"
                                onClick={() => {
                                  if (window.confirm(`Удалить столбец «${c.title}»?`))
                                    void mutate(`/providers/${sel!.pid}/lists/${sel!.lid}/columns/${c.key}`, "DELETE");
                                }}><X size={9} /></button>
                            </>
                          )}
                        </span>
                      </th>
                    ))}
                    <th className="sticky top-0 z-10" style={{ width: 32, background: "var(--bg2)" }} />
                  </tr>
                </thead>
                <tbody>
                  {current.rows.length === 0 && (
                    <tr><td colSpan={current.columns.length + (scanOpen ? 2 : 1)}
                      style={{ textAlign: "center", color: "var(--t-faint)", padding: 16 }}>
                      Пусто — добавьте подсеть ниже.
                    </td></tr>
                  )}
                  {grouped ? groups.map(g => {
                    const gids = g.rows.map(r => r.id);
                    const allPicked = gids.length > 0 && gids.every(id => picked.includes(id));
                    const somePicked = gids.some(id => picked.includes(id));
                    return (
                      <Fragment key={g.id}>
                        {/* Акцентный заголовок группы: прозрачный фон + полный
                            цвет текста/иконок + полоса слева (borderLeft). */}
                        <tr data-testid={`subnets-group-${g.id}`} onClick={() => toggleGroup(g.id)}
                          style={{
                            background: colored ? hexToRgba(g.color, 0.14) : undefined,
                            color: colored ? g.color : undefined,
                            cursor: "pointer",
                          }}>
                          <td colSpan={current.columns.length + (scanOpen ? 2 : 1)}
                            style={colored ? { borderLeft: `3px solid ${g.color}` } : undefined}>
                            <span className="flex items-center gap-1.5 font-semibold"
                              style={{ fontSize: 11 }}>
                              {scanOpen && (
                                // Чекбокс «выбрать все строки группы»: клик не
                                // сворачивает группу (stopPropagation).
                                <input type="checkbox"
                                  data-testid={`latency-pick-group-${g.id}`}
                                  aria-label={`Выбрать все строки группы ${g.label}`}
                                  checked={allPicked}
                                  ref={el => { if (el) el.indeterminate = somePicked && !allPicked; }}
                                  onClick={e => e.stopPropagation()}
                                  onChange={() => toggleGroupPick(g.id)} />
                              )}
                              {collapsedGroups.has(g.id) ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                              <span>{g.label} ({g.rows.length})</span>
                            </span>
                          </td>
                        </tr>
                        {!collapsedGroups.has(g.id) && g.rows.map(r => renderRow(r, colored ? g.color : undefined))}
                      </Fragment>
                    );
                  }) : current.rows.map(r => renderRow(
                    r,
                    colored && colorMode === "all"
                      ? rowColor.get((r.values?.provider ?? "").trim())
                      : undefined,
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex gap-2 px-3 py-2.5" style={{ borderTop: "1px solid var(--line-soft)", flex: "none" }}>
              <textarea className="input font-mono text-xs" rows={1} value={newSubnet}
                onChange={e => setNewSubnet(e.target.value)}
                placeholder="203.0.113.0/24 — можно несколько, через запятую или с новой строки"
                style={{ resize: "vertical", minHeight: 34 }} />
              <button className="btn btn-primary" style={{ flex: "none" }} onClick={addRows}
                data-testid="subnets-add-rows"
                disabled={busy || !newSubnet.trim()}>
                {busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Добавить
              </button>
            </div>
          </div>
        )}
      </div>
      <input ref={iconInput} type="file" hidden accept=".png,.svg,.webp"
        data-testid="subnets-icon-file" onChange={uploadAsnIcon} />
    </Page>
  );
}

/** Иконка оператора из /operators/<key>.png — файлы заменяемы без правок кода. */
function OpIcon({ op, dim }: { op: string; dim?: boolean }) {
  return (
    <img src={`/operators/${op}.png`} alt={op} width={16} height={16}
      style={{ borderRadius: 4, opacity: dim ? 0.25 : 1, flex: "none", objectFit: "contain" }} />
  );
}
