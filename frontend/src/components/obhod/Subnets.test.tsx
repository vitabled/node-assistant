import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { aggregateSubnet, Subnets } from "./Subnets";

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
      columns: [{ key: "subnet", title: "Подсеть" }, { key: "provider", title: "Провайдер" }],
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

/** Строки с повторяющимися и пустыми значениями по колонкам — для теста
 *  раскраски по значению выбранного столбца (color-column-select). */
const COLORS_STORE = {
  providers: [{
    id: "p1", name: "МТС",
    lists: [{
      id: "l1", name: "Основной",
      columns: [
        { key: "subnet", title: "Подсеть" },
        { key: "provider", title: "Провайдер" },
        { key: "asn_type", title: "Тип ASN" },
      ],
      rows: [
        { id: "r1", values: { subnet: "10.0.0.0/8", provider: "RUVDS", asn_type: "hosting" }, operators: {} },
        { id: "r2", values: { subnet: "11.0.0.0/8", provider: "Beeline", asn_type: "isp" }, operators: {} },
        { id: "r3", values: { subnet: "12.0.0.0/8", provider: "RUVDS", asn_type: "business" }, operators: {} },
        { id: "r4", values: { subnet: "13.0.0.0/8", provider: "", asn_type: "" }, operators: {} },
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

/** Список с колонкой asn_type — чипы ISP/Hosting/Business. */
const TYPES_STORE = {
  providers: [{
    id: "p1", name: "МТС",
    lists: [{
      id: "l1", name: "Основной",
      columns: [{ key: "subnet", title: "Подсеть" }, { key: "asn_type", title: "Тип ASN" }],
      rows: [
        { id: "r1", values: { subnet: "10.0.0.0/8", asn_type: "hosting" }, operators: {} },
        { id: "r2", values: { subnet: "11.0.0.0/8", asn_type: "isp" }, operators: {} },
        { id: "r3", values: { subnet: "12.0.0.0/8", asn_type: "business" }, operators: {} },
        { id: "r4", values: { subnet: "13.0.0.0/8" }, operators: {} },
      ],
    }],
  }],
  operators: [],
};

/** Провайдер с иконкой в данных — фронт иконки файлов больше НЕ показывает
 *  (иконки задаются у записей ASN). Сторидж для проверки «иконки убраны». */
const ICON_STORE = {
  providers: [{
    id: "p1", name: "RUVDS", icon: "p1.png",
    lists: [{
      id: "l1", name: "Основной",
      columns: [{ key: "subnet", title: "Подсеть" }],
      rows: [
        { id: "r1", values: { subnet: "10.0.0.0/8", provider: "RUVDS" }, operators: {} },
        { id: "r2", values: { subnet: "11.0.0.0/8", provider: "" }, operators: {} },
      ],
    }],
  }],
  operators: [],
};

/** `latency` — конфиг интеграции; `job` — очередь ответов GET /latency-scan/{id};
 *  `store` — ответ GET /api/subnets (по умолчанию STORE);
 *  `enrich` — ответ POST …/enrich-missing; `enrichTypes` — POST …/enrich-types;
 *  `reqIds` — очередь req_id из POST /latency-scan (по умолчанию ["req-1"]);
 *  `asns` — стартовый справочник ASN: mock ДЕРЖИТ его состояние (POST
 *  добавляет/обновляет, DELETE удаляет, POST /asns/{asn}/icon кладёт icon,
 *  GET отдаёт текущий список). */
function installFetch(opts?: { latency?: Record<string, unknown>; job?: unknown[]; imp?: unknown; store?: unknown; reqIds?: string[]; enrich?: unknown; enrichTypes?: unknown; asns?: { asn: string; name: string; note?: string; icon?: string; netname?: string; country?: string; asn_type?: string; category?: string }[] }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const jobQueue = [...(opts?.job ?? [])];
  const reqIdQueue = [...(opts?.reqIds ?? ["req-1"])];
  const asnList = [...(opts?.asns ?? [])];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url === "/api/subnets") return json(opts?.store ?? STORE);
    if (url === "/api/subnets/asns") {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        // как backend: asn нормализуется в «AS» + цифры («99999» → «AS99999»)
        body.asn = "AS" + String(body.asn ?? "").replace(/\D/g, "");
        const i = asnList.findIndex(x => x.asn === body.asn);
        if (i >= 0) asnList[i] = { ...asnList[i], ...body };
        else asnList.push({ asn: body.asn, name: body.name ?? "", note: body.note ?? "", icon: "" });
        return json({ ok: true, updated_rows: 1 });
      }
      return json({ ok: true, asns: asnList });
    }
    if (url === "/api/subnets/asns/sync") {
      // как backend: все уникальные asn из строк store → в asnList (добавить
      // отсутствующие даже без названия, заполнить пустые name/category, не
      // перезаписывать непустые; мусорный asn пропустить)
      const st = (opts?.store ?? STORE) as {
        providers?: { lists?: { rows?: { values?: Record<string, string> }[] }[] }[];
      };
      const pairs = new Map<string, { name: string; category: string }>();
      for (const p of st.providers ?? [])
        for (const l of p.lists ?? [])
          for (const r of l.rows ?? []) {
            const asn = String(r.values?.asn ?? "").trim();
            const m = /(\d+)/.exec(asn);
            if (!m) continue;
            // как backend: name собирается из values.provider (не asnname)
            const name = String(r.values?.provider ?? "").trim();
            const category = String(r.values?.category ?? "").trim();
            const key = "AS" + m[1];
            if (!pairs.has(key)) pairs.set(key, { name, category });
          }
      let added = 0, filled = 0;
      for (const [key, meta] of pairs) {
        const i = asnList.findIndex(x => x.asn === key);
        if (i < 0) {
          asnList.push({ asn: key, name: meta.name, note: "", icon: "", category: meta.category });
          added++;
        } else {
          const cur = asnList[i];
          const upd: Record<string, string> = {};
          if (meta.name && !(cur.name ?? "").trim()) upd.name = meta.name;
          if (meta.category && !(cur.category ?? "").trim()) upd.category = meta.category;
          if (Object.keys(upd).length) { asnList[i] = { ...cur, ...upd }; filled++; }
        }
      }
      return json({ ok: true, added, filled, total: asnList.length });
    }
    if (url === "/api/subnets/asns/apply") {
      // как backend: каждая запись справочника с name/netname/category
      // перезаписывает provider/netname/category у строк store с этим ASN
      // (справочник авторитетнее)
      const st = (opts?.store ?? STORE) as {
        providers?: { lists?: { rows?: { values?: Record<string, string> }[] }[] }[];
      };
      const byAsn = new Map(asnList.map(x => [x.asn, x]));
      let updated = 0;
      for (const p of st.providers ?? [])
        for (const l of p.lists ?? [])
          for (const r of l.rows ?? []) {
            const rec = byAsn.get(String(r.values?.asn ?? "").trim());
            if (!rec) continue;
            const name = String(rec.name ?? "").trim();
            const netname = String(rec.netname ?? "").trim();
            const category = String(rec.category ?? "").trim();
            if (!name && !netname && !category) continue;
            if (name && r.values) r.values.provider = name;
            if (netname && r.values) r.values.netname = netname;
            if (category && r.values) r.values.category = category;
            updated++;
          }
      return json({ ok: true, updated_rows: updated });
    }
    if (url === "/api/subnets/asns/enrich-types") {
      // как backend: тип каждой записи справочника — эвристика по
      // name/netname/note (hosting → isp → business, регистр/дефисы не
      // важны); пустые asn_type заполняются, отличающиеся перезаписываются,
      // записи без данных не трогаются
      const hosting = ["hosting", "cloud", "data center", "datacenter", "server",
        "vps", "vds", "dedicated", "selectel", "timeweb", "hetzner", "digitalocean",
        "aws", "azure", "облако", "хостинг"];
      const isp = ["telecom", "связь", "интернет", "сети", "isp", "operator",
        "ростелеком", "мтс", "билайн", "мегафон", "телеком", "provider", "lte"];
      const norm = (s: unknown) => String(s ?? "").toLowerCase().replace(/-/g, " ");
      let updated = 0;
      for (const rec of asnList) {
        const text = [rec.name, rec.netname, rec.note].map(norm).join(" ");
        if (!text.trim()) continue;
        let t = "business";
        if (hosting.some(w => text.includes(w))) t = "hosting";
        else if (isp.some(w => text.includes(w))) t = "isp";
        if ((rec.asn_type ?? "").trim() === t) continue;
        rec.asn_type = t;
        updated++;
      }
      return json({ ok: true, updated, of: asnList.length });
    }
    if (url.startsWith("/api/subnets/asns/")) {
      const parts = url.split("/").filter(Boolean); // [..., "asns", "AS12345"] или [..., "AS12345", "icon"]
      const tail = decodeURIComponent(parts[parts.length - 1]);
      if (tail === "icon") {
        // иконка записи: POST кладёт icon в запись справочника (GET — файл;
        // jsdom картинки не грузит, но ответ нужен на случай прямого fetch)
        const asn = decodeURIComponent(parts[parts.length - 2] ?? "");
        if (init?.method === "POST") {
          const i = asnList.findIndex(x => x.asn === asn);
          if (i >= 0) asnList[i] = { ...asnList[i], icon: `asn_${asn}.png` };
          return json({ ok: true });
        }
        return new Response("png-bytes", { status: 200, headers: { "Content-Type": "image/png" } });
      }
      const i = asnList.findIndex(x => x.asn === tail);
      if (i >= 0) asnList.splice(i, 1);
      return json({ ok: true });
    }
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
    if (url.endsWith("/enrich-missing"))
      return json(opts?.enrich ?? { updated: 0, of: 0, skipped: 0 });
    if (url.endsWith("/enrich-types"))
      return json(opts?.enrichTypes ?? { updated: 0, of: 0 });
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

describe("aggregateSubnet", () => {
  it("минимальная CIDR-подсеть по живым IP (первый отличающийся бит)", () => {
    // 10.0.0.1..10.0.0.10: отличается 4-й октет (биты 0-3) → /28
    expect(aggregateSubnet(["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4",
      "10.0.0.5", "10.0.0.6", "10.0.0.7", "10.0.0.8", "10.0.0.9", "10.0.0.10"]))
      .toBe("10.0.0.0/28");
    // разные третий октет → /23
    expect(aggregateSubnet(["10.0.0.1", "10.0.1.1"])).toBe("10.0.0.0/23");
    // жива только половина /24 → /25 вместо /24
    expect(aggregateSubnet(["91.109.200.1", "91.109.200.2", "91.109.200.126"]))
      .toBe("91.109.200.0/25");
    expect(aggregateSubnet(["203.0.113.1", "203.0.113.7"])).toBe("203.0.113.0/29");
    // один IP — сам как /32
    expect(aggregateSubnet(["10.0.0.5"])).toBe("10.0.0.5/32");
  });

  it("пусто / IPv6 / смесь версий / мусор → null", () => {
    expect(aggregateSubnet([])).toBeNull();
    expect(aggregateSubnet(["2001:db8::1", "2001:db8::2"])).toBeNull();
    expect(aggregateSubnet(["10.0.0.1", "2001:db8::1"])).toBeNull();
    expect(aggregateSubnet(["не-ip"])).toBeNull();
    expect(aggregateSubnet(["10.0.0.300"])).toBeNull();
  });
});

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
    fireEvent.click(screen.getByTestId("subnets-add-rows"));
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

  it("результат скана: живые IP → минимальная подсеть (/29 из /24), точечный IP — отдельной записью с chip /32", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetch({
      latency: enabled,
      job: [{
        status: "done",
        result: { rows: [
          { row_id: "r1", subnet: "203.0.113.0/24", operator: "mts", alive_count: 2, available: true,
            reachable_ips: ["203.0.113.1", "203.0.113.7"] },
          // точечный IP: subnet без маски — запись host'а, не сеть
          { row_id: "r9", subnet: "195.239.193.161", alive_count: 1, available: true,
            reachable_ips: ["195.239.193.161"] },
        ]},
      }],
    });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    await vi.advanceTimersByTimeAsync(3500);
    const panel = await screen.findByTestId("latency-result");
    // сеть: вместо /24 показывается агрегированная /29 + подпись «из /24»
    expect(panel).toHaveTextContent("203.0.113.0/29");
    expect(screen.getByTestId("scan-agg-r1")).toHaveTextContent("из 203.0.113.0/24");
    // точечный IP — отдельная запись с чипом /32
    expect(panel).toHaveTextContent("195.239.193.161");
    expect(screen.getByTestId("scan-host-r9")).toHaveTextContent("/32");
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

  it("после отмены новые выбранные строки без значка ошибки (состав скана фиксируется на старте)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = installFetch({ latency: enabled, store: GROUP_STORE });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    // выбираем r1 и запускаем скан
    fireEvent.click(screen.getByTestId("latency-pick-r1"));
    fireEvent.click(screen.getByTestId("latency-start"));
    await waitFor(() => {
      const posts = calls.filter(c => c.url === "/api/subnets/latency-scan");
      expect(posts.length).toBe(1);
    });
    // отменяем
    fireEvent.click(await screen.findByTestId("latency-cancel"));
    expect(await screen.findByText("Отменено")).toBeInTheDocument();
    // выбираем ДРУГУЮ строку — она вне скана, значка нет (не error!)
    fireEvent.click(screen.getByTestId("latency-pick-r2"));
    expect(screen.getByTestId("scan-icon-r2-none")).toBeInTheDocument();
    expect(screen.queryByTestId("scan-icon-r2-error")).toBeNull();
    // и у сканированной r1 после отмены — сброс в none, а не error
    expect(screen.getByTestId("scan-icon-r1-none")).toBeInTheDocument();
    expect(screen.queryByTestId("scan-icon-r1-error")).toBeNull();
  });

  it("статус-синоним Latency Lab «success» нормализуется в done — скан не виснет вечно", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetch({
      latency: enabled,
      job: [{ status: "success", result: { rows: [{ row_id: "r1", subnet: "203.0.113.0/24", available: true, alive_count: 2 }] } }],
    });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    await vi.advanceTimersByTimeAsync(3500);
    // «success» → done: результат показан, спиннер ушёл, статус завершён
    const panel = await screen.findByTestId("latency-result");
    expect(panel).toHaveTextContent("доступна");
    expect(screen.getByTestId("scan-icon-r1-ok")).toBeInTheDocument();
    expect(screen.queryByTestId("latency-progress")).toBeNull();
  });

  it("статус-синоним Latency Lab «failed» нормализуется в error — скан падает, а не виснет", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetch({ latency: enabled, job: [{ status: "failed" }] });
    await openList();
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    fireEvent.click(screen.getByTestId("latency-start"));
    await vi.advanceTimersByTimeAsync(3500);
    expect(screen.getByText("Ошибка")).toBeInTheDocument();
    expect(screen.getByTestId("scan-icon-r1-error")).toBeInTheDocument();
  });

  it("группировка + скан: чекбокс в заголовке группы выбирает все её строки, не сворачивая группу", async () => {
    installFetch({ latency: enabled, store: GROUP_STORE });
    await openList();
    fireEvent.click(screen.getByTestId("group-toggle"));
    await screen.findByText("RUVDS (2)");
    fireEvent.click(await screen.findByTestId("latency-scan-toggle"));
    await screen.findByTestId("latency-scan-panel");
    const cb = screen.getByTestId("latency-pick-group-RUVDS") as HTMLInputElement;
    expect(cb).toBeInTheDocument();
    expect(cb.checked).toBe(false);
    // клик по чекбоксу → выбраны обе строки группы, группа НЕ свернулась
    fireEvent.click(cb);
    expect(screen.getByTestId("latency-pick-r1")).toBeChecked();
    expect(screen.getByTestId("latency-pick-r4")).toBeChecked();
    expect(screen.getByText("10.0.0.0/8")).toBeInTheDocument(); // группа раскрыта
    // снимаем одну строку → чекбокс группы в indeterminate
    fireEvent.click(screen.getByTestId("latency-pick-r1"));
    const cb2 = screen.getByTestId("latency-pick-group-RUVDS") as HTMLInputElement;
    expect(cb2.indeterminate).toBe(true);
    expect(cb2.checked).toBe(false);
    // из indeterminate клик выбирает ВСЕ строки группы (стандарт три-стейта)…
    fireEvent.click(screen.getByTestId("latency-pick-group-RUVDS"));
    expect(screen.getByTestId("latency-pick-r1")).toBeChecked();
    expect(screen.getByTestId("latency-pick-r4")).toBeChecked();
    // …а из полностью выбранного — снимает все
    fireEvent.click(screen.getByTestId("latency-pick-group-RUVDS"));
    expect(screen.getByTestId("latency-pick-r1")).not.toBeChecked();
    expect(screen.getByTestId("latency-pick-r4")).not.toBeChecked();
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

  it("«Разметить провайдеров» зовёт enrich-missing и показывает итог", async () => {
    const calls = installFetch({ enrich: { updated: 3, of: 5, skipped: 2 } });
    await openIo();
    fireEvent.click(screen.getByTestId("enrich-missing-run"));
    await waitFor(() => {
      const post = calls.find(c => c.url.endsWith("/enrich-missing"));
      expect(post).toBeTruthy();
      expect(post!.init!.method).toBe("POST");
      expect(JSON.parse(String(post!.init!.body))).toEqual({});
    });
    const chip = await screen.findByTestId("enrich-missing-result");
    expect(chip).toHaveTextContent("Обновлено: 3 из 5");
    expect(chip).toHaveTextContent("пропущено 2");
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

  // ── Тип ASN (колонка asn_type, кнопка «Типы ASN») ───────────

  it("колонка asn_type рендерит чипы ISP/Hosting/Business с цветами; пусто — «—»", async () => {
    installFetch({ store: TYPES_STORE });
    await openList();
    const chip = (id: string) => screen.getByTestId(`asn-type-${id}`);
    expect(chip("r1")).toHaveTextContent("Hosting");
    expect(chip("r2")).toHaveTextContent("ISP");
    expect(chip("r3")).toHaveTextContent("Business");
    expect(chip("r1").className).toContain("chip ok");      // hosting — зелёный
    expect(chip("r2").className).toContain("chip accent");  // isp — синий
    expect(chip("r3").className).toContain("chip warn");    // business — янтарный
    // без значения — прочерк, без чипа
    expect(screen.getByText("13.0.0.0/8").closest("tr")!.textContent).toContain("—");
    expect(screen.queryByTestId("asn-type-r4")).toBeNull();
  });

  it("«Типы ASN» зовёт enrich-types (все строки) и показывает итог", async () => {
    const calls = installFetch({ enrichTypes: { updated: 3, of: 5 } });
    await openIo();
    fireEvent.click(screen.getByTestId("enrich-types-run"));
    await waitFor(() => {
      const post = calls.find(c => c.url.endsWith("/enrich-types"));
      expect(post).toBeTruthy();
      expect(post!.init!.method).toBe("POST");
      expect(JSON.parse(String(post!.init!.body))).toEqual({});
    });
    const chip = await screen.findByTestId("enrich-types-result");
    expect(chip).toHaveTextContent("Типы: 3 из 5");
  });

  // ── Цвет по столбцу (color-column-select): раскраска по значению ──

  it("color-column-select: варианты «Выкл» + колонки списка; дефолт — выкл; выбор столбца раскрашивает строки по значению (одинаковые — один цвет, разные — разные), пустые значения и «Выкл» — без фона", async () => {
    installFetch({ store: COLORS_STORE });
    await openList();
    const sel = screen.getByTestId("color-column-select") as HTMLSelectElement;
    // варианты: «Выкл» (пустое значение) + колонки списка по ключу
    expect([...sel.options].map(o => o.value)).toEqual(["", "subnet", "provider", "asn_type"]);
    expect(sel.value).toBe(""); // дефолт — выкл
    const bg = (id: string) => screen.getByTestId(`subnets-row-${id}`).style.backgroundColor;
    // выкл — фона нет
    expect(bg("r1")).toBe("");
    // выбор provider → одинаковые значения — один цвет, разные — разные
    fireEvent.change(sel, { target: { value: "provider" } });
    expect(bg("r1")).toMatch(/^rgba\(/);
    expect(bg("r3")).toBe(bg("r1")); // RUVDS — тот же цвет
    expect(bg("r2")).toMatch(/^rgba\(/);
    expect(bg("r2")).not.toBe(bg("r1")); // Beeline — другой цвет
    expect(bg("r4")).toBe(""); // пустое значение — без фона
    // выбор asn_type — те же правила по своим значениям
    fireEvent.change(sel, { target: { value: "asn_type" } });
    expect(bg("r1")).toMatch(/^rgba\(/);
    expect(bg("r2")).toMatch(/^rgba\(/);
    expect(bg("r3")).toMatch(/^rgba\(/);
    expect(bg("r3")).not.toBe(bg("r1")); // hosting vs business — разные
    // «Выкл» → фона нет вообще
    fireEvent.change(sel, { target: { value: "" } });
    expect(bg("r1")).toBe("");
    expect(bg("r2")).toBe("");
    expect(bg("r3")).toBe("");
  });

  it("цвет по столбцу + группировка: заголовок группы — цвет первого значения, строки — по своим значениям; «Без провайдера» и правка — без фона", async () => {
    installFetch({ store: GROUP_STORE });
    await openList();
    // дефолт «Выкл»: группировка есть, но фона нет
    fireEvent.click(screen.getByTestId("group-toggle"));
    await screen.findByText("RUVDS (2)");
    expect(screen.getByTestId("subnets-group-RUVDS").style.backgroundColor).toBe("");
    expect(screen.getByTestId("subnets-row-r1").style.backgroundColor).toBe("");
    // выбираем provider → заголовок RUVDS цветной (по первой строке r1),
    // строки RUVDS (r1, r4) — одним цветом
    fireEvent.change(screen.getByTestId("color-column-select"), { target: { value: "provider" } });
    expect(screen.getByTestId("subnets-group-RUVDS").style.backgroundColor).toMatch(/^rgba\(/);
    expect(screen.getByTestId("subnets-row-r1").style.backgroundColor).toMatch(/^rgba\(/);
    expect(screen.getByTestId("subnets-row-r4").style.backgroundColor)
      .toBe(screen.getByTestId("subnets-row-r1").style.backgroundColor);
    // «Без провайдера» — пустое значение → заголовок и строки без фона
    expect(screen.getByTestId("subnets-group-__none__").style.backgroundColor).toBe("");
    expect(screen.getByTestId("subnets-row-r2").style.backgroundColor).toBe("");
    // выключаем группировку → строки всё равно цветные (плоский список)
    fireEvent.click(screen.getByTestId("group-toggle"));
    expect(screen.getByTestId("subnets-row-r1").style.backgroundColor).toMatch(/^rgba\(/);
    // в режиме правки — цвета гаснут
    fireEvent.click(screen.getByTestId("table-edit-toggle"));
    expect(screen.getByTestId("subnets-row-r1").style.backgroundColor).toBe("");
  });

  it("заголовки групп — акцентный стиль по значению колонки: rgba-фон 0.14, цветной текст, полоса слева; строки — контрастный rgba", async () => {
    installFetch({ store: GROUP_STORE });
    await openList();
    fireEvent.click(screen.getByTestId("group-toggle"));
    fireEvent.change(screen.getByTestId("color-column-select"), { target: { value: "provider" } });
    const head = await screen.findByTestId("subnets-group-RUVDS") as HTMLElement;
    // прозрачный фон акцента + текст полным цветом палитры (не пастель)
    expect(head.style.backgroundColor).toMatch(/rgba\(/);
    expect(head.style.color).toMatch(/^rgb\(/); // jsdom отдаёт hex как rgb()
    // полоса слева 3px — на единственной ячейке заголовка
    const td = head.querySelector("td") as HTMLElement;
    expect(td.style.borderLeft).toMatch(/3px solid rgb\(/); // jsdom: hex → rgb()
    // строки группы — контрастный rgba-фон (0.12)
    expect(screen.getByTestId("subnets-row-r1").style.backgroundColor).toMatch(/^rgba\(/);
  });

  // ── Иконки переехали с файлов на ASN ────────────────────────

  it("иконки провайдеров/списков убраны: кнопок загрузки и img в дереве/шапке/строках/группах нет", async () => {
    installFetch({ store: ICON_STORE });
    await openList();
    expect(screen.queryByTestId("provider-icon-btn-p1")).toBeNull();
    expect(screen.queryByTestId("list-icon-upload")).toBeNull();
    expect(document.querySelector('img[src^="/api/subnets/provider-icon/"]')).toBeNull();
    expect(document.querySelector('img[src^="/api/subnets/list-icon/"]')).toBeNull();
    // и при группировке — заголовок группы без иконки провайдера
    fireEvent.click(screen.getByTestId("group-toggle"));
    await screen.findByText("RUVDS (1)");
    expect(document.querySelector('img[src^="/api/subnets/provider-icon/"]')).toBeNull();
    expect(document.querySelector('img[src^="/api/subnets/list-icon/"]')).toBeNull();
  });

  // ── Справочник ASN: кнопка «Справочник» под деревом → полная таблица справа ──

  it("ASN: кнопка «Справочник» (asn-dir-btn) под деревом открывает справа таблицу asn-view с asn-row-*", async () => {
    const calls = installFetch({ asns: [{ asn: "AS12345", name: "Яндекс" }] });
    await openList();
    // большая кнопка видна сразу, без клика; старой рамки-вкладки больше нет
    const dirBtn = screen.getByTestId("asn-dir-btn");
    expect(dirBtn).toHaveTextContent("Справочник");
    expect(screen.queryByTestId("asn-tab")).toBeNull();
    expect(screen.queryByTestId("asn-list")).toBeNull();
    // клик → справа таблица справочника, строки asn-row-{asn}
    fireEvent.click(dirBtn);
    expect(await screen.findByTestId("asn-view")).toBeInTheDocument();
    const row = screen.getByTestId("asn-row-AS12345");
    expect(row).toHaveTextContent("AS12345");
    expect(row).toHaveTextContent("Яндекс");
    // пока открыт справочник, таблица подсетей скрыта
    expect(screen.queryByText("203.0.113.0/24")).toBeNull();
  });

  it("ASN: добавление через asn-add — POST /asns (с netname/country/asn_type/category), новая строка появляется в таблице", async () => {
    const calls = installFetch({ asns: [{ asn: "AS12345", name: "Яндекс" }] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    fireEvent.change(screen.getByTestId("asn-new-asn"), { target: { value: "99999" } });
    fireEvent.change(screen.getByTestId("asn-new-name"), { target: { value: "Новый" } });
    fireEvent.change(screen.getByTestId("asn-new-netname"), { target: { value: "RU-NEW" } });
    fireEvent.change(screen.getByTestId("asn-new-country"), { target: { value: "RU" } });
    fireEvent.change(screen.getByTestId("asn-new-asn_type"), { target: { value: "hosting" } });
    fireEvent.change(screen.getByTestId("asn-new-category"), { target: { value: "CDN" } });
    fireEvent.click(screen.getByTestId("asn-add"));
    await waitFor(() => {
      const post = calls.find(c => c.url === "/api/subnets/asns" && c.init?.method === "POST");
      expect(post).toBeTruthy();
      expect(JSON.parse(String(post!.init!.body))).toEqual({
        asn: "99999", name: "Новый", netname: "RU-NEW", country: "RU",
        asn_type: "hosting", category: "CDN",
      });
    });
    // после мутации — loadAsns() перезагрузил справочник, новая запись видна
    expect(await screen.findByTestId("asn-row-AS99999")).toHaveTextContent("Новый");
  });

  it("ASN: «Синхронизировать» (asn-sync) шлёт POST /asns/sync и наполняет справочник из строк", async () => {
    const store = {
      providers: [{ id: "p1", name: "МТС", lists: [{ id: "l1", name: "Основной",
        columns: [
          { key: "subnet", title: "Подсеть" }, { key: "asn", title: "ASN" },
          { key: "provider", title: "Провайдер" },
        ],
        rows: [
          { id: "r1", values: { subnet: "10.0.0.0/8", asn: "AS3261", provider: "Ростелеком" }, operators: {} },
          { id: "r2", values: { subnet: "11.0.0.0/8", asn: "мусор", provider: "Мусор" }, operators: {} },
        ] }] }],
      operators: [],
    };
    const calls = installFetch({ store, asns: [] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    fireEvent.click(screen.getByTestId("asn-sync"));
    await waitFor(() => {
      const sync = calls.find(c => c.url === "/api/subnets/asns/sync" && c.init?.method === "POST");
      expect(sync).toBeTruthy();
    });
    // после sync — loadAsns() перезагрузил справочник: запись из строки
    // появилась (asn нормализован), мусорный asn проигнорирован
    expect(await screen.findByTestId("asn-row-AS3261")).toHaveTextContent("Ростелеком");
    expect(screen.queryByTestId("asn-row-ASмусор")).toBeNull();
  });

  it("ASN: «Применить из справочника» (asn-apply) шлёт POST /asns/apply и переносит name+netname в строки", async () => {
    const store = {
      providers: [{ id: "p1", name: "МТС", lists: [{ id: "l1", name: "Основной",
        columns: [
          { key: "subnet", title: "Подсеть" }, { key: "asn", title: "ASN" },
          { key: "provider", title: "Провайдер" }, { key: "netname", title: "Netname" },
        ],
        rows: [
          // у r1 свой provider — apply перезапишет его из справочника
          { id: "r1", values: { subnet: "10.0.0.0/8", asn: "AS12345", provider: "Старое" }, operators: {} },
          { id: "r2", values: { subnet: "11.0.0.0/8", asn: "AS999" }, operators: {} },
        ] }] }],
      operators: [],
    };
    const calls = installFetch({ store, asns: [
      { asn: "AS12345", name: "Яндекс", netname: "RU-YANDEX" },
      { asn: "AS999", name: "", netname: "RU-OTHER" },
    ] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    // кнопка в шапке рядом с asn-sync
    expect(screen.getByTestId("asn-apply")).toHaveTextContent("Применить из справочника");
    fireEvent.click(screen.getByTestId("asn-apply"));
    await waitFor(() => {
      const apply = calls.find(c => c.url === "/api/subnets/asns/apply" && c.init?.method === "POST");
      expect(apply).toBeTruthy();
    });
    // назад к подсетям: строки получили provider/netname из справочника
    fireEvent.click(screen.getByTestId("asn-back"));
    const row1 = (await screen.findByTestId("subnets-row-r1")).querySelectorAll("td");
    expect(row1[2]).toHaveTextContent("Яндекс");      // provider перезаписан
    expect(row1[3]).toHaveTextContent("RU-YANDEX");   // netname перенесён
    const row2 = screen.getByTestId("subnets-row-r2").querySelectorAll("td");
    expect(row2[3]).toHaveTextContent("RU-OTHER");    // запись только с netname
  });

  it("ASN: «Типы ASN» (asn-enrich-types) шлёт POST /asns/enrich-types и заполняет типы в справочнике", async () => {
    const calls = installFetch({ asns: [
      { asn: "AS49505", name: "Selectel", netname: "RU-SELECTEL" },
      { asn: "AS3261", name: "Ростелеком" },
      { asn: "AS11111", name: "ПФР", note: "пенсионный фонд" },
      { asn: "AS22222", name: "" },
    ] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    // кнопка в шапке рядом с asn-sync/asn-apply
    expect(screen.getByTestId("asn-enrich-types")).toHaveTextContent("Типы ASN");
    fireEvent.click(screen.getByTestId("asn-enrich-types"));
    await waitFor(() => {
      const post = calls.find(c =>
        c.url === "/api/subnets/asns/enrich-types" && c.init?.method === "POST");
      expect(post).toBeTruthy();
    });
    // loadAsns() перезагрузил справочник: типы проставлены эвристикой
    // (hosting/isp/business), запись без данных — без типа
    expect(await screen.findByTestId("asn-row-AS49505")).toHaveTextContent("Hosting");
    expect(screen.getByTestId("asn-row-AS3261")).toHaveTextContent("ISP");
    expect(screen.getByTestId("asn-row-AS11111")).toHaveTextContent("Business");
    const cells4 = screen.getByTestId("asn-row-AS22222").querySelectorAll("td");
    expect(cells4[5]).toHaveTextContent("—");
  });

  it("ASN: в asn-view колонка Netname — ячейка с a.netname (или «—»)", async () => {
    installFetch({ asns: [
      { asn: "AS12345", name: "Яндекс", netname: "RU-YANDEX" },
      { asn: "AS999", name: "Без" },
    ] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    // заголовок колонки Netname между Название и Примечание
    const headers = Array.from(screen.getByTestId("asn-view").querySelectorAll("th"))
      .map(th => th.textContent);
    expect(headers).toContain("Netname");
    // ячейка: netname из записи; у записи без netname — «—»
    const cells1 = screen.getByTestId("asn-row-AS12345").querySelectorAll("td");
    expect(cells1[3]).toHaveTextContent("RU-YANDEX");
    const cells2 = screen.getByTestId("asn-row-AS999").querySelectorAll("td");
    expect(cells2[3]).toHaveTextContent("—");
  });

  it("ASN: в asn-view колонки [Иконка, ASN, Провайдер, Netname, Страна, Тип ASN, Категория, Примечание, Действия]; страна/тип — ячейки записи", async () => {
    installFetch({ asns: [
      { asn: "AS12345", name: "Яндекс", netname: "RU-YANDEX", country: "RU", asn_type: "hosting" },
      { asn: "AS999", name: "Без" },
    ] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    // заголовки: «Название» переименовано в «Провайдер», добавлены Страна/Тип ASN/Категория
    const headers = Array.from(screen.getByTestId("asn-view").querySelectorAll("th"))
      .map(th => th.textContent);
    expect(headers).toEqual(["Иконка", "ASN", "Провайдер", "Netname", "Страна", "Тип ASN", "Категория", "Примечание", "Действия"]);
    // ячейки: страна и тип ASN из записи; у записи без них — «—»
    const cells1 = screen.getByTestId("asn-row-AS12345").querySelectorAll("td");
    expect(cells1[4]).toHaveTextContent("RU");        // Страна
    expect(cells1[5]).toHaveTextContent("Hosting");   // Тип ASN (чип)
    expect(cells1[6]).toHaveTextContent("—");         // Категория не задана
    const cells2 = screen.getByTestId("asn-row-AS999").querySelectorAll("td");
    expect(cells2[4]).toHaveTextContent("—");
    expect(cells2[5]).toHaveTextContent("—");
    expect(cells2[6]).toHaveTextContent("—");
  });

  it("ASN: в asn-view колонка Категория — ячейка с a.category (или «—»)", async () => {
    installFetch({ asns: [
      { asn: "AS12345", name: "Яндекс", category: "CDN" },
      { asn: "AS999", name: "Без" },
    ] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    const cells1 = screen.getByTestId("asn-row-AS12345").querySelectorAll("td");
    expect(cells1[6]).toHaveTextContent("CDN");
    const cells2 = screen.getByTestId("asn-row-AS999").querySelectorAll("td");
    expect(cells2[6]).toHaveTextContent("—");
  });

  it("таблица подсетей: asnname рендерится из columns («Название ASN»), country — «Страна», asn_type — «Тип ASN»", async () => {
    const store = {
      providers: [{ id: "p1", name: "МТС", lists: [{ id: "l1", name: "Основной",
        columns: [
          { key: "subnet", title: "Подсеть" }, { key: "asn", title: "ASN" },
          { key: "asnname", title: "Название ASN" }, { key: "country", title: "Код" },
          { key: "asn_type", title: "Тип" },
        ],
        rows: [{
          id: "r1",
          values: { subnet: "10.0.0.0/8", asn: "AS12345", asnname: "Яндекс", country: "RU", asn_type: "hosting" },
          operators: {},
        }],
      }] }],
      operators: [],
    };
    installFetch({ store });
    await openList();
    const headers = Array.from(screen.getByTestId("subnets-table-scroll").querySelectorAll("th"))
      .map(th => th.textContent);
    // asnname больше не переопределяется как «Организация» — берётся title из columns
    expect(headers).toContain("Название ASN");
    expect(headers).not.toContain("Организация");
    expect(headers).toContain("Страна");
    expect(headers).toContain("Тип ASN");
  });

  it("ASN: asn-back возвращает к подсетям; выбор списка в дереве тоже сбрасывает справочник", async () => {
    installFetch({ asns: [{ asn: "AS12345", name: "Яндекс" }] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    // «Назад» — снова таблица подсетей
    fireEvent.click(screen.getByTestId("asn-back"));
    expect(await screen.findByText("203.0.113.0/24")).toBeInTheDocument();
    expect(screen.queryByTestId("asn-view")).toBeNull();
    // снова открыли справочник; клик по списку в дереве возвращает к подсетям
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    fireEvent.click(screen.getByText("Основной"));
    expect(await screen.findByText("203.0.113.0/24")).toBeInTheDocument();
    expect(screen.queryByTestId("asn-view")).toBeNull();
  });

  it("ASN: Pencil редактирует (POST upsert), Trash2 удаляет (DELETE /asns/{asn})", async () => {
    const calls = installFetch({ asns: [{ asn: "AS12345", name: "Яндекс", note: "" }] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    const row = await screen.findByTestId("asn-row-AS12345");
    // редактирование: prompt'ы с текущими значениями
    // (Провайдер, netname, страна, тип, категория, примечание) → POST
    const promptSpy = vi.spyOn(window, "prompt")
      .mockReturnValueOnce("Яндекс Облако")
      .mockReturnValueOnce("RU-YANDEX")
      .mockReturnValueOnce("RU")
      .mockReturnValueOnce("hosting")
      .mockReturnValueOnce("CDN")
      .mockReturnValueOnce("новое примечание");
    fireEvent.click(row.querySelector('button[title="Изменить"]')!);
    await waitFor(() => {
      const post = calls.filter(c => c.url === "/api/subnets/asns" && c.init?.method === "POST");
      expect(post.length).toBe(1);
      expect(JSON.parse(String(post[0].init!.body))).toEqual({
        asn: "AS12345", name: "Яндекс Облако", netname: "RU-YANDEX",
        country: "RU", asn_type: "hosting", category: "CDN", note: "новое примечание",
      });
    });
    promptSpy.mockRestore();
    // удаление: confirm + DELETE
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByTestId("asn-row-AS12345").querySelector('button[title="Удалить"]')!);
    await waitFor(() => {
      const del = calls.find(c => c.url === "/api/subnets/asns/AS12345" && c.init?.method === "DELETE");
      expect(del).toBeTruthy();
    });
    // после перезагрузки записи нет
    await waitFor(() => expect(screen.queryByTestId("asn-row-AS12345")).toBeNull());
    confirmSpy.mockRestore();
  });

  it("ASN: загрузка иконки записи (asn-icon-upload) шлёт multipart POST /asns/{asn}/icon", async () => {
    const calls = installFetch({ asns: [{ asn: "AS12345", name: "Яндекс" }] });
    await openList();
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    fireEvent.click(screen.getByTestId("asn-icon-upload-AS12345"));
    const file = new File(["png-bytes"], "icon.png", { type: "image/png" });
    fireEvent.change(screen.getByTestId("subnets-icon-file"), { target: { files: [file] } });
    await waitFor(() => {
      const post = calls.find(c => c.url === "/api/subnets/asns/AS12345/icon");
      expect(post).toBeTruthy();
      expect(post!.init!.method).toBe("POST");
      const fd = post!.init!.body as FormData;
      expect(fd).toBeInstanceOf(FormData);
      expect((fd.get("file") as File).name).toBe("icon.png");
    });
  });

  it("иконка ASN из справочника показывается слева от подсети (только если у записи она есть)", async () => {
    const store = {
      providers: [{ id: "p1", name: "МТС", lists: [{ id: "l1", name: "Основной",
        columns: [{ key: "subnet", title: "Подсеть" }, { key: "asn", title: "ASN" }],
        rows: [
          { id: "r1", values: { subnet: "10.0.0.0/8", asn: "AS12345" }, operators: {} },
          { id: "r2", values: { subnet: "11.0.0.0/8", asn: "AS999" }, operators: {} },
          { id: "r3", values: { subnet: "12.0.0.0/8" }, operators: {} },
        ] }] }],
      operators: [],
    };
    installFetch({ store, asns: [
      { asn: "AS12345", name: "Яндекс", icon: "asn_AS12345.png" },
      { asn: "AS999", name: "Без иконки", icon: "" },
    ] });
    await openList();
    // r1: asn из справочника с иконкой — img перед подсетью
    const icon = screen.getByTestId("asn-row-icon-r1");
    expect(icon.getAttribute("src")).toBe("/api/subnets/asns/AS12345/icon");
    // r2: запись есть, но без icon; r3: asn нет вовсе — иконки нет
    expect(screen.queryByTestId("asn-row-icon-r2")).toBeNull();
    expect(screen.queryByTestId("asn-row-icon-r3")).toBeNull();
    // в справочнике (asn-view) у записи с иконкой — img, у без иконки — нет
    fireEvent.click(screen.getByTestId("asn-dir-btn"));
    await screen.findByTestId("asn-view");
    expect(screen.getByTestId("asn-row-AS12345")
      .querySelector('img[src="/api/subnets/asns/AS12345/icon"]')).toBeTruthy();
    expect(screen.getByTestId("asn-row-AS999")
      .querySelector('img[src^="/api/subnets/asns/"]')).toBeNull();
  });

  it("provider строки: пустое значение подставляется из справочника (fallback), своё — важнее", async () => {
    const store = {
      providers: [{ id: "p1", name: "МТС", lists: [{ id: "l1", name: "Основной",
        columns: [
          { key: "subnet", title: "Подсеть" }, { key: "asn", title: "ASN" },
          { key: "provider", title: "Провайдер" },
        ],
        rows: [
          { id: "r1", values: { subnet: "10.0.0.0/8", asn: "AS12345", provider: "" }, operators: {} },
          { id: "r2", values: { subnet: "11.0.0.0/8", asn: "AS999", provider: "Свой" }, operators: {} },
          { id: "r3", values: { subnet: "12.0.0.0/8", asn: "AS12345" }, operators: {} },
        ] }] }],
      operators: [],
    };
    installFetch({ store, asns: [{ asn: "AS12345", name: "Яндекс" }] });
    await openList();
    const cell = (id: string) => screen.getByTestId(`subnets-row-${id}`).querySelectorAll("td")[2];
    expect(cell("r1")).toHaveTextContent("Яндекс"); // пустой provider → справочник
    expect(cell("r2")).toHaveTextContent("Свой");   // своё значение важнее справочника
    expect(cell("r3")).toHaveTextContent("Яндекс"); // отсутствующий provider → справочник
  });
});
