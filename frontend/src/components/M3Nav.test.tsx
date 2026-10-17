import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

let allow: (permission: string) => boolean = () => true;
vi.mock("../auth/usePermissions", () => ({
  usePermissions: () => ({
    user: null, permissions: [], loading: false,
    can: (p: string) => allow(p),
  }),
}));

import { M3Nav, M3_GROUPS, M3_FOOTER, M3_SECTIONS } from "./M3Nav";
import { NAV_TABS } from "./Sidebar";

// Drawer по умолчанию раскрыт только на ширине ≥1100px (как в макете).
Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });

afterEach(() => { cleanup(); allow = () => true; });

describe("M3Nav", () => {
  // Пункт, потерянный при раскладке по разделам, тихо пропал бы из навигации.
  it("places every NAV_TABS item exactly once", () => {
    const m3 = [...M3_GROUPS.flatMap(g => g.items.map(i => i.tab)), ...M3_FOOTER.map(i => i.tab)];
    expect(new Set(m3).size).toBe(m3.length);
    expect([...m3].sort()).toEqual(NAV_TABS.map(i => i.tab).sort());
  });

  it("every group belongs to a declared section", () => {
    const ids = new Set(M3_SECTIONS.map(s => s.id));
    for (const g of M3_GROUPS) expect(ids.has(g.sec)).toBe(true);
  });

  it("shows the active section's groups in the drawer", () => {
    render(<M3Nav activeTab="deploy" onTabChange={() => {}} />);
    expect(screen.getByText("Развёртывание")).toBeInTheDocument();
    expect(screen.getByText("Шаблоны Xray")).toBeInTheDocument();
    expect(screen.queryByText("Статус нод")).not.toBeInTheDocument();
  });

  it("rail section click opens its first tab", () => {
    const onTabChange = vi.fn();
    render(<M3Nav activeTab="deploy" onTabChange={onTabChange} />);
    fireEvent.click(screen.getByTestId("m3-section-finance"));
    expect(onTabChange).toHaveBeenCalledWith("infra-dashboard");
  });

  it("drawer item click switches tab", () => {
    const onTabChange = vi.fn();
    render(<M3Nav activeTab="dashboard" onTabChange={onTabChange} />);
    fireEvent.click(screen.getByText("Fail2Ban"));
    expect(onTabChange).toHaveBeenCalledWith("f2b-list");
  });

  it("hides sections without permitted items", () => {
    allow = p => p !== "billing.view" && p !== "cloudflare.view";
    render(<M3Nav activeTab="dashboard" onTabChange={() => {}} />);
    // В «Финансах» остаются только infra-* (billing) и cf-* — всё скрыто.
    expect(screen.queryByTestId("m3-section-finance")).not.toBeInTheDocument();
    expect(screen.getByTestId("m3-section-nodes")).toBeInTheDocument();
  });
});
