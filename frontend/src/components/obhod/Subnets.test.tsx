import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Subnets } from "./Subnets";

// Wave-5 PR-5: «Подсети» — дерево, таблица, режим редактирования.

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

function installFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url === "/api/subnets") return new Response(JSON.stringify(STORE), { status: 200 });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

describe("Subnets", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("таблица рендерится; иконки операторов видны без режима правки", async () => {
    installFetch();
    render(<Subnets />);
    fireEvent.click(await screen.findByText("Основной"));
    expect(await screen.findByText("203.0.113.0/24")).toBeInTheDocument();
    expect(screen.getByText("AS123")).toBeInTheDocument();
    const icons = document.querySelectorAll('img[src^="/operators/"]');
    expect(icons.length).toBe(5);
    // чекбоксов нет вне режима правки
    expect(document.querySelectorAll('input[type="checkbox"]').length).toBe(0);
  });

  it("режим редактирования: чекбоксы операторов тогглят иконки", async () => {
    const calls = installFetch();
    render(<Subnets />);
    fireEvent.click(await screen.findByText("Основной"));
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
    render(<Subnets />);
    fireEvent.click(await screen.findByText("Основной"));
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
});
