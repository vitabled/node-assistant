import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  EMPTY_HIDDEN, normalizeHidden, isNodeHidden, isCheckerNodeHidden,
  filterNodeLoad, filterMigrations, filterCheckerNodes, hiddenCount,
  useStatWidgets,
} from "./statWidgetsStore";

// ── Чистые селекторы: тестируются без рендера ──
describe("hidden selectors", () => {
  const h = {
    nodes: { "uuid-a": "de-1" },
    checker: { local: { n1: "node-1" }, remote7: { n1: "другой n1" } },
  };

  it("keys node widgets on node_uuid", () => {
    expect(isNodeHidden(h, "uuid-a")).toBe(true);
    expect(isNodeHidden(h, "uuid-z")).toBe(false);
    expect(filterNodeLoad(h, [{ node_uuid: "uuid-a" }, { node_uuid: "uuid-b" }]))
      .toEqual([{ node_uuid: "uuid-b" }]);
  });

  // Строка «из A в B» бессмысленна, если скрыт любой из концов.
  it("drops a migration when EITHER end is hidden", () => {
    const migs = [
      { from_node: "uuid-a", to_node: "uuid-b" },
      { from_node: "uuid-b", to_node: "uuid-a" },
      { from_node: "uuid-b", to_node: "uuid-c" },
    ];
    expect(filterMigrations(h, migs)).toEqual([{ from_node: "uuid-b", to_node: "uuid-c" }]);
  });

  // Главный смысл второй оси: stableId уникален только внутри своего чекера.
  it("isolates the same stableId across different checkers", () => {
    expect(isCheckerNodeHidden(h, "local", "n1")).toBe(true);
    expect(isCheckerNodeHidden(h, "other", "n1")).toBe(false);
    expect(filterCheckerNodes(h, "local", [{ stableId: "n1" }, { stableId: "n2" }]))
      .toEqual([{ stableId: "n2" }]);
    expect(filterCheckerNodes(h, "other", [{ stableId: "n1" }])).toEqual([{ stableId: "n1" }]);
  });

  // server-monitor подавляется на бэкенде (Ф4) — иначе учли бы дважды.
  it("passes server-monitor through untouched", () => {
    const rows = [{ stableId: "n1" }, { stableId: "n2" }];
    expect(filterCheckerNodes({ nodes: {}, checker: { "server-monitor": { n1: "x" } } },
      "server-monitor", rows)).toEqual(rows);
  });

  it("counts both axes", () => {
    expect(hiddenCount(h)).toBe(3);
    expect(hiddenCount(EMPTY_HIDDEN)).toBe(0);
  });
});

describe("normalizeHidden", () => {
  it("survives garbage and applies the backend limits", () => {
    expect(normalizeHidden(null)).toEqual(EMPTY_HIDDEN);
    expect(normalizeHidden({ nodes: "nope", checker: 5 })).toEqual(EMPTY_HIDDEN);
    const many = Object.fromEntries(Array.from({ length: 300 }, (_, i) => [`u${i}`, "n"]));
    expect(Object.keys(normalizeHidden({ nodes: many }).nodes)).toHaveLength(200);
    expect(normalizeHidden({ nodes: { u1: "x".repeat(200) } }).nodes.u1).toHaveLength(64);
    expect(normalizeHidden({ nodes: { u1: 42 } }).nodes.u1).toBe("");
  });
});

// ── Стор: гидрация и персист ──
describe("statWidgetsStore hidden state", () => {
  let fetchMock: any;
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ layout: [], hidden: EMPTY_HIDDEN }) }));
    (globalThis as any).fetch = fetchMock;
    useStatWidgets.setState({ layout: [], hidden: EMPTY_HIDDEN, hydrated: false, editing: false });
  });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("migrates the legacy localStorage format (a bare layout array)", async () => {
    (globalThis as any).fetch = vi.fn(async () => ({ ok: false }));
    localStorage.setItem("stat_widgets_none", JSON.stringify([
      { instance_id: "w1", kind: "node-load", w: 2, order: 0, settings: {} },
    ]));
    await useStatWidgets.getState().hydrate();
    expect(useStatWidgets.getState().layout).toHaveLength(1);
    expect(useStatWidgets.getState().hidden).toEqual(EMPTY_HIDDEN);
  });

  it("reads the new {layout, hidden} localStorage format", async () => {
    (globalThis as any).fetch = vi.fn(async () => ({ ok: false }));
    localStorage.setItem("stat_widgets_none", JSON.stringify({
      layout: [{ instance_id: "w1", kind: "node-load", w: 1, order: 0, settings: {} }],
      hidden: { nodes: { u1: "de-1" }, checker: {} },
    }));
    await useStatWidgets.getState().hydrate();
    expect(useStatWidgets.getState().hidden.nodes.u1).toBe("de-1");
  });

  // Ручка — full-replace: тело без hidden обнулило бы набор на сервере.
  it("PUTs layout AND hidden together", async () => {
    await useStatWidgets.getState().hydrate();
    useStatWidgets.getState().hideNode("uuid-a", "de-1");
    vi.runAllTimers();
    const put = fetchMock.mock.calls.find(([, o]: any[]) => o?.method === "PUT");
    expect(put).toBeTruthy();
    const body = JSON.parse(put[1].body);
    expect(body).toHaveProperty("layout");
    expect(body.hidden.nodes["uuid-a"]).toBe("de-1");
  });

  it("hides and shows on both axes", async () => {
    await useStatWidgets.getState().hydrate();
    const s = () => useStatWidgets.getState();
    s().hideNode("u1", "de-1");
    s().hideCheckerNode("local", "n1", "node-1");
    expect(hiddenCount(s().hidden)).toBe(2);
    s().showNode("u1");
    s().showCheckerNode("local", "n1");
    expect(s().hidden).toEqual(EMPTY_HIDDEN);
  });

  // Пустая карта чекера не должна оставаться мусором в документе.
  it("drops a checker entry once its last node is shown again", async () => {
    await useStatWidgets.getState().hydrate();
    useStatWidgets.getState().hideCheckerNode("local", "n1", "x");
    useStatWidgets.getState().showCheckerNode("local", "n1");
    expect(useStatWidgets.getState().hidden.checker).toEqual({});
  });
});
