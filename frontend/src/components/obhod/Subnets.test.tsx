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

/** Строки с разными провайдерами (и без него) — для тестов группировки. */
const GROUP_STORE = {
  providers: [{
    id: "p1", name: "МТС",
    lists: [{
      id: "l1", name: "Основной",
      columns: [{ key: "subnet", title: "Подсеть" }],
      rows: [
        { id: "r1", values: { subnet: "10.0.0.0/8", provider: "RUVDS" }, operators: {} },
        { id: "r2", values: { subnet: "11.0.0.0/8", provider: "" }, operators: {} },
        { id: "r3", values: { subnet: "12.0.0.0/8", provider: "Beeline" }, operators: {} },
        { id: "r4", values: { subnet: "13.0.0.0/8", provider: "RUVDS" }, operators: {} },
        { id: "r5", values: { subnet: "14.0.0.0/8", provider: "Аврора" }, operators: {} },
      ],
    }],
  }],
  operators: [],
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

/** `latency` — конфиг интеграции; `job` — очередь ответов GET /latency-scan/{id};
 *  `store` — ответ GET /api/subnets (по умолчанию STORE);
 *  `reqIds` — очередь req_id из POST /latency-scan (по умолчанию ["req-1"]). */
function installFetch(opts?: { latency?: Record<string, unknown>; job?: unknown[]; imp?: unknown; store?: unknown; reqIds?: string[] }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const jobQueue = [...(opts?.job ?? [])];
  const reqIdQueue = [...(opts?.reqIds ?? ["req-1"])];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url === "/api/subnets") return json(opts?.store ?? STORE);
    if (url === "/api/latency/config") return json(opts?.latency ?? { enabled: false, has_key: false });
    if (url === "/api/latency/operators") return json(LAT_OPS);
    if (url === "/api/subnets/latency-scan") {
      const id = reqIdQueue.length > 1 ? reqIdQueue.shift() : reqIdQueue[0];
      return json({ ok: true, req_id: id ?? "req-1", status: "pending" });
    }
    if (url.startsWith("/api/subnets/latency-scan/") && !url.endsWith("/cancel"))
      return json(jobQueue.length > 1 ? jobQueue.shift() : jobQueue[0] ?? { status: "pending" });
    if (url.startsWith("/api/subnets/export")) {
      const fmt = new URLSearchParams(url.split("?")[1] ?? "").get("format") ?? "json";
      return new Response("subnet\n203.0.113.0/24\n", {
        status: 200,
        // только ASCII: заголовки в undici — ByteString, кириллица бросит TypeError
        headers: { "Content-Disposition": `attachment; filename="subnets-l1.${fmt}"` },
      });
    }
    if (url === "/api/subnets/import")
      return json(opts?.imp ?? { ok: true, imported: 2, skipped: 1, errors: [] });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const enabled = { enabled: true, has_key: true, base_url: "", node_id: "orel", default_operator: "" };

/** Список из N строк с уникальными подсетями 10.x.y.0/24 — для порций. */
function mkBigStore(n: number) {
  const rows = Array.from({ length: n }, (_, i) => ({
    id: `r${i + 1}`,
    values: { subnet: `10.${Math.floor(i / 256)}.${i % 256}.0/24` },
    operators: {},
  }));
  return {
    providers: [{ id: "p1", name: "МТС", lists: [{ id: "l1", name: "Основной", columns: [{ key: "subnet", title: "Подсеть" }], rows }] }],
    operators: [],
  };
}

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

  it(">750 строк: порции по 750, индикатор «Порция N/M», результат объединяется", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const big = mkBigStore(1500);
    const calls = installFetch({
      latency: enabled,
      store: big,
      reqIds: ["req-1", "req-2"],
      job: [
        { status: "pending" }, { status: "pending" },
        { status: "done", result: { rows: [{ row_id: "r1", subnet: big.providers[0].lists[0].rows[0].values.subnet, available: true, alive_count: 2 }] } },
        { status: "done", result: { rows: [{ row_id: "r1500", subnet: big.providers[0].lists[0].rows[1499].values.subnet, available: false, alive_count: 0 }] } },
      ],
    });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    // 1500 строк → 2 ПОСЛЕДОВАТЕЛЬНЫХ POST'а ровно по 750 row_id, без all:true
    await waitFor(() => {
      const posts = calls.filter(c => c.url === "/api/subnets/latency-scan");
      expect(posts.length).toBe(2);
      const b1 = JSON.parse(String(posts[0].init!.body));
      const b2 = JSON.parse(String(posts[1].init!.body));
      expect(b1.row_ids).toHaveLength(750);
      expect(b2.row_ids).toHaveLength(750);
      expect(b1.all).toBeUndefined();
      // порции не пересекаются и покрывают все строки
      expect(new Set([...b1.row_ids, ...b2.row_ids]).size).toBe(1500);
    });
    // индикатор: «Порция 1/2» (обе ещё бегут, готова 0)
    expect(screen.getByTestId("latency-progress")).toHaveTextContent("Порция 1/2");
    await vi.advanceTimersByTimeAsync(3500);
    // объединённый результат: строки обеих порций в одном списке
    const panel = await screen.findByTestId("latency-result");
    expect(panel).toHaveTextContent(big.providers[0].lists[0].rows[0].values.subnet);
    expect(panel).toHaveTextContent(big.providers[0].lists[0].rows[1499].values.subnet);
    expect(panel).toHaveTextContent("недоступна");
    // значки строк: галочка у доступной, крест у недоступной
    expect(screen.getByTestId("scan-icon-r1-ok")).toBeInTheDocument();
    expect(screen.getByTestId("scan-icon-r1500-unavailable")).toBeInTheDocument();
  }, 15000); // рендер 1500 строк — даём тесту больше времени под нагрузкой

  it("750 строк и «все» — один POST с all:true (без порций)", async () => {
    const calls = installFetch({ latency: enabled, store: mkBigStore(750) });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    await waitFor(() => {
      const posts = calls.filter(c => c.url === "/api/subnets/latency-scan");
      expect(posts.length).toBe(1);
      expect(JSON.parse(String(posts[0].init!.body))).toEqual({
        provider_id: "p1", list_id: "l1", all: true, async_: true,
      });
    });
  });

  it("значки статуса строк: спиннер пока идёт, галочка после available:true", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetch({ latency: enabled, job: [{ status: "pending" }, SCAN_RESULT] });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    // скан идёт — спиннер слева от подсети
    expect(await screen.findByTestId("scan-icon-r1-running")).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(3500);
    // завершён, available: true — зелёная галочка
    expect(await screen.findByTestId("scan-icon-r1-ok")).toBeInTheDocument();
    expect(screen.queryByTestId("scan-icon-r1-running")).toBeNull();
  });

  it("значок недоступной подсети — красный крест; до скана — пусто; клики целы", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetch({
      latency: enabled,
      job: [{ status: "done", result: { rows: [{ row_id: "r1", subnet: "203.0.113.0/24", available: false, alive_count: 0 }] } }],
    });
    await openList();
    // до скана — пустой плейсхолдер (без значка)
    expect(screen.getByTestId("scan-icon-r1-none")).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    await vi.advanceTimersByTimeAsync(3500);
    // available: false — красный крест
    expect(await screen.findByTestId("scan-icon-r1-unavailable")).toBeInTheDocument();
    // значок не перехватывает клики — чекбокс строки по-прежнему работает
    fireEvent.click(screen.getByTestId("latency-pick-r1"));
    expect(screen.getByTestId("latency-pick-r1")).toBeChecked();
  });

  // ── Импорт/экспорт ───────────────────────────────────────────

  /** jsdom не умеет blob-URL и навигацию по <a> — подменяем обе точки.
   *  click объявлен на HTMLElement.prototype, у HTMLAnchorElement своего нет. */
  function stubDownload() {
    const clicked: { href: string; download: string }[] = [];
    vi.stubGlobal("URL", Object.assign(Object.create(URL), {
      createObjectURL: vi.fn(() => "blob:mock"),
      revokeObjectURL: vi.fn(),
    }));
    const spy = vi.spyOn(HTMLElement.prototype, "click")
      .mockImplementation(function (this: HTMLElement) {
        if (this instanceof HTMLAnchorElement)
          clicked.push({ href: this.href, download: this.download });
      });
    return { clicked, spy };
  }

  async function openIo() {
    await openList();
    fireEvent.click(await screen.findByTestId("subnets-io-toggle"));
    return screen.findByTestId("subnets-io-panel");
  }

  it("кнопка «Импорт/экспорт» открывает панель с выбором формата", async () => {
    installFetch();
    await openIo();
    const fmt = screen.getByTestId("export-format") as HTMLSelectElement;
    expect(fmt.options.length).toBe(4);
    expect([...fmt.options].map(o => o.value)).toEqual(["json", "csv", "txt", "xlsx"]);
    expect(screen.getByTestId("export-run")).toBeInTheDocument();
    expect(screen.getByTestId("import-run")).toBeDisabled(); // файл не выбран
  });

  it("экспорт дёргает /export с provider_id/list_id/format и скачивает файл", async () => {
    const calls = installFetch();
    const { clicked, spy } = stubDownload();
    await openIo();
    fireEvent.change(screen.getByTestId("export-format"), { target: { value: "csv" } });
    fireEvent.click(screen.getByTestId("export-run"));
    await waitFor(() => {
      const c = calls.find(x => x.url.startsWith("/api/subnets/export"));
      expect(c).toBeTruthy();
      const q = new URLSearchParams(c!.url.split("?")[1]);
      expect(q.get("provider_id")).toBe("p1");
      expect(q.get("list_id")).toBe("l1");
      expect(q.get("format")).toBe("csv");
    });
    // имя берётся из Content-Disposition
    await waitFor(() => expect(clicked[0]?.download).toBe("subnets-l1.csv"));
    spy.mockRestore();
  });

  it("экспорт в Excel шлёт format=xlsx (тот же fetch→blob→download)", async () => {
    const calls = installFetch();
    const { clicked, spy } = stubDownload();
    await openIo();
    fireEvent.change(screen.getByTestId("export-format"), { target: { value: "xlsx" } });
    fireEvent.click(screen.getByTestId("export-run"));
    await waitFor(() => {
      const c = calls.find(x => x.url.startsWith("/api/subnets/export"));
      expect(c).toBeTruthy();
      const q = new URLSearchParams(c!.url.split("?")[1]);
      expect(q.get("format")).toBe("xlsx");
    });
    await waitFor(() => expect(clicked[0]?.download).toBe("subnets-l1.xlsx"));
    spy.mockRestore();
  });

  it("импорт шлёт multipart POST с файлом, provider_id/list_id и mode", async () => {
    const calls = installFetch();
    await openIo();
    const file = new File(['{"rows":[]}'], "subnets.json", { type: "application/json" });
    fireEvent.change(screen.getByTestId("import-file"), { target: { files: [file] } });
    fireEvent.change(screen.getByTestId("import-mode"), { target: { value: "replace" } });
    fireEvent.click(screen.getByTestId("import-run"));
    await waitFor(() => {
      const post = calls.find(c => c.url === "/api/subnets/import");
      expect(post).toBeTruthy();
      expect(post!.init!.method).toBe("POST");
      const fd = post!.init!.body as FormData;
      expect(fd).toBeInstanceOf(FormData);
      expect((fd.get("file") as File).name).toBe("subnets.json");
      expect(fd.get("provider_id")).toBe("p1");
      expect(fd.get("list_id")).toBe("l1");
      expect(fd.get("mode")).toBe("replace");
    });
  });

  it("результат импорта виден; «в новый список» убирает list_id", async () => {
    const calls = installFetch({ imp: { ok: true, imported: 5, skipped: 2, errors: [] } });
    await openIo();
    fireEvent.click(screen.getByTestId("import-new-list"));
    const file = new File(["203.0.113.0/24"], "subnets.txt", { type: "text/plain" });
    fireEvent.change(screen.getByTestId("import-file"), { target: { files: [file] } });
    fireEvent.click(screen.getByTestId("import-run"));

    const res = await screen.findByTestId("import-result");
    expect(res).toHaveTextContent("Импортировано 5");
    expect(res).toHaveTextContent("пропущено 2");
    const fd = calls.find(c => c.url === "/api/subnets/import")!.init!.body as FormData;
    expect(fd.get("list_id")).toBeNull();
  });

  // ── UX: скролл таблицы + сворачивание дерева ─────────────────

  it("таблица: скролл-контейнер с внутренним overflow, шапка sticky", async () => {
    installFetch();
    await openList();
    const scroller = screen.getByTestId("subnets-table-scroll");
    expect(scroller).toHaveClass("overflow-auto");
    expect(scroller).toHaveClass("min-h-0");
    const th = document.querySelector("thead th");
    expect(th).toBeTruthy();
    expect(th!.className).toContain("sticky");
  });

  it("кнопка дерева сворачивает и разворачивает панель провайдеров", async () => {
    installFetch();
    await openList();
    // развёрнуто: провайдер виден
    expect(screen.getByText("МТС")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tree-toggle"));
    // свёрнуто: дерево скрыто, таблица осталась
    expect(screen.queryByText("МТС")).toBeNull();
    expect(screen.getByText("203.0.113.0/24")).toBeInTheDocument();
    // разворачиваем обратно
    fireEvent.click(screen.getByTestId("tree-toggle"));
    expect(await screen.findByText("МТС")).toBeInTheDocument();
  });

  // ── Группировка строк по провайдеру ─────────────────────────

  it("toggle «Группировать»: дефолт — плоский список; включение группирует, выключение возвращает", async () => {
    installFetch({ store: GROUP_STORE });
    await openList();
    // дефолт: плоский список, заголовков групп нет
    const toggle = screen.getByTestId("group-toggle");
    expect(toggle).toHaveTextContent("Выкл");
    expect(screen.queryByText(/Без провайдера/)).toBeNull();
    expect(screen.getByText("10.0.0.0/8")).toBeInTheDocument();
    // включаем → появляются заголовки групп, строки на месте
    fireEvent.click(toggle);
    expect(await screen.findByText("RUVDS (2)")).toBeInTheDocument();
    expect(screen.getByText("Без провайдера (1)")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.0/8")).toBeInTheDocument();
    // выключаем → снова плоский список
    fireEvent.click(screen.getByTestId("group-toggle"));
    expect(screen.queryByText("RUVDS (2)")).toBeNull();
    expect(screen.getByText("10.0.0.0/8")).toBeInTheDocument();
  });

  it("заголовки групп: имя провайдера + счётчик, порядок ru, «Без провайдера» последней", async () => {
    installFetch({ store: GROUP_STORE });
    await openList();
    fireEvent.click(screen.getByTestId("group-toggle"));
    const heads = await screen.findAllByTestId(/^subnets-group-/);
    expect(heads.map(h => h.getAttribute("data-testid"))).toEqual([
      "subnets-group-Аврора",
      "subnets-group-Beeline",
      "subnets-group-RUVDS",
      "subnets-group-__none__",
    ]);
    expect(heads[2]).toHaveTextContent("RUVDS (2)");
    expect(heads[3]).toHaveTextContent("Без провайдера (1)");
  });

  it("клик по заголовку сворачивает и разворачивает группу", async () => {
    installFetch({ store: GROUP_STORE });
    await openList();
    fireEvent.click(screen.getByTestId("group-toggle"));
    await screen.findByText("RUVDS (2)");
    // развёрнуто: строки RUVDS видны
    expect(screen.getByText("10.0.0.0/8")).toBeInTheDocument();
    expect(screen.getByText("13.0.0.0/8")).toBeInTheDocument();
    // сворачиваем RUVDS → его строки скрыты, другие группы не тронуты
    fireEvent.click(screen.getByTestId("subnets-group-RUVDS"));
    expect(screen.queryByText("10.0.0.0/8")).toBeNull();
    expect(screen.queryByText("13.0.0.0/8")).toBeNull();
    expect(screen.getByText("11.0.0.0/8")).toBeInTheDocument(); // «Без провайдера»
    expect(screen.getByText("12.0.0.0/8")).toBeInTheDocument(); // Beeline
    // разворачиваем обратно
    fireEvent.click(screen.getByTestId("subnets-group-RUVDS"));
    expect(await screen.findByText("10.0.0.0/8")).toBeInTheDocument();
  });

  it("в режиме правки группировка игнорируется (плоский список)", async () => {
    installFetch({ store: GROUP_STORE });
    await openList();
    fireEvent.click(screen.getByTestId("group-toggle"));
    await screen.findByText("RUVDS (2)");
    // переходим в правку → заголовки групп исчезли, все строки видны
    fireEvent.click(screen.getByTestId("table-edit-toggle"));
    expect(screen.queryByTestId("subnets-group-RUVDS")).toBeNull();
    expect(screen.getByText("10.0.0.0/8")).toBeInTheDocument();
    expect(screen.getByText("13.0.0.0/8")).toBeInTheDocument();
    expect(screen.getByText("11.0.0.0/8")).toBeInTheDocument();
    // выходим из правки → группировка вернулась
    fireEvent.click(screen.getByTestId("table-edit-toggle"));
    expect(await screen.findByText("RUVDS (2)")).toBeInTheDocument();
  });
});
