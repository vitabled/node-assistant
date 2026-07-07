import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { PanelManageModal, panelManageableComponents } from "./PanelManageModal";
import type { PanelJobSummary } from "./PanelDashboard";
import type { PanelDeployPayload } from "./PanelDeployForm";

// Minimal wire-shaped payload; only the fields panelManageableComponents reads
// (target / install_test_tools) matter here.
const base: PanelDeployPayload = {
  target: "panel",
  ip: "1.2.3.4",
  ssh_user: "root",
  ssh_password: "pw",
  ssh_port: 22,
  panel_domain: "panel.example.com",
  sub_domain: "",
  email: "",
  reverse_proxy: "caddy",
  cert_provider: "letsencrypt",
  cf_api_key: "",
  enable_webhooks: false,
  webhook_url: "",
  extra_env: {},
  sub_server: null,
  subpage_html: "",
  install_test_tools: true,
};

const ids = (p: PanelDeployPayload) => panelManageableComponents(p).map(c => c.id);

describe("panelManageableComponents", () => {
  it("target=panel → panel + docker + test_tools + reverse_proxy (no subpage)", () => {
    expect(ids(base)).toEqual(["panel", "docker", "test_tools", "reverse_proxy"]);
  });

  it("target=subpage → subpage (no panel)", () => {
    expect(ids({ ...base, target: "subpage" })).toEqual(["subpage", "docker", "test_tools", "reverse_proxy"]);
  });

  it("target=both → both panel and subpage", () => {
    expect(ids({ ...base, target: "both" })).toEqual(["panel", "subpage", "docker", "test_tools", "reverse_proxy"]);
  });

  it("drops test_tools when install_test_tools is false", () => {
    expect(ids({ ...base, install_test_tools: false })).toEqual(["panel", "docker", "reverse_proxy"]);
  });

  it("docker is reinstall-only (not removable); everything else is removable", () => {
    const comps = panelManageableComponents({ ...base, target: "both" });
    const removable = Object.fromEntries(comps.map(c => [c.id, c.removable]));
    expect(removable.docker).toBe(false);
    expect(removable.panel).toBe(true);
    expect(removable.subpage).toBe(true);
    expect(removable.test_tools).toBe(true);
    expect(removable.reverse_proxy).toBe(true);
  });
});

// ── render smoke — mounts the real modal (catches hook/JSX errors tsc can't) ──
const job: PanelJobSummary = {
  id: "j1", taskId: "t1", savedForm: { ...base }, createdAt: Date.now(),
  target: "panel", finalStatus: "success",
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("PanelManageModal (render)", () => {
  it("renders the Компоненты tab with the component list + server-data editor", () => {
    render(<PanelManageModal job={job} onClose={() => {}} onEditJob={() => {}} />);
    expect(screen.getByText("Управление компонентами")).toBeTruthy();
    expect(screen.getByText("Панель Remnawave")).toBeTruthy();
    expect(screen.getByText("Данные сервера")).toBeTruthy();
    // the two tabs exist
    expect(screen.getByText("Компоненты")).toBeTruthy();
    expect(screen.getByText("Статистика")).toBeTruthy();
  });

  it("switching to Статистика fetches /api/stats/node and renders traffic/security", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({
        online: true,
        securityStats: { fail2banActive: 2, fail2banTotal: 9, trafficGuardActive: 0 },
        trafficStats: { today: { rx: 1073741824, tx: 0, total: 1073741824 }, week: { rx: 0, tx: 0, total: 0 }, month: { rx: 0, tx: 0, total: 0 } },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<PanelManageModal job={job} onClose={() => {}} onEditJob={() => {}} />);
    fireEvent.click(screen.getByText("Статистика"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/stats/node");
    expect(JSON.parse(opts.body).ip).toBe("1.2.3.4");
    await screen.findByText("Сетевой трафик");
    await screen.findByText("Безопасность сервера");
  });
});
