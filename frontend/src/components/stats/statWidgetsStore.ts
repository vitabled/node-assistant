// Per-account stats-dashboard store. Holds which widgets are shown, their order
// and width (Wave-5 Plan G), plus which servers the user hid (Wave 6, Plan B).
// Persists to the backend (source of truth) with a localStorage mirror for
// instant/offline defaults. Per-widget settings (window/checker) stay local to
// each widget for now.
import { create } from "zustand";
import { getActiveId } from "../../auth/store";

export type WidgetKind =
  | "node-load" | "avg-per-node" | "top-users" | "migrations" | "stable-nodes" | "fast-nodes";

export const WIDGET_KINDS: WidgetKind[] = [
  "node-load", "avg-per-node", "top-users", "migrations", "stable-nodes", "fast-nodes",
];
const DEFAULT_W: Record<WidgetKind, 1 | 2> = {
  "node-load": 2, "avg-per-node": 1, "top-users": 1, "migrations": 1, "stable-nodes": 1, "fast-nodes": 1,
};

export interface WidgetInstance {
  instanceId: string;
  kind: WidgetKind;
  w: 1 | 2;
  order: number;
  settings: Record<string, unknown>;
}

/** Скрытые серверы. Две оси, потому что идентичность сервера в виджетах не
 *  едина: node-load/avg-per-node/migrations ключуются на Remnawave `node_uuid`,
 *  а stable-nodes/fast-nodes — на `stableId`, чей namespace принадлежит
 *  конкретному чекеру. Значение — последнее известное имя, чтобы пикер мог
 *  показать пропавшую запись человеку, а не голый uuid. */
export interface Hidden {
  nodes: Record<string, string>;
  checker: Record<string, Record<string, string>>;
}

export const EMPTY_HIDDEN: Hidden = { nodes: {}, checker: {} };

const MAX_HIDDEN = 200, MAX_CHECKERS = 20, MAX_NAME = 64;

/** Те же лимиты, что и на бэкенде: localStorage мог быть испорчен вручную. */
export function normalizeHidden(raw: any): Hidden {
  const str = (v: any) => (typeof v === "string" ? v.slice(0, MAX_NAME) : "");
  // Строка тоже итерируется через Object.entries ("ab" → {0:"a",1:"b"}), поэтому
  // проверяем именно объектность, а не truthiness.
  const isObj = (o: any) => !!o && typeof o === "object" && !Array.isArray(o);
  const map = (o: any): Record<string, string> => {
    const out: Record<string, string> = {};
    if (!isObj(o)) return out;
    for (const [k, v] of Object.entries(o)) {
      if (typeof k !== "string" || !k) continue;
      if (Object.keys(out).length >= MAX_HIDDEN) break;
      out[k] = str(v);
    }
    return out;
  };
  const checker: Record<string, Record<string, string>> = {};
  for (const [cid, inner] of Object.entries(isObj(raw?.checker) ? raw.checker : {})) {
    if (Object.keys(checker).length >= MAX_CHECKERS) break;
    checker[cid] = map(inner);
  }
  return { nodes: map(raw?.nodes), checker };
}

// ── Чистые селекторы (тестируются без рендера) ──
export const isNodeHidden = (h: Hidden, uuid: string) => uuid in h.nodes;
export const isCheckerNodeHidden = (h: Hidden, cid: string, sid: string) =>
  !!h.checker[cid] && sid in h.checker[cid];

export const filterNodeLoad = <T extends { node_uuid?: string }>(h: Hidden, xs: T[]): T[] =>
  (xs || []).filter(x => !x.node_uuid || !isNodeHidden(h, x.node_uuid));

/** Миграция режется по ЛЮБОМУ концу: строка «из A в B» бессмысленна, если
 *  скрыт хотя бы один из них. */
export const filterMigrations = <T extends { from_node?: string; to_node?: string }>(h: Hidden, xs: T[]): T[] =>
  (xs || []).filter(x => !isNodeHidden(h, x.from_node || "") && !isNodeHidden(h, x.to_node || ""));

/** `server-monitor` фильтруется на бэкенде (Ф4) — здесь passthrough, иначе
 *  подавление считалось бы дважды и в двух разных пространствах имён. */
export const filterCheckerNodes = <T extends { stableId?: string }>(h: Hidden, cid: string, xs: T[]): T[] =>
  cid === "server-monitor" ? (xs || []) : (xs || []).filter(x => !x.stableId || !isCheckerNodeHidden(h, cid, x.stableId));

export const hiddenCount = (h: Hidden) =>
  Object.keys(h.nodes).length + Object.values(h.checker).reduce((a, m) => a + Object.keys(m).length, 0);

const widgetsKey = () => `stat_widgets_${getActiveId() ?? "none"}`;
let _uid = 0;
const newId = () => `w${Date.now().toString(36)}${(_uid++).toString(36)}`;

function defaultLayout(): WidgetInstance[] {
  return WIDGET_KINDS.map((k, i) => ({ instanceId: newId(), kind: k, w: DEFAULT_W[k], order: i, settings: {} }));
}

