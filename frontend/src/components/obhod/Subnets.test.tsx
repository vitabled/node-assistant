import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Subnets } from "./Subnets";

// Wave-5 PR-5: «Подсети» — дерево, таблица, режим редактирования.
// Latency Lab: кнопка скана, выбор строк/оператора, поллинг job'а, результат.

const STORE = {
  providers: [{
    id: "p1", name: "МТС",
    lists: [{
      id: "l1", name: "Основной",
      columns: [
        { key: "subnet", title: "Подсеть" }, { key: "ipver", title: "Версия IP" },
        { key: "asn", title: "ASN" }, { key: "asnname", title: "Название ASN" },
        { key: "date", title: "Дата" }, { key: "operators", title: "Операторы" },
      ],
      rows: [{
        id: "r1",
        values: { subnet: "203.0.113.0/24", ipver: "IPv4", asn: "AS123", asnname: "Test", date: "2026-08-14" },
        operators: { mts: true, beeline: true, megafon: true, tele2: true, tmobile: true },
      }],
    }],
  }],
  operators: [
    { key: "mts", label: "MTS" }, { key: "beeline", label: "Beeline" },
    { key: "megafon", label: "МегаФон" }, { key: "tele2", label: "Tele2" },
    { key: "tmobile", label: "T-Mobile" },
  ],
};

const LAT_OPS = {
  operators: [
    { id: "mts", label: "MTS", online: true, configured: true },
    { id: "beeline", label: "Beeline", online: false, configured: true },
    { id: "tele2", label: "Tele2", online: true, configured: false },
  ],
  online: ["mts"],
};

const SCAN_RESULT = {
  status: "done",
  result: {
    rows: [{
      row_id: "r1", subnet: "203.0.113.0/24", operator: "mts",
      alive_count: 3, available: true, status_text: "OK",
      reachable_ips: ["203.0.113.1", "203.0.113.7"],
    }],
  },
};

/** `latency` — конфиг интеграции; `job` — очередь ответов GET /latency-scan/{id}. */
function installFetch(opts?: { latency?: Record<string, unknown>; job?: unknown[] }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const jobQueue = [...(opts?.job ?? [])];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url === "/api/subnets") return json(STORE);
    if (url === "/api/latency/config") return json(opts?.latency ?? { enabled: false, has_key: false });
    if (url === "/api/latency/operators") return json(LAT_OPS);
    if (url === "/api/subnets/latency-scan") return json({ ok: true, req_id: "req-1", status: "pending" });
    if (url.startsWith("/api/subnets/latency-scan/") && !url.endsWith("/cancel"))
      return json(jobQueue.length > 1 ? jobQueue.shift() : jobQueue[0] ?? { status: "pending" });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const enabled = { enabled: true, has_key: true, base_url: "", node_id: "orel", default_operator: "" };

async function openList() {
  render(<Subnets />);
  fireEvent.click(await screen.findByText("Основной"));
}

