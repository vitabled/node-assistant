import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deployJobsKey } from "../auth/store";
import { RknScanners } from "./RknScanners";

/**
 * Раздел «RKNscanner»: таблица найденных сканеров + центральный сбор со всех нод.
 *
 * Сеть подменяется целиком: проверяем и рендер, и ТОЧНЫЕ url/тела запросов.
 * Ответ `/sync` читается в ДВУХ формах — словесный контракт раздела
 * (`collected`/`new`/`applied`/`entries`) и фактическая реализация backend'а
 * (`results`/`merged`/`total`, список перечитывается отдельным GET).
 */

interface Call { url: string; method: string; body: Record<string, unknown> | null }

const ENTRIES = [
  {
    ip: "203.0.113.10", firstSeen: "2026-09-01T00:00:00Z", lastSeen: "2026-09-23T10:00:00Z",
    hits: 5, nodes: ["10.0.0.1"], port: 443, chain: "TSPUIPS", source: "rkn-watcher",
  },
  {
    ip: "198.51.100.7", firstSeen: "2026-08-20T00:00:00Z", lastSeen: "2026-09-10T10:00:00Z",
    hits: 12, nodes: ["10.0.0.2"], chain: "GOVIPS",
  },
];

const NODE_CARD = {
  taskId: "t1", domain: "node1.example", ip: "10.0.0.1",
  savedForm: {
    ssh_user: "root", ssh_password: "pw",
    current_ssh_port: "22", new_ssh_port: "2222", change_ssh_port: true,
    country_code: "nl",
  },
  finalStatus: "success",
};

let calls: Call[] = [];
let entriesResponse: unknown = ENTRIES;
let syncResponse: Record<string, unknown> = {};

function res(obj: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => obj } as unknown as Response;
}

beforeEach(() => {
  calls = [];
  entriesResponse = ENTRIES;
  // Контракт раздела: собрано/новых + применённые адреса + готовый список.
  syncResponse = {
    collected: 3, new: 1,
    applied: [{ ip: "10.0.0.1", ok: true, detail: "210 правил" }],
    entries: [...ENTRIES, { ip: "203.0.113.77", lastSeen: "2026-09-23T11:00:00Z", hits: 1, nodes: ["10.0.0.1"] }],
  };
  localStorage.clear();
  localStorage.setItem(deployJobsKey(), JSON.stringify([NODE_CARD]));

  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ url, method, body });

    if (url === "/api/deploy-jobs") return res({ jobs: [] });
    if (url === "/api/rkn-scanners" && method === "GET") {
      return res({
        entries: entriesResponse,
        total: Array.isArray(entriesResponse) ? entriesResponse.length : 0,
        updatedAt: "2026-09-23T12:00:00Z",
      });
    }
    if (url === "/api/rkn-scanners" && method === "DELETE") return res({ ok: true, entries: [], total: 0 });
    if (url === "/api/rkn-scanners/save") {
      const entries = (body?.entries ?? []) as unknown[];
      return res({ ok: true, entries, total: entries.length, updatedAt: "2026-09-23T12:00:00Z" });
    }
    if (url === "/api/rkn-scanners/sync") return res(syncResponse);
    throw new Error(`unexpected request: ${method} ${url}`);
  }));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const fetches = (url: string, method = "GET") =>
  calls.filter(c => c.url === url && c.method === method);
const rowIps = () =>
  Array.from(document.querySelectorAll("tbody tr"))
    .map(tr => (tr.querySelector("td")?.textContent ?? "").trim())
    .filter(Boolean);

