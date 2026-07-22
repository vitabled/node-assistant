import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Stub every heavy tab component so we can drive App's tab logic in isolation.
// Factories are inlined (no shared helper) because vi.mock is hoisted above any
// top-level variable.
vi.mock("./components/Dashboard", () => ({ Dashboard: () => <div>TAB:Dashboard</div> }));
vi.mock("./components/DeployDashboard", () => ({ DeployDashboard: () => <div>TAB:DeployDashboard</div> }));
vi.mock("./components/Settings", () => ({ Settings: () => <div>TAB:Settings</div> }));
vi.mock("./components/Templates", () => ({ Templates: () => <div>TAB:Templates</div> }));
vi.mock("./components/TrafficRules", () => ({ TrafficRules: () => <div>TAB:TrafficRules</div> }));
vi.mock("./components/CertsForm", () => ({ CertsForm: () => <div>TAB:CertsForm</div> }));
vi.mock("./components/infra/InfraDashboard", () => ({ InfraDashboard: () => <div>TAB:InfraDashboard</div> }));
vi.mock("./components/infra/InfraProviders", () => ({ InfraProviders: () => <div>TAB:InfraProviders</div> }));
vi.mock("./components/infra/InfraProjects", () => ({ InfraProjects: () => <div>TAB:InfraProjects</div> }));
vi.mock("./components/infra/InfraServices", () => ({ InfraServices: () => <div>TAB:InfraServices</div> }));
vi.mock("./components/infra/InfraPayments", () => ({ InfraPayments: () => <div>TAB:InfraPayments</div> }));
vi.mock("./components/infra/InfraSettings", () => ({ InfraSettings: () => <div>TAB:InfraSettings</div> }));
vi.mock("./components/infra/InfraApiTokens", () => ({ InfraApiTokens: () => <div>TAB:InfraApiTokens</div> }));
vi.mock("./components/infra/Toast", () => ({ Toaster: () => null }));
vi.mock("./components/StepProgress", () => ({ StepProgress: () => null, RENEW_STEPS: [] }));
vi.mock("./components/TerminalOutput", () => ({ TerminalOutput: () => null }));
vi.mock("./hooks/useTaskStream", () => ({ useTaskStream: () => {} }));

import App, { CRUMB } from "./App";
import { addAccount, forget, getSnapshot, tabKey } from "./auth/store";

function reset() {
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

// Группа в CRUMB — рукописный дубль группировки сайдбара, TypeScript их не
// связывает. Это единственное место, где перенос вкладки между группами можно
// сделать наполовину и не заметить: крошка начнёт противоречить сайдбару.
describe("CRUMB group labels", () => {
  it("puts the Remnawave editors in the Remnawave group", () => {
    for (const tab of ["rw-profiles", "mihomo", "configs"] as const) {
      expect(CRUMB[tab][0]).toBe("Remnawave");
    }
  });

  it("keeps every rw-* tab in the Remnawave group", () => {
    for (const [tab, [group]] of Object.entries(CRUMB)) {
      if (tab.startsWith("rw-")) expect(group).toBe("Remnawave");
    }
  });
});

describe("App tab persistence (per account)", () => {
  beforeEach(() => { reset(); addAccount({ id: "id-a", login: "alice", token: "t" }); });
  afterEach(cleanup);

  it("restores the last-open tab for the active account", () => {
    localStorage.setItem(tabKey("id-a"), "settings");
    render(<App />);
    expect(screen.getByText("TAB:Settings")).toBeInTheDocument();
  });

  it("defaults to dashboard when nothing was persisted", () => {
    render(<App />);
    expect(screen.getByText("TAB:Dashboard")).toBeInTheDocument();
  });

  it("falls back to dashboard for an unknown persisted tab value", () => {
    localStorage.setItem(tabKey("id-a"), "bogus-tab");
    render(<App />);
    expect(screen.getByText("TAB:Dashboard")).toBeInTheDocument();
  });

  it("persists the tab to the account's key on change", () => {
    render(<App />);
    fireEvent.click(screen.getByText("Настройки"));
    expect(screen.getByText("TAB:Settings")).toBeInTheDocument();
    expect(localStorage.getItem(tabKey("id-a"))).toBe("settings");
  });
});
