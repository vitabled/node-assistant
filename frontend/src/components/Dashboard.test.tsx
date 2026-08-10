import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { Dashboard } from "./Dashboard";

// Wave-7: per-node hiding on the Xray uptime tab. A hidden node leaves the
// country groups + counts and moves to the «Не отслеживаются» section; the
// hide/unhide buttons POST to the shared hidden set.

const NODES = [
  { stableId: "n1", name: "Alpha", groupName: "Germany", subId: "s1", protocol: "vless",
    online: true, latencyMs: 40, uptime30d: 99.9, bars: [], hidden: false },
  { stableId: "n2", name: "Beta", groupName: "Germany", subId: "s1", protocol: "vless",
    online: false, latencyMs: -1, uptime30d: 10, bars: [], hidden: true },
];

function statuspage(nodes: any[]) {
  const shown = nodes.filter(n => !n.hidden);
  return {
    container: "running", reachable: true, nodes,
    subscriptions: [{ id: "s1", label: "sub-1" }],
    global: {
      state: shown.every(n => n.online) ? "ok" : "partial",
      uptime30d: 99.9, protocols: ["vless"],
      total: shown.length, online: shown.filter(n => n.online).length,
      offline: shown.filter(n => !n.online).length,
    },
  };
}

function installFetch() {
  const fn = vi.fn(async (url: string, opts?: any) => {
    const ok = (body: any) => ({ ok: true, status: 200, json: async () => body } as any);
    if (url.startsWith("/api/checker/instances")) return ok({ instances: [{ id: "local", name: "Локальный" }] });
    if (url.startsWith("/api/checker/statuspage")) return ok(statuspage(NODES));
    if (url.startsWith("/api/checker/incidents")) return ok({ incidents: [] });
    if (url.startsWith("/api/subscriptions/status")) return ok([]);
    if (url === "/api/stats/users/hidden/checker") return ok({ hidden: {} });
    if (url.startsWith("/api/checker/")) return ok({});
    return ok({});
  });
  (globalThis as any).fetch = fn;
  return fn;
}

const hiddenPosts = (fn: any) =>
  fn.mock.calls
    .filter(([u]: any[]) => u === "/api/stats/users/hidden/checker")
    .map(([, o]: any[]) => JSON.parse(o.body));

afterEach(() => vi.restoreAllMocks());

describe("Dashboard › Xray uptime hiding", () => {
  it("puts a hidden node in the «Не отслеживаются» section, not the groups", async () => {
    installFetch();
    render(<Dashboard />);
    // hidden node lands in its own section
    const section = await screen.findByText(/Не отслеживаются/);
    expect(section).toBeInTheDocument();
    // banner counts only the one shown node
    expect(await screen.findByText(/1 из 1 узлов онлайн/)).toBeInTheDocument();
  });

  it("hides a node with the eye-off button (POST hidden:true)", async () => {
    const fn = installFetch();
    render(<Dashboard />);
    const hideBtn = await screen.findByTitle("Убрать из отслеживания");
    fireEvent.click(hideBtn);
    await waitFor(() => {
      const posts = hiddenPosts(fn);
      expect(posts).toContainEqual(
        expect.objectContaining({ checker_id: "local", stable_id: "n1", hidden: true }));
    });
  });

  it("restores a node with the eye button (POST hidden:false)", async () => {
    const fn = installFetch();
    render(<Dashboard />);
    const restoreBtn = await screen.findByTitle("Вернуть в отслеживание");
    fireEvent.click(restoreBtn);
    await waitFor(() => {
      const posts = hiddenPosts(fn);
      expect(posts).toContainEqual(
        expect.objectContaining({ checker_id: "local", stable_id: "n2", hidden: false }));
    });
  });
});

describe("Dashboard › скрытие истории доступности (Wave-4)", () => {
  function installFetchWithIncident() {
    const fn = vi.fn(async (url: string, opts?: any) => {
      const ok = (body: any) => ({ ok: true, status: 200, json: async () => body } as any);
      if (url.startsWith("/api/checker/instances")) return ok({ instances: [{ id: "local", name: "Локальный" }] });
      if (url.startsWith("/api/checker/statuspage")) return ok(statuspage(NODES));
      if (url.startsWith("/api/checker/incidents")) return ok({ incidents: [
        { start: 1786000000, durationSec: 300, name: "Alpha", group: "Germany", reason: "timeout", ongoing: false },
      ] });
      if (url.startsWith("/api/subscriptions/status")) return ok([]);
      if (url === "/api/stats/users/hidden/checker") return ok({ hidden: {} });
      if (url.startsWith("/api/checker/")) return ok({});
      return ok({});
    });
    (globalThis as any).fetch = fn;
    return fn;
  }

  afterEach(() => { localStorage.removeItem("ni_hide_incidents"); });

  it("eye-кнопка скрывает список и запоминает выбор в localStorage", async () => {
    installFetchWithIncident();
    render(<Dashboard />);
    // инцидент виден
    expect(await screen.findByText(/была недоступна/)).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Скрыть историю"));
    expect(screen.queryByText(/была недоступна/)).toBeNull();
    expect(localStorage.getItem("ni_hide_incidents")).toBe("1");
    // и возвращается обратно
    fireEvent.click(screen.getByTitle("Показать историю"));
    expect(await screen.findByText(/была недоступна/)).toBeInTheDocument();
  });

  it("скрытое состояние восстанавливается из localStorage", async () => {
    localStorage.setItem("ni_hide_incidents", "1");
    installFetchWithIncident();
    render(<Dashboard />);
    await screen.findByText(/История доступности/);
    expect(screen.queryByText(/была недоступна/)).toBeNull();
  });
});
