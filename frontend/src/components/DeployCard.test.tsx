import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { DeployCard, manageableComponents, opPayload, VnstatBlock } from "./DeployCard";
import { FORM_DEFAULT, type FormData } from "./DeployForm";

const remna: FormData = { ...FORM_DEFAULT, mode: "remnanode", optimize: true, install_warp: true, install_trafficguard: true };
const haproxy: FormData = { ...FORM_DEFAULT, mode: "haproxy", optimize: true, install_trafficguard: true };

describe("manageableComponents", () => {
  it("lists remnanode components incl warp/ssl/hysteria2 when enabled", () => {
    const ids = manageableComponents(remna).map(c => c.id);
    expect(ids).toEqual([
      "node_accelerator", "trafficguard", "test_tools", "remnanode", "masking", "warp", "ssl", "hysteria2",
    ]);
  });

  it("omits warp when install_warp is off", () => {
    const ids = manageableComponents({ ...remna, install_warp: false }).map(c => c.id);
    expect(ids).not.toContain("warp");
  });

  it("lists test_tools in both modes, omitted when install_test_tools is off", () => {
    expect(manageableComponents(remna).map(c => c.id)).toContain("test_tools");
    expect(manageableComponents(haproxy).map(c => c.id)).toContain("test_tools");
    expect(manageableComponents({ ...remna, install_test_tools: false }).map(c => c.id))
      .not.toContain("test_tools");
  });

  it("omits node_accelerator when optimize is off and trafficguard when disabled", () => {
    const ids = manageableComponents({ ...remna, optimize: false, install_trafficguard: false }).map(c => c.id);
    expect(ids).not.toContain("node_accelerator");
    expect(ids).not.toContain("trafficguard");
  });

  it("haproxy mode lists haproxy, not remnanode/masking/ssl", () => {
    const ids = manageableComponents(haproxy).map(c => c.id);
    expect(ids).toContain("haproxy");
    expect(ids).not.toContain("remnanode");
    expect(ids).not.toContain("masking");
    expect(ids).not.toContain("ssl");
  });

  it("never exposes the non-manageable steps (connect/update/network SSH ports)", () => {
    const ids = manageableComponents(remna).map(c => c.id);
    for (const forbidden of ["connect", "update", "ssh_port", "reboot"]) {
      expect(ids).not.toContain(forbidden);
    }
  });
});

describe("opPayload", () => {
  it("coerces string ports to ints and nullable tokens", () => {
    const p = opPayload({ ...remna, current_ssh_port: "22", new_ssh_port: "2222", remnanode_port: "2222", remnanode_token: "", plugin_uuid: "" });
    expect(p.current_ssh_port).toBe(22);
    expect(p.new_ssh_port).toBe(2222);
    expect(p.remnanode_port).toBe(2222);
    expect(p.remnanode_token).toBeNull();
    expect(p.plugin_uuid).toBeNull();
    expect(typeof p.haproxy_source_port).toBe("number");
  });
});