describe("RknScanners", () => {
  it("renders the scanner table, counter, search and the four action buttons", async () => {
    render(<RknScanners />);

    // Таблица строится из GET /api/rkn-scanners.
    expect(await screen.findByText("203.0.113.10")).toBeInTheDocument();
    expect(screen.getByText("198.51.100.7")).toBeInTheDocument();
    expect(screen.getByTestId("rkn-table")).toBeInTheDocument();

    // Счётчик сканеров + поиск + сортировки.
    expect(screen.getByText("2 сканера")).toBeInTheDocument();
    expect(screen.getByLabelText("Поиск по сканерам")).toBeInTheDocument();
    expect(screen.getByTitle("Сортировать по последнему появлению")).toBeInTheDocument();
    expect(screen.getByTitle("Сортировать по числу попаданий")).toBeInTheDocument();

    // Кнопки центрального сбора/рассылки/сохранения/очистки.
    expect(screen.getByRole("button", { name: /Собрать со всех нод/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Собрать и разослать блок-лист/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Сохранить вручную/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Очистить/ })).toBeInTheDocument();

    // Ноды берутся из сохранённых карточек деплоя (тот же источник, что у F2bList).
    expect(screen.getByText("Ноды для сбора (1)")).toBeInTheDocument();
    expect(screen.getByText(/node1\.example/)).toBeInTheDocument();
  });

  it("shows the empty state when the central list has no scanners", async () => {
    entriesResponse = [];
    render(<RknScanners />);

    expect(await screen.findByText("Сканеры не найдены")).toBeInTheDocument();
    expect(screen.queryByTestId("rkn-table")).toBeNull();
    // «Очистить» нечего — кнопка недоступна.
    expect(screen.getByRole("button", { name: /Очистить/ })).toBeDisabled();
  });

  it("filters rows by search and sorts by hits on demand", async () => {
    render(<RknScanners />);
    await screen.findByText("203.0.113.10");

    // Дефолт — по lastSeen desc: свежайший сканер первым.
    expect(rowIps()[0]).toBe("203.0.113.10");

    // Сортировка по попаданиям (desc → asc по повторному клику).
    fireEvent.click(screen.getByTitle("Сортировать по числу попаданий"));
    expect(rowIps()).toEqual(["198.51.100.7", "203.0.113.10"]);
    fireEvent.click(screen.getByTitle("Сортировать по числу попаданий"));
    expect(rowIps()[0]).toBe("203.0.113.10");

    // Поиск по IP/ноде/цепочке.
    fireEvent.change(screen.getByLabelText("Поиск по сканерам"), { target: { value: "GOVIPS" } });
    expect(rowIps()).toEqual(["198.51.100.7"]);
    expect(screen.getByText("1 из 2")).toBeInTheDocument();
  });

  it("«Собрать со всех нод» posts sync with apply=false and the saved node creds", async () => {
    render(<RknScanners />);
    await screen.findByText("203.0.113.10");

    fireEvent.click(screen.getByRole("button", { name: /Собрать со всех нод/ }));

    await waitFor(() => expect(fetches("/api/rkn-scanners/sync", "POST")).toHaveLength(1));
    const body = fetches("/api/rkn-scanners/sync", "POST")[0].body as {
      apply: boolean; nodes: Record<string, unknown>[];
    };
    expect(body.apply).toBe(false);
    expect(body.nodes).toHaveLength(1);
    expect(body.nodes[0]).toMatchObject({
      ip: "10.0.0.1", ssh_port: 2222, ssh_user: "root", ssh_password: "pw",
      since_hours: 24, sinceHours: 24, apply: false,
    });

    // Ответ sync заменяет таблицу (новый сканер виден).
    expect(await screen.findByText("203.0.113.77")).toBeInTheDocument();
  });

  it("«Собрать и разослать блок-лист» waits for confirmation and then sends apply=true", async () => {
    render(<RknScanners />);
    await screen.findByText("203.0.113.10");

    fireEvent.click(screen.getByRole("button", { name: /Собрать и разослать блок-лист/ }));
    // Первое нажатие — только подтверждение, сети ещё нет.
    expect(await screen.findByRole("button", { name: /Подтвердить рассылку/ })).toBeInTheDocument();
    expect(fetches("/api/rkn-scanners/sync", "POST")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /Подтвердить рассылку/ }));

    await waitFor(() => expect(fetches("/api/rkn-scanners/sync", "POST")).toHaveLength(1));
    const body = fetches("/api/rkn-scanners/sync", "POST")[0].body as {
      apply: boolean; nodes: Record<string, unknown>[];
    };
    expect(body.apply).toBe(true);          // словесный контракт
    expect(body.nodes[0].apply).toBe(true); // фактическая реализация (флаг на ноде)

    // Отчёт по нодам из ответа (applied[{ip, ok, detail}]).
    expect(await screen.findByText("210 правил")).toBeInTheDocument();
    expect(screen.getByText(/разослано на 1 ноду/)).toBeInTheDocument();
  });

  it("reads the real backend sync reply (results/merged) and refreshes the list", async () => {
    // Фактическая форма ответа: без entries — список перечитывается отдельным GET.
    syncResponse = {
      results: [
        { ip: "10.0.0.1", ok: true, collected: 3, hits: 5, merged: { added: 1, updated: 0 } },
        { ip: "10.0.0.2", ok: false, error: "ssh timeout" },
      ],
      total: 3, updatedAt: "2026-09-23T12:00:00Z", merged: { added: 1, updated: 0, hitsAdded: 5 },
    };
    entriesResponse = [...ENTRIES, { ip: "203.0.113.77", lastSeen: "2026-09-23T11:00:00Z", hits: 1, nodes: ["10.0.0.1"] }];

    render(<RknScanners />);
    await screen.findByText("203.0.113.10");
    expect(fetches("/api/rkn-scanners", "GET")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: /Собрать со всех нод/ }));

    // Отчёт по нодам + новый сканер, подтянутый повторным GET.
    expect(await screen.findByText(/собрано 3 · попаданий 5/)).toBeInTheDocument();
    expect(screen.getByText("ssh timeout")).toBeInTheDocument();
    expect(await screen.findByText("203.0.113.77")).toBeInTheDocument();
    expect(fetches("/api/rkn-scanners", "GET")).toHaveLength(2);
  });

  it("«Сохранить вручную» adds the typed IP and posts the whole list to /save", async () => {
    render(<RknScanners />);
    await screen.findByText("203.0.113.10");

    fireEvent.change(screen.getByLabelText("IP вручную"), { target: { value: "203.0.113.99" } });
    fireEvent.change(screen.getByLabelText("Порт вручную"), { target: { value: "8443" } });
    fireEvent.change(screen.getByLabelText("Цепочка вручную"), { target: { value: "EXTRA_ANTISCAN" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить вручную/ }));

    await waitFor(() => expect(fetches("/api/rkn-scanners/save", "POST")).toHaveLength(1));
    const entries = (fetches("/api/rkn-scanners/save", "POST")[0].body as {
      entries: Record<string, unknown>[];
    }).entries;
    expect(entries).toHaveLength(3);
    expect(entries[2]).toMatchObject({ ip: "203.0.113.99", port: 8443, chain: "EXTRA_ANTISCAN", source: "manual" });

    // После ответа ручная запись видна в таблице, поля очищены.
    expect(await screen.findByText("203.0.113.99")).toBeInTheDocument();
    expect((screen.getByLabelText("IP вручную") as HTMLInputElement).value).toBe("");
  });

  it("«Очистить» needs the second confirmation and then sends DELETE", async () => {
    render(<RknScanners />);
    await screen.findByText("203.0.113.10");

    fireEvent.click(screen.getByRole("button", { name: /Очистить/ }));
    // Первое нажатие — только вопрос, DELETE не ушёл.
    expect(await screen.findByRole("button", { name: /Да, очистить полностью/ })).toBeInTheDocument();
    expect(fetches("/api/rkn-scanners", "DELETE")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /Да, очистить полностью/ }));

    await waitFor(() => expect(fetches("/api/rkn-scanners", "DELETE")).toHaveLength(1));
    expect(await screen.findByText("Сканеры не найдены")).toBeInTheDocument();
  });
});
