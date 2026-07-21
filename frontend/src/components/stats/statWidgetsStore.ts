// Per-account stats-dashboard layout store (Wave-5 Plan G). Holds which widgets
// are shown, their order and width. Persists to the backend (source of truth)
// with a localStorage mirror for instant/offline defaults. Per-widget settings
// (window/checker) stay local to each widget for now.
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
function persist(layout: WidgetInstance[]) {
  try { localStorage.setItem(widgetsKey(), JSON.stringify(layout)); } catch { /* quota/private */ }
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(() => {
    fetch("/api/stats/users/widgets", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        layout: layout.map(w => ({ instance_id: w.instanceId, kind: w.kind, w: w.w, order: w.order, settings: w.settings })),
      }),
    }).catch(() => { /* localStorage already saved */ });
  }, 600);
}

interface State {
  layout: WidgetInstance[];
  editing: boolean;
  hydrated: boolean;
  hydrate: () => Promise<void>;
  setEditing: (v: boolean) => void;
  add: (k: WidgetKind) => void;
  remove: (id: string) => void;
  resize: (id: string, w: 1 | 2) => void;
  move: (id: string, dir: -1 | 1) => void;
}

export const useStatWidgets = create<State>((set, get) => ({
  layout: [],
  editing: false,
  hydrated: false,
  hydrate: async () => {
    let layout: WidgetInstance[] = [];
    try {
      const r = await fetch("/api/stats/users/widgets");
      if (r.ok) layout = normalize((await r.json()).layout);
    } catch { /* fall through */ }
    if (!layout.length) {
      try { const ls = localStorage.getItem(widgetsKey()); if (ls) layout = normalize(JSON.parse(ls)); } catch { /* ignore */ }
    }
    if (!layout.length) layout = defaultLayout();
    set({ layout, hydrated: true });
  },
  setEditing: v => set({ editing: v }),
  add: k => {
    const l = [...get().layout];
    l.push({ instanceId: newId(), kind: k, w: DEFAULT_W[k], order: l.length, settings: {} });
    persist(l); set({ layout: l });
  },
  remove: id => {
    const l = get().layout.filter(w => w.instanceId !== id).map((w, i) => ({ ...w, order: i }));
    persist(l); set({ layout: l });
  },
  resize: (id, w) => {
    const l = get().layout.map(x => (x.instanceId === id ? { ...x, w } : x));
    persist(l); set({ layout: l });
  },
  move: (id, dir) => {
    const l = [...get().layout].sort((a, b) => a.order - b.order);
    const i = l.findIndex(w => w.instanceId === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= l.length) return;
    [l[i], l[j]] = [l[j], l[i]];
    l.forEach((w, k) => (w.order = k));
    persist(l); set({ layout: l });
  },
}));
