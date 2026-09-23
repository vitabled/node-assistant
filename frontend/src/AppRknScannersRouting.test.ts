import { describe, expect, it } from "vitest";
import appSource from "./App.tsx?raw";
import { CRUMB } from "./App";
import { NAV_TABS, tabPermission, mergeNavOrder } from "./components/Sidebar";

/**
 * Раздел «RKNscanner» — соседний с Fail2Ban пункт навигации.
 *
 * Связь «пункт сайдбара ↔ блок рендера ↔ крошка» рукописная (обе стороны —
 * обычные строки), TypeScript её не проверяет: тест сторожит, что раздел
 * зарегистрирован во всех трёх местах и не уехал в чужую вкладку.
 */
describe("RKNscanner section routing", () => {
  it("renders RknScanners in its own tab block, right after Fail2Ban", () => {
    const f2bStart = appSource.indexOf('{tab === "f2b-list"');
    const rknStart = appSource.indexOf('{tab === "rkn-scanners"');
    const certsStart = appSource.indexOf('{tab === "certs"');

    expect(f2bStart).toBeGreaterThan(-1);
    expect(rknStart).toBeGreaterThan(f2bStart);      // соседний раздел, сразу после fail2ban
    expect(certsStart).toBeGreaterThan(rknStart);
    expect(appSource.slice(rknStart, certsStart)).toContain("<RknScanners />");
    // и не подмешивается в чужую вкладку
    expect(appSource.slice(f2bStart, rknStart)).not.toContain("<RknScanners />");

    // Тяжёлый экран грузится лениво, как большинство разделов.
    expect(appSource).toMatch(/const RknScanners\s*= lazy\(\(\) => import\("\.\/components\/RknScanners"\)/);
  });

  it("registers the sidebar item next to Fail2Ban with its own permission", () => {
    const tabs = NAV_TABS.map(i => i.tab as string);
    const i = tabs.indexOf("rkn-scanners");

    expect(i).toBeGreaterThan(-1);
    expect(tabs[i - 1]).toBe("f2b-list");            // ровно рядом с fail2ban
    expect(NAV_TABS[i].label).toBe("RKNscanner");
    expect(tabPermission("rkn-scanners")).toBe("deploy.view");
  });

  it("keeps the new tab in the default nav order and in a saved order", () => {
    const def = mergeNavOrder(null).find(g => g.title === "Управление")!;
    expect(def.tabs).toContain("rkn-scanners");
    expect(def.tabs[def.tabs.indexOf("rkn-scanners") - 1]).toBe("f2b-list");

    // Порядок, сохранённый ДО появления раздела, не должен его потерять.
    const saved = mergeNavOrder([{ title: "Управление", tabs: ["dashboard"] }]);
    expect(saved.find(g => g.title === "Управление")!.tabs).toContain("rkn-scanners");
  });

  it("has a breadcrumb for the new tab", () => {
    expect(CRUMB["rkn-scanners"]).toEqual(["Node Assistant", "RKNscanner"]);
  });
});