describe("Subnets", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("таблица рендерится; иконки операторов видны без режима правки", async () => {
    installFetch();
    await openList();
    expect(await screen.findByText("203.0.113.0/24")).toBeInTheDocument();
    expect(screen.getByText("AS123")).toBeInTheDocument();
    const icons = document.querySelectorAll('img[src^="/operators/"]');
    expect(icons.length).toBe(5);
    // чекбоксов нет вне режима правки
    expect(document.querySelectorAll('input[type="checkbox"]').length).toBe(0);
  });

  it("режим редактирования: чекбоксы операторов тогглят иконки", async () => {
    const calls = installFetch();
    await openList();
    fireEvent.click(screen.getByTestId("table-edit-toggle"));
    // в режиме правки — чекбоксы появились
    const boxes = await waitFor(() => {
      const b = document.querySelectorAll('input[type="checkbox"]');
      expect(b.length).toBe(5);
      return b;
    });
    fireEvent.click(boxes[1]); // beeline off
    await waitFor(() => {
      const patch = calls.find(c => c.url.includes("/operator/beeline"));
      expect(patch).toBeTruthy();
      expect(JSON.parse(String(patch!.init!.body))).toEqual({ on: false });
    });
  });

  it("добавление подсети шлёт rows с разбором строк", async () => {
    const calls = installFetch();
    await openList();
    fireEvent.change(screen.getByPlaceholderText(/203\.0\.113\.0\/24/),
      { target: { value: "10.0.0.0/8, 192.168.0.0/16" } });
    fireEvent.click(screen.getByText("Добавить"));
    await waitFor(() => {
      const post = calls.find(c => c.url.includes("/rows"));
      expect(post).toBeTruthy();
      expect(JSON.parse(String(post!.init!.body))).toEqual({
        subnets: ["10.0.0.0/8", "192.168.0.0/16"],
      });
    });
  });

  // ── Latency Lab ──────────────────────────────────────────────

  it("кнопка «Скан Latency» скрыта, пока интеграция выключена", async () => {
    installFetch({ latency: { enabled: false, has_key: false } });
    await openList();
    expect(await screen.findByText("203.0.113.0/24")).toBeInTheDocument();
    expect(screen.queryByTestId("latency-scan-toggle")).toBeNull();
  });

  it("панель скана: операторы подгружаются, чекбоксы строк появляются", async () => {
    installFetch({ latency: enabled });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    expect(await screen.findByTestId("latency-scan-panel")).toBeInTheDocument();
    // dropdown: «все» + 3 оператора; недоступный — disabled
    const opSelect = screen.getByTestId("latency-operator") as HTMLSelectElement;
    await waitFor(() => expect(opSelect.options.length).toBe(4));
    expect(opSelect.options[1].value).toBe("mts");
    expect(opSelect.options[3].disabled).toBe(true);
    // колонка выбора строк
    expect(screen.getByTestId("latency-pick-all")).toBeInTheDocument();
    expect(screen.getByTestId("latency-pick-r1")).toBeInTheDocument();
  });

  it("без выбора строк шлёт all:true, с выбором — row_ids + operator", async () => {
    const calls = installFetch({ latency: enabled });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");

    fireEvent.click(screen.getByTestId("latency-start"));
    await waitFor(() => {
      const post = calls.find(c => c.url === "/api/subnets/latency-scan");
      expect(JSON.parse(String(post!.init!.body))).toEqual({
        provider_id: "p1", list_id: "l1", all: true, async_: true,
      });
    });
    // прогресс виден, пока job pending
    expect(await screen.findByTestId("latency-progress")).toBeInTheDocument();

    // выбираем строку и оператора → второй запрос уже адресный
    fireEvent.click(screen.getByTestId("latency-cancel"));
    fireEvent.click(await screen.findByTestId("latency-pick-r1"));
    fireEvent.change(screen.getByTestId("latency-operator"), { target: { value: "mts" } });
    fireEvent.click(await screen.findByTestId("latency-start"));
    await waitFor(() => {
      const posts = calls.filter(c => c.url === "/api/subnets/latency-scan");
      expect(posts.length).toBe(2);
      expect(JSON.parse(String(posts[1].init!.body))).toEqual({
        provider_id: "p1", list_id: "l1", row_ids: ["r1"], operator: "mts", async_: true,
      });
    });
  });

  it("поллинг job'а доводит до результата: alive_count / доступность / IP", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetch({ latency: enabled, job: [{ status: "pending" }, SCAN_RESULT] });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    await screen.findByTestId("latency-progress");

    await vi.advanceTimersByTimeAsync(3500);
    const panel = await screen.findByTestId("latency-result");
    expect(panel).toHaveTextContent("живых IP: 3");
    expect(panel).toHaveTextContent("доступна");
    expect(panel).toHaveTextContent("203.0.113.1");
  });

  it("отмена шлёт req_id на /latency-scan/cancel", async () => {
    const calls = installFetch({ latency: enabled });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    fireEvent.click(await screen.findByTestId("latency-cancel"));
    await waitFor(() => {
      const c = calls.find(x => x.url.endsWith("/latency-scan/cancel"));
      expect(c).toBeTruthy();
      expect(JSON.parse(String(c!.init!.body))).toEqual({ req_id: "req-1" });
    });
    expect(screen.getByText("Отменено")).toBeInTheDocument();
  });
});
