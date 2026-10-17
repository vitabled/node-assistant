import { fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Heavy children are stubbed — we only exercise DeployDashboard: загрузка списка
// карточек С СЕРВЕРА (data/deployJobsStore), фильтры/поиск и серверные мутации.
// Карточки больше не живут в localStorage: ни чтение, ни запись.
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
import { resetJobs, setJobs, getJobs, type DeployJobRecord } from "../data/deployJobsStore";

/** Что «лежит на сервере» в текущем тесте (GET /api/deploy-jobs). */
let serverJobs: DeployJobRecord[] = [];

function reset() {
  capturedPreset = undefined;
  serverJobs = [];
  resetJobs();
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

/** Засеять серверный список: и снимок стора, и ответ GET. */
function seed(jobs: DeployJobRecord[]) {
  serverJobs = jobs;
  setJobs(jobs);
}

type Call = { url: string; method: string; body?: unknown };
type Handler = (url: string, init?: RequestInit) => Promise<unknown> | unknown;

// Stub global fetch with a router + call recorder. Возвращает записанные вызовы,
// чтобы тесты могли утверждать, какие запросы (метод/тело) ушли.
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

/** Роутер по умолчанию: серверный список карточек + успешные POST/DELETE. */
function stubServer(extra?: Handler) {
  return stubFetch((url, init) => {
    const method = (init?.method ?? "GET").toUpperCase();
    if (extra) {
      const custom = extra(url, init);
      if (custom !== undefined) return custom;
    }
    if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: serverJobs }) };
    if (url.includes("/api/deploy-jobs") && method === "POST") return { ok: true, json: async () => ({ job: {} }) };
    if (url.includes("/api/deploy-jobs") && method === "DELETE") return { ok: true, json: async () => ({ ok: true }) };
    return { ok: true, json: async () => ({}) };
  });
}

const job = (domain: string, taskId = "t1", extra: Partial<DeployJobRecord> = {}): DeployJobRecord => ({
  taskId, domain, ip: "1.2.3.4", newSshPort: 2222, startedAt: 1, savedForm: {}, ...extra,
});