// Drop unknown kinds (after a version rollback), coerce, sort by order.
function normalize(raw: any[]): WidgetInstance[] {
  return (raw || [])
    .filter((w: any) => WIDGET_KINDS.includes(w?.kind))
    .map((w: any, i: number) => ({
      instanceId: w.instance_id || w.instanceId || newId(),
      kind: w.kind as WidgetKind,
      w: (w.w === 2 ? 2 : 1) as 1 | 2,
      order: typeof w.order === "number" ? w.order : i,
      settings: w.settings || {},
    }))
    .sort((a, b) => a.order - b.order);
}

let _timer: ReturnType<typeof setTimeout> | null = null;
/** PUT — FULL-REPLACE: тело без `hidden` обнулило бы набор на сервере, поэтому
 *  документ всегда уходит целиком. */
function persist(layout: WidgetInstance[], hidden: Hidden) {
  try { localStorage.setItem(widgetsKey(), JSON.stringify({ layout, hidden })); } catch { /* quota/private */ }
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(() => {
    fetch("/api/stats/users/widgets", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        layout: layout.map(w => ({ instance_id: w.instanceId, kind: w.kind, w: w.w, order: w.order, settings: w.settings })),
        hidden,
      }),
    }).catch(() => { /* localStorage already saved */ });
  }, 600);
}

interface State {
  layout: WidgetInstance[];
  hidden: Hidden;
  editing: boolean;
  hydrated: boolean;
  hydrate: () => Promise<void>;
  setEditing: (v: boolean) => void;
  add: (k: WidgetKind) => void;
  remove: (id: string) => void;
  resize: (id: string, w: 1 | 2) => void;
  move: (id: string, dir: -1 | 1) => void;
  hideNode: (uuid: string, name: string) => void;
  showNode: (uuid: string) => void;
  hideCheckerNode: (cid: string, sid: string, name: string) => void;
  showCheckerNode: (cid: string, sid: string) => void;
}

export const useStatWidgets = create<State>((set, get) => ({
  layout: [],
  hidden: EMPTY_HIDDEN,
  editing: false,
  hydrated: false,
  hydrate: async () => {
    let layout: WidgetInstance[] = [];
    let hidden: Hidden = EMPTY_HIDDEN;
    try {
      const r = await fetch("/api/stats/users/widgets");
      if (r.ok) {
        const doc = await r.json();
        layout = normalize(doc.layout);
        hidden = normalizeHidden(doc.hidden);
      }
    } catch { /* fall through */ }
    if (!layout.length) {
      try {
        const ls = localStorage.getItem(widgetsKey());
        if (ls) {
          // Старый формат — ГОЛЫЙ массив layout; новый — {layout, hidden}.
          const parsed = JSON.parse(ls);
          const doc = Array.isArray(parsed) ? { layout: parsed, hidden: EMPTY_HIDDEN } : parsed;
          layout = normalize(doc.layout);
          if (!hiddenCount(hidden)) hidden = normalizeHidden(doc.hidden);
        }
      } catch { /* ignore */ }
    }
    if (!layout.length) layout = defaultLayout();
    set({ layout, hidden, hydrated: true });
  },
  setEditing: v => set({ editing: v }),
  add: k => {
    const l = [...get().layout];
    l.push({ instanceId: newId(), kind: k, w: DEFAULT_W[k], order: l.length, settings: {} });
    persist(l, get().hidden); set({ layout: l });
  },
  remove: id => {
    const l = get().layout.filter(w => w.instanceId !== id).map((w, i) => ({ ...w, order: i }));
    persist(l, get().hidden); set({ layout: l });
  },
  resize: (id, w) => {
    const l = get().layout.map(x => (x.instanceId === id ? { ...x, w } : x));
    persist(l, get().hidden); set({ layout: l });
  },
  move: (id, dir) => {
    const l = [...get().layout].sort((a, b) => a.order - b.order);
    const i = l.findIndex(w => w.instanceId === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= l.length) return;
    [l[i], l[j]] = [l[j], l[i]];
    l.forEach((w, k) => (w.order = k));
    persist(l, get().hidden); set({ layout: l });
  },

  // «Показать» и «забыть пропавший» — одно действие: удаление ключа.
  hideNode: (uuid, name) => {
    const h: Hidden = { ...get().hidden, nodes: { ...get().hidden.nodes, [uuid]: (name || "").slice(0, MAX_NAME) } };
    persist(get().layout, h); set({ hidden: h });
  },
  showNode: uuid => {
    const nodes = { ...get().hidden.nodes };
    delete nodes[uuid];
    const h: Hidden = { ...get().hidden, nodes };
    persist(get().layout, h); set({ hidden: h });
  },
  hideCheckerNode: (cid, sid, name) => {
    const cur = get().hidden.checker;
    const h: Hidden = { ...get().hidden, checker: { ...cur, [cid]: { ...(cur[cid] || {}), [sid]: (name || "").slice(0, MAX_NAME) } } };
    persist(get().layout, h); set({ hidden: h });
  },
  showCheckerNode: (cid, sid) => {
    const cur = get().hidden.checker;
    const inner = { ...(cur[cid] || {}) };
    delete inner[sid];
    const checker = { ...cur };
    if (Object.keys(inner).length) checker[cid] = inner; else delete checker[cid];
    const h: Hidden = { ...get().hidden, checker };
    persist(get().layout, h); set({ hidden: h });
  },
}));
