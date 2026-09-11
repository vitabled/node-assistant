import { fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Heavy children are stubbed — we only exercise DeployDashboard's job loading,
// the per-account localStorage key, and the server sync. The stub renders a
// "remove" button so we can drive a real mutation (remove → PUT) end-to-end.
vi.mock("./DeployCard", () => ({
  DeployCard: ({ job, nodeName, onRemove }: { job: { domain: string; taskId: string }; nodeName?: string; onRemove: (id: string) => void }) => (
    <div>
      CARD:{job.domain}
      {nodeName ? <span data-testid="node-name">{nodeName}</span> : null}
      <button onClick={() => onRemove(job.taskId)}>remove</button>
    </div>
  ),
}));
let capturedPreset: Record<string, unknown> | undefined;
vi.mock("./DeployForm", () => ({
  DeployForm: ({ preset }: { preset?: Record<string, unknown> }) => {
    capturedPreset = preset;
    return <div>FORM</div>;
  },
}));

import { DeployDashboard } from "./DeployDashboard";
import { addAccount, forget, getSnapshot } from "../auth/store";

function reset() {
  capturedPreset = undefined;
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

type Call = { url: string; method: string; body?: unknown };
type Handler = (url: string, init?: RequestInit) => Promise<unknown> | unknown;

// Stub global fetch with a router + call recorder. Returns the recorded calls
// so tests can assert exactly which requests (method/body) went out.
function stubFetch(handler: Handler) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown;
    if (init?.body) { try { body = JSON.parse(String(init.body)); } catch { body = String(init.body); } }
    calls.push({ url, method, body });
    return handler(url, init);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

const job = (domain: string, taskId = "t1") => ({
  taskId, domain, ip: "1.2.3.4", newSshPort: 2222, startedAt: 1, savedForm: {},
});

describe("DeployDashboard", () => {
  beforeEach(() => {
    reset();
    addAccount({ id: "id-a", login: "alice", token: "t" });
    // Default: offline, so the mount GET degrades to localStorage unless a test
    // installs its own router via stubFetch().
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads jobs from the active account's per-account key", () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("node1.example")]));
    render(<DeployDashboard />);
    expect(screen.getByText("CARD:node1.example")).toBeInTheDocument();
  });

  it("shows the empty state when the account has no jobs", () => {
    render(<DeployDashboard />);
    expect(screen.getAllByText("Нет задач деплоя").length).toBeGreaterThan(0);
  });

  it("protects the node search field from browser autofill", () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("node1.example")]));
    render(<DeployDashboard />);
    const search = screen.getByRole("searchbox", { name: "Поиск нод" });
    expect(search).toHaveAttribute("autoComplete", "off");
  });

  it("ignores the legacy un-suffixed deploy_jobs key (isolation)", () => {
    // Old global key must NOT leak into an account's view.
    localStorage.setItem("deploy_jobs", JSON.stringify([job("leaked.example")]));
    render(<DeployDashboard />);
    expect(screen.queryByText("CARD:leaked.example")).not.toBeInTheDocument();
    expect(screen.getAllByText("Нет задач деплоя").length).toBeGreaterThan(0);
  });

  // ── server sync (server is the source of truth) ──────────────

  it("renders the server list and mirrors it into the local cache when nothing is pending", async () => {
    const serverJob = job("server.example", "t1");
    const { calls } = stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [serverJob] }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByText("CARD:server.example")).toBeInTheDocument());
    // No local-only card → no write calls on mount (no POST/DELETE).
    expect(calls.some(c => c.url.includes("/api/deploy-jobs") && c.method !== "GET")).toBe(false);
    // Server cards are ADDED to the local cache (server is the source of truth).
    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem("deploy_jobs_id-a") ?? "[]")).toEqual([serverJob]);
    });
  });

  it("uploads a local-only card to the server and mirrors it in the cache", async () => {
    const local = job("local.example", "t-local");
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([local]));
    const { calls } = stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [] }) };
      if (url.includes("/api/deploy-jobs") && method === "POST") return { ok: true, json: async () => ({ job: local }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    // Uploaded via a per-card upsert (POST), never a full-list PUT.
    await waitFor(() => {
      const post = calls.find(c => c.method === "POST" && c.url.includes("/api/deploy-jobs"));
      expect(post?.body).toEqual(local);
    });
    expect(calls.some(c => c.method === "PUT")).toBe(false);
    expect(screen.getByText("CARD:local.example")).toBeInTheDocument();
    // Confirmed card stays mirrored in the local cache (no drain-to-empty).
    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem("deploy_jobs_id-a") ?? "[]")).toEqual([local]);
    });
  });

  it("uploads local-only cards even when the server list is non-empty (no empty-server guard)", async () => {
    const serverJob = job("server.example", "t-server");
    const localNew = job("new.example", "t-new");
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([localNew]));
    const { calls } = stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [serverJob] }) };
      if (url.includes("/api/deploy-jobs") && method === "POST") return { ok: true, json: async () => ({ job: localNew }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByText("CARD:server.example")).toBeInTheDocument());
    expect(screen.getByText("CARD:new.example")).toBeInTheDocument();
    await waitFor(() => {
      const post = calls.find(c => c.method === "POST" && c.url.includes("/api/deploy-jobs"));
      expect(post?.body).toEqual(localNew);
    });
  });

  it("uses the server list as source of truth (server wins on the same taskId)", async () => {
    const serverJob = job("server.example", "t1");
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("local.example", "t1")]));
    stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [serverJob] }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByText("CARD:server.example")).toBeInTheDocument());
    expect(screen.queryByText("CARD:local.example")).not.toBeInTheDocument();
  });

  it("a mutation (remove) deletes the card on the server, not a full-list PUT", async () => {
    const existing = job("node1.example", "t1");
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([existing]));
    const { calls } = stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [existing] }) };
      if (url.includes("/api/deploy-jobs") && method === "DELETE") return { ok: true, json: async () => ({ ok: true }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node1.example")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "remove" }));

    await waitFor(() => {
      const del = calls.find(c => c.method === "DELETE" && c.url.includes("/api/deploy-jobs"));
      expect(del?.url).toContain("t1");
    });
    expect(screen.queryByText("CARD:node1.example")).not.toBeInTheDocument();
    expect(calls.some(c => c.method === "PUT")).toBe(false);
  });

  it("keeps a local-only card cached when its upsert fails (no loss)", async () => {
    const serverJob = job("server.example", "t-server");
    const localNew = job("new.example", "t-new");
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([localNew]));
    stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [serverJob] }) };
      if (url.includes("/api/deploy-jobs") && method === "POST") throw new Error("offline");
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByText("CARD:new.example")).toBeInTheDocument());
    // Upsert failed → the pending card stays in the cache, alongside the merged
    // server card (nothing is lost).
    await waitFor(() => {
      const ids = JSON.parse(localStorage.getItem("deploy_jobs_id-a") ?? "[]").map((j: { taskId: string }) => j.taskId);
      expect(ids).toEqual(["t-server", "t-new"]);
    });
  });

  it("degrades to localStorage when the server is unreachable", async () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("offline.example", "t-off")]));
    stubFetch(() => { throw new Error("network down"); });
    render(<DeployDashboard />);

    // Card renders from the local buffer; the failed GET doesn't crash the UI.
    expect(screen.getByText("CARD:offline.example")).toBeInTheDocument();
    await new Promise(r => setTimeout(r, 0));
    expect(screen.getByText("CARD:offline.example")).toBeInTheDocument();
  });

  // ── node names from Remnawave ──────────────────────────────

  it("passes the node name from /api/remnawave/nodes into the card", async () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("node1.example", "t1")]));
    stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/remnawave/nodes")) {
        return { ok: true, json: async () => [{ uuid: "u1", name: "astra1nl", address: "1.2.3.4" }] };
      }
      if (url.includes("/api/deploy-jobs") && method === "GET") {
        return { ok: true, json: async () => ({ jobs: [job("node1.example", "t1")] }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByTestId("node-name")).toHaveTextContent("astra1nl"));
  });

  it("renders without a node name when /api/remnawave/nodes fails", async () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("node1.example", "t1")]));
    stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/remnawave/nodes")) throw new Error("no nodes");
      if (url.includes("/api/deploy-jobs") && method === "GET") {
        return { ok: true, json: async () => ({ jobs: [job("node1.example", "t1")] }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByText("CARD:node1.example")).toBeInTheDocument());
    expect(screen.queryByTestId("node-name")).not.toBeInTheDocument();
  });

  it.each([
    ["ничего", [], [], "remnanode"],
    ["только Remnanode", ["Remnanode"], ["remnanode"], "remnanode"],
    ["только SSL", ["SSL-сертификат"], ["ssl"], "remnanode"],
    ["Remnanode и SSL", ["Remnanode", "SSL-сертификат"], ["remnanode", "ssl"], "remnanode"],
    ["только HAProxy", ["HAProxy"], ["haproxy"], "haproxy"],
  ])("existing server sends positive selection: %s", async (_name, labels, expected, expectedMode) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: { remnanode: "present", ssl: "present", haproxy: "present" },
        settings: {},
      }),
    }));
    render(<DeployDashboard />);

    fireEvent.click(screen.getByRole("button", { name: /Существующий сервер/ }));
    fireEvent.change(screen.getByPlaceholderText("1.2.3.4"), { target: { value: "1.2.3.4" } });
    const password = document.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(password, { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Определить компоненты" }));
    await screen.findByText("Что доустановить");

    for (const label of labels) {
      fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(label) }));
    }
    fireEvent.click(screen.getByRole("button", { name: "Продолжить к деплою" }));

    await waitFor(() => expect(capturedPreset?.install_components).toEqual(expected));
    expect(capturedPreset?.mode).toBe(expectedMode);
    expect(capturedPreset?.skip_components).toEqual([]);
  });
});