describe("DeployDashboard", () => {
  beforeEach(() => {
    reset();
    addAccount({ id: "id-a", login: "alice", token: "t" });
    // По умолчанию сервер пуст и доступен; офлайн/ошибки задаёт конкретный тест.
    stubServer();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    resetJobs();
  });

  // ── список приходит с сервера ──────────────────────────────

  it("renders cards that come from the server", async () => {
    seed([job("node1.example")]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node1.example")).toBeInTheDocument());
  });

  it("shows the empty state when the account has no jobs", async () => {
    render(<DeployDashboard />);
    await waitFor(() =>
      expect(screen.getAllByText("Нет задач деплоя").length).toBeGreaterThan(0));
  });

  it("does not write the card list into localStorage (no browser cache anymore)", async () => {
    const { calls } = stubServer();
    seed([job("server.example", "t1")]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:server.example")).toBeInTheDocument());

    // никаких записей в кэш и никаких мутаций на монтировании
    expect(localStorage.getItem("deploy_jobs_id-a")).toBeNull();
    expect(calls.some(c => c.method === "POST" || c.method === "DELETE")).toBe(false);
  });

  it("a stale browser cache cannot add cards: server list wins", async () => {
    // Старый кэш этого же аккаунта с карточкой, которой на сервере нет.
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("ghost.example", "t-ghost")]));
    seed([job("server.example", "t1")]);
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByText("CARD:server.example")).toBeInTheDocument());
    expect(screen.queryByText("CARD:ghost.example")).not.toBeInTheDocument();
  });

  it("same taskId: the server version is shown, the cached one is ignored", async () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("local.example", "t1")]));
    seed([job("server.example", "t1")]);
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByText("CARD:server.example")).toBeInTheDocument());
    expect(screen.queryByText("CARD:local.example")).not.toBeInTheDocument();
  });

  it("ignores the legacy un-suffixed deploy_jobs key (isolation)", async () => {
    localStorage.setItem("deploy_jobs", JSON.stringify([job("leaked.example")]));
    render(<DeployDashboard />);
    await waitFor(() =>
      expect(screen.getAllByText("Нет задач деплоя").length).toBeGreaterThan(0));
    expect(screen.queryByText("CARD:leaked.example")).not.toBeInTheDocument();
    expect(getJobs()).toEqual([]);
  });

  it("migrates an old cache to the server once, then drops the cache (nothing lost)", async () => {
    const legacy = job("legacy.example", "t-legacy");
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([legacy]));
    const { calls } = stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/deploy-jobs") && method === "GET") {
        // до заливки сервер пуст, после — карточка уже там
        const posted = calls.some(c => c.method === "POST");
        return { ok: true, json: async () => ({ jobs: posted ? [legacy] : [] }) };
      }
      if (method === "POST") return { ok: true, json: async () => ({ job: legacy }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    await waitFor(() => {
      const post = calls.find(c => c.method === "POST" && c.url.includes("/api/deploy-jobs"));
      expect(post?.body).toEqual(legacy);
    });
    await waitFor(() => expect(screen.getByText("CARD:legacy.example")).toBeInTheDocument());
    await waitFor(() => expect(localStorage.getItem("deploy_jobs_id-a")).toBeNull());
  });

  it("does not upload cached cards when the server already has cards", async () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("local.example", "t-local")]));
    seed([job("server.example", "t-server")]);
    const { calls } = stubServer();
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByText("CARD:server.example")).toBeInTheDocument());
    expect(screen.queryByText("CARD:local.example")).not.toBeInTheDocument();
    expect(calls.some(c => c.method === "POST")).toBe(false);
  });

  it("shows an empty list when the server is unreachable (no localStorage fallback)", async () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("offline.example", "t-off")]));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    render(<DeployDashboard />);

    await waitFor(() =>
      expect(screen.getAllByText("Нет задач деплоя").length).toBeGreaterThan(0));
    expect(screen.queryByText("CARD:offline.example")).not.toBeInTheDocument();
  });

  // ── поиск и фильтры ────────────────────────────────────────

  it("protects the node search field from browser autofill", async () => {
    seed([job("node1.example")]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node1.example")).toBeInTheDocument());
    const search = screen.getByRole("searchbox", { name: "Поиск нод" });
    expect(search).toHaveAttribute("autoComplete", "off");
    expect(search).toHaveAttribute("inputMode", "search");
    expect(search).toHaveAttribute("enterKeyHint", "search");
  });

  it("keeps cards visible when a value is written directly into the search DOM (autofill spill)", async () => {
    seed([job("node1.example", "t1"), job("node2.example", "t2")]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node2.example")).toBeInTheDocument());

    const search = screen.getByRole("searchbox", { name: "Поиск нод" }) as HTMLInputElement;
    expect(search.value).toBe("");

    search.value = "vсskahquyythrgse";
    search.dispatchEvent(new Event("input", { bubbles: true }));

    expect(screen.getByText("CARD:node1.example")).toBeInTheDocument();
    expect(screen.getByText("CARD:node2.example")).toBeInTheDocument();
    expect(screen.queryByText(/совпадений нет/)).not.toBeInTheDocument();
  });

  it("shows a clear button only while the search query is non-empty", async () => {
    seed([job("node1.example")]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node1.example")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: "Очистить поиск" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Поиск нод" }), { target: { value: "node" } });
    expect(screen.getByRole("button", { name: "Очистить поиск" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Очистить поиск" }));
    expect((screen.getByRole("searchbox", { name: "Поиск нод" }) as HTMLInputElement).value).toBe("");
    expect(screen.queryByRole("button", { name: "Очистить поиск" })).not.toBeInTheDocument();
  });

  it("shows a search-specific empty state and resets it", async () => {
    seed([job("node1.example")]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node1.example")).toBeInTheDocument());

    fireEvent.change(screen.getByRole("searchbox", { name: "Поиск нод" }), { target: { value: "abc" } });

    expect(screen.getByText("Поиск: «abc» — совпадений нет")).toBeInTheDocument();
    expect(screen.queryByText("CARD:node1.example")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Сбросить поиск" }));
    expect(screen.getByText("CARD:node1.example")).toBeInTheDocument();
    expect((screen.getByRole("searchbox", { name: "Поиск нод" }) as HTMLInputElement).value).toBe("");
  });

  it("normal search typing still filters cards (regression)", async () => {
    seed([job("node1.example", "t1"), job("other.example", "t2")]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:other.example")).toBeInTheDocument());

    fireEvent.change(screen.getByRole("searchbox", { name: "Поиск нод" }), { target: { value: "node1" } });

    expect(screen.getByText("CARD:node1.example")).toBeInTheDocument();
    expect(screen.queryByText("CARD:other.example")).not.toBeInTheDocument();
  });

  it("status filters still work (regression)", async () => {
    seed([
      job("node1.example", "t1", { finalStatus: "success" }),
      job("node2.example", "t2", { finalStatus: "failed" }),
    ]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node2.example")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Успех" }));
    expect(screen.getByText("CARD:node1.example")).toBeInTheDocument();
    expect(screen.queryByText("CARD:node2.example")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Ошибка" }));
    expect(screen.queryByText("CARD:node1.example")).not.toBeInTheDocument();
    expect(screen.getByText("CARD:node2.example")).toBeInTheDocument();
  });

  it("shows a status-filter empty state and «Показать все» restores the cards", async () => {
    seed([job("node1.example")]);
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node1.example")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Ошибка" }));

    expect(screen.getByText("Фильтр: «Ошибка» — нет карточек")).toBeInTheDocument();
    expect(screen.queryByText("CARD:node1.example")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Показать все" }));
    expect(screen.getByText("CARD:node1.example")).toBeInTheDocument();
  });

  // ── серверные мутации ──────────────────────────────────────

  it("a mutation (remove) deletes the card on the server, not a full-list PUT", async () => {
    const existing = job("node1.example", "t1");
    seed([existing]);
    const { calls } = stubServer();
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:node1.example")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "remove" }));

    await waitFor(() => {
      const del = calls.find(c => c.method === "DELETE" && c.url.includes("/api/deploy-jobs"));
      expect(del?.url).toContain("t1");
    });
    expect(screen.queryByText("CARD:node1.example")).not.toBeInTheDocument();
    expect(calls.some(c => c.method === "PUT")).toBe(false);
    // и в кэш браузера ничего не уходит
    expect(localStorage.getItem("deploy_jobs_id-a")).toBeNull();
  });

  it("does not resurrect a card whose upsert failed", async () => {
    seed([job("server.example", "t-server")]);
    stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [job("server.example", "t-server")] }) };
      if (method === "POST") throw new Error("offline");
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);
    await waitFor(() => expect(screen.getByText("CARD:server.example")).toBeInTheDocument());

    // мутация, ломающаяся на POST, не должна оставить карточку в списке из кэша
    await expect(
      import("../data/deployJobsStore").then(m => m.saveJob(job("broken.example", "t-broken"))),
    ).rejects.toThrow("offline");
    await waitFor(() => expect(getJobs().map(j => j.taskId)).toEqual(["t-server"]));
  });

  // ── имена нод из Remnawave ─────────────────────────────────

  it("passes the node name from /api/remnawave/nodes into the card", async () => {
    seed([job("node1.example", "t1")]);
    stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/remnawave/nodes")) {
        return { ok: true, json: async () => ([{ uuid: "u1", name: "astra1nl", address: "1.2.3.4" }]) };
      }
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [job("node1.example", "t1")] }) };
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    await waitFor(() => expect(screen.getByTestId("node-name")).toHaveTextContent("astra1nl"));
  });

  it("renders without a node name when /api/remnawave/nodes fails", async () => {
    seed([job("node1.example", "t1")]);
    stubFetch((url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/remnawave/nodes")) throw new Error("no nodes");
      if (url.includes("/api/deploy-jobs") && method === "GET") return { ok: true, json: async () => ({ jobs: [job("node1.example", "t1")] }) };
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
        jobs: [],
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

  // ── Remnawave sync toolbar action (dry run → apply) ─────────

  const syncReport = (dryRun: boolean) => ({
    ok: true,
    dry_run: dryRun,
    added: [{ taskId: "rw-u1", ip: "5.6.7.8", domain: "" }],
    removed: [{ taskId: "dead", ip: "9.9.9.9", domain: "dead.example" }],
    skipped: [{ ip: "172.30.0.1", reason: "private-address" }],
    nodes_total: 3,
    cards_before: 2,
    cards_after: 2,
  });

  it("does not touch the sync endpoint on a plain page load", async () => {
    const { calls } = stubServer();
    render(<DeployDashboard />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));

    expect(calls.some(c => c.url.includes("/sync-remnawave"))).toBe(false);
  });

  it("previews the difference with a dry run and applies only after confirmation", async () => {
    seed([job("live.example", "t-live")]);
    const { calls } = stubFetch((url, init) => {
      if (url.includes("/sync-remnawave")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        return { ok: true, json: async () => syncReport(Boolean(body.dry_run)) };
      }
      if (url.includes("/api/deploy-jobs") && (init?.method ?? "GET") === "GET") {
        return { ok: true, json: async () => ({ jobs: serverJobs }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    fireEvent.click(screen.getByRole("button", { name: /Синхронизировать с Remnawave/ }));

    await screen.findByText(/добавить 1/);
    expect(screen.getByText(/убрать 1/)).toBeInTheDocument();
    expect(screen.getByText(/пропущено 1/)).toBeInTheDocument();
    const syncCalls = () => calls.filter(c => c.url.includes("/sync-remnawave"));
    expect(syncCalls()).toHaveLength(1);
    expect(syncCalls()[0].body).toEqual({ dry_run: true });

    fireEvent.click(screen.getByRole("button", { name: "Применить" }));

    await waitFor(() => expect(syncCalls()).toHaveLength(2));
    expect(syncCalls()[1].body).toEqual({ dry_run: false });
    await waitFor(() => expect(screen.queryByText(/добавить 1/)).not.toBeInTheDocument());
  });

  it("keeps the apply button disabled when there is nothing to do", async () => {
    stubFetch((url, init) => {
      if (url.includes("/sync-remnawave")) {
        return { ok: true, json: async () => ({ ...syncReport(true), added: [], removed: [], skipped: [] }) };
      }
      if (url.includes("/api/deploy-jobs") && (init?.method ?? "GET") === "GET") {
        return { ok: true, json: async () => ({ jobs: [] }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    fireEvent.click(screen.getByRole("button", { name: /Синхронизировать с Remnawave/ }));
    await screen.findByText(/добавить 0/);
    expect(screen.getByRole("button", { name: "Применить" })).toBeDisabled();
  });

  it("surfaces a dry-run failure instead of applying anything", async () => {
    const { calls } = stubFetch((url, init) => {
      if (url.includes("/sync-remnawave")) return { ok: false, status: 400, json: async () => ({}) };
      if (url.includes("/api/deploy-jobs") && (init?.method ?? "GET") === "GET") {
        return { ok: true, json: async () => ({ jobs: [] }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    render(<DeployDashboard />);

    fireEvent.click(screen.getByRole("button", { name: /Синхронизировать с Remnawave/ }));
    await screen.findByText(/sync-remnawave 400/);

    expect(calls.filter(c => c.url.includes("/sync-remnawave"))).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Применить" })).toBeDisabled();
  });
});