describe("waiting deploy recovery", () => {
  it("offers an idempotent restart while the deploy is still pending", () => {
    const onRestart = vi.fn().mockResolvedValue(undefined);
    render(<DeployCard
      job={{
        taskId: "waiting-1", domain: "node.example", ip: "1.2.3.4",
        newSshPort: 2222, startedAt: Date.now(), savedForm: remna,
      }}
      onRemove={vi.fn()} onEdit={vi.fn()} onRetry={vi.fn()}
      onRestart={onRestart} onStatusChange={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Перезапустить ожидающий деплой" }));
    expect(onRestart).toHaveBeenCalledTimes(1);
  });
});

describe("collapsed success card", () => {
  beforeEach(() => {
    // The success card polls /api/stats/node and, once expanded, SpeedtestBlock +
    // RemnanodeVersionBlock each fetch on mount. Stub fetch so none of them throw
    // synchronously on a relative URL in jsdom.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ online: true, securityStats: null, trafficStats: null, certInfo: null }),
    }));
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("collapses a success node by default and expands on click", () => {
    render(<DeployCard
      job={{
        taskId: "ok-1", domain: "ok.example", ip: "1.2.3.4",
        newSshPort: 2222, startedAt: Date.now(), savedForm: remna,
        finalStatus: "success",
      }}
      onRemove={vi.fn()} onEdit={vi.fn()} onRetry={vi.fn()}
      onRestart={vi.fn()} onStatusChange={vi.fn()}
    />);

    // Collapsed: only name + security/cert + expand affordance, no heavy blocks.
    expect(screen.getByText("Развернуть")).toBeInTheDocument();
    expect(screen.queryByText("Управление компонентами")).not.toBeInTheDocument();

    // Expand via the collapsed card.
    fireEvent.click(screen.getByRole("button", { name: "Развернуть карточку ok.example" }));
    expect(screen.getByText("Управление компонентами")).toBeInTheDocument();
    expect(screen.queryByText("Развернуть")).not.toBeInTheDocument();
  });
});

describe("expanded success card — domain + remnanode image controls", () => {
  const expand = () =>
    fireEvent.click(screen.getByRole("button", { name: "Развернуть карточку ok.example" }));

  const okJob = (taskId: string) => ({
    taskId, domain: "ok.example", ip: "1.2.3.4",
    newSshPort: 2222, startedAt: Date.now(), savedForm: remna,
    finalStatus: "success" as const,
  });

  const stubFetch = (versions: string[], current: string | null) => {
    vi.stubGlobal("fetch", vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.includes("/api/node/remnanode/versions")) {
        return { ok: true, json: async () => ({ versions, current, source: "registry" }) };
      }
      return { ok: true, json: async () => ({ online: true, securityStats: null, trafficStats: null, certInfo: null }) };
    }));
  };

  beforeEach(() => { stubFetch(["latest", "v2.8.0", "v2.7.0"], "v2.8.0"); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("shows «Сменить домен» as a button that opens the domain wizard", () => {
    render(<DeployCard
      job={okJob("ok-3")}
      onRemove={vi.fn()} onEdit={vi.fn()} onRetry={vi.fn()}
      onRestart={vi.fn()} onStatusChange={vi.fn()}
    />);
    expand();

    const btn = screen.getByRole("button", { name: "Сменить домен ноды" });
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(screen.getByText("Сменить домен ноды")).toBeInTheDocument();
  });

  it("renders a native <select> of remnanode versions with current pre-selected", async () => {
    render(<DeployCard
      job={okJob("ok-4")}
      onRemove={vi.fn()} onEdit={vi.fn()} onRetry={vi.fn()}
      onRestart={vi.fn()} onStatusChange={vi.fn()}
    />);
    expand();
    fireEvent.click(screen.getByRole("button", { name: "Сменить образ remnanode" }));

    const select = await screen.findByRole("combobox", { name: "Версия образа remnanode" });
    expect((select as HTMLSelectElement).value).toBe("v2.8.0");

    const optionLabels = within(select).getAllByRole("option").map(o => o.textContent);
    expect(optionLabels).toEqual(["Выберите версию…", "latest", "v2.8.0", "v2.7.0"]);
  });

  it("falls back to a manual docker-tag input when the versions list is empty", async () => {
    stubFetch([], null);
    render(<DeployCard
      job={okJob("ok-5")}
      onRemove={vi.fn()} onEdit={vi.fn()} onRetry={vi.fn()}
      onRestart={vi.fn()} onStatusChange={vi.fn()}
    />);
    expand();
    fireEvent.click(screen.getByRole("button", { name: "Сменить образ remnanode" }));

    const input = await screen.findByRole("textbox", { name: "Тег образа remnanode" });
    expect(input).toBeInTheDocument();

    const replace = screen.getByRole("button", { name: "Заменить образ remnanode на указанный тег" });
    expect(replace).toBeDisabled();
    fireEvent.change(input, { target: { value: "v2.0.1" } });
    expect(replace).not.toBeDisabled();
  });
});

describe("VnstatBlock", () => {
  const form = { ...FORM_DEFAULT, ip: "1.2.3.4", change_ssh_port: false };
  afterEach(() => { vi.unstubAllGlobals(); });

  it("renders the header and formatted totals from a mocked fetch", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ip: "1.2.3.4", online: true,
        interfaces: [{
          name: "eth0",
          rx_total: 5368709120, tx_total: 2147483648,   // 5 ГБ / 2 ГБ
          rx_month: 1073741824, tx_month: 536870912,     // 1 ГБ / 512 МБ
          rx_day: 104857600,    tx_day: 52428800,        // 100 МБ / 50 МБ
          top_days: [
            { date: "2026-09-04", rx: 3221225472, tx: 1073741824 },
            { date: "2026-09-03", rx: 2147483648, tx: 536870912 },
          ],
        }],
        error: null,
      }),
    }));
    render(<VnstatBlock form={form} />);

    expect(await screen.findByText("Трафик (vnstat)")).toBeInTheDocument();
    expect(await screen.findByText("5.00 ГБ")).toBeInTheDocument();  // rx_total
    expect(screen.getByText("2.00 ГБ")).toBeInTheDocument();         // tx_total
    expect(screen.getByText("04.09")).toBeInTheDocument();           // топ-день
    expect(screen.getByText("03.09")).toBeInTheDocument();
  });

  it("shows the empty state (install_vnstat hint) when no interfaces are reported", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ip: "1.2.3.4", online: true, interfaces: [], error: null }),
    }));
    render(<VnstatBlock form={form} />);

    expect(await screen.findByText(/vnstat не установлен/)).toBeInTheDocument();
    expect(screen.getByText(/Установить vnstat/)).toBeInTheDocument();
  });

  it("offers an interface selector when more than one interface is present", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ip: "1.2.3.4", online: true,
        interfaces: [
          { name: "eth0", rx_total: 1, tx_total: 1, rx_month: 0, tx_month: 0, rx_day: 0, tx_day: 0, top_days: [] },
          { name: "ens3", rx_total: 2, tx_total: 2, rx_month: 0, tx_month: 0, rx_day: 0, tx_day: 0, top_days: [] },
        ],
        error: null,
      }),
    }));
    render(<VnstatBlock form={form} />);

    const sel = await screen.findByRole("combobox", { name: "Интерфейс vnstat" });
    expect((sel as HTMLSelectElement).value).toBe("eth0");
    expect(within(sel as HTMLElement).getAllByRole("option").map(o => o.textContent))
      .toEqual(["eth0", "ens3"]);
  });

  it("shows the vnstat-not-installed reason as an empty state, not an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ip: "1.2.3.4", online: false, interfaces: [], error: "vnstat not installed" }),
    }));
    render(<VnstatBlock form={form} />);

    expect(await screen.findByText(/vnstat не установлен/)).toBeInTheDocument();
  });
});
