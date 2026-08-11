import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SubscriptionAnalyze } from "./SubscriptionAnalyze";

// Wave-4 PR-8: колонка «IP (выход)» — проверка через /egress.

const ROWS = {
  results: [{
    host: "ru04.example.com", hosts: ["ru04.example.com"], names: ["FS Russia"],
    ip: "111.88.13.7",
    asn: { number: 44677, name: "MTS", website: "", website_source: "" },
    geo_actual: { cc: "RU", city: "Moscow" },
    geo_registry: { cc: "RU" },
    net: { org: "MTS PJSC", isp: "", ptr: "", hosting: true, proxy: false },
  }],
};

const EGRESS = {
  egress: { ip: "5.188.9.10", cc: "NL", city: "Amsterdam", org: "Exit BV",
            isp: "", as: "AS999", hosting: true, proxy: false },
};

function installFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url === "/api/subscription-analyze/egress") return new Response(JSON.stringify(EGRESS), { status: 200 });
    if (url === "/api/subscription-analyze") return new Response(JSON.stringify(ROWS), { status: 200 });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

describe("SubscriptionAnalyze › выходной IP", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("кнопка «Выход» запрашивает egress и показывает IP с флагом", async () => {
    const calls = installFetch();
    render(<SubscriptionAnalyze />);
    fireEvent.change(screen.getByPlaceholderText(/sub.example.com/), { target: { value: "https://sub.example.com/x" } });
    fireEvent.click(screen.getByText("Проанализировать"));

    fireEvent.click(await screen.findByText("Выход"));
    expect(await screen.findByText("5.188.9.10")).toBeInTheDocument();
    expect(screen.getByText("relay")).toBeInTheDocument();   // вход ≠ выход

    const eg = calls.find(c => c.url === "/api/subscription-analyze/egress");
    expect(JSON.parse(String(eg!.init!.body))).toMatchObject({
      input: "https://sub.example.com/x", host: "ru04.example.com",
    });
  });

  it("«Проверить все выходы» идёт по строкам", async () => {
    const calls = installFetch();
    render(<SubscriptionAnalyze />);
    fireEvent.change(screen.getByPlaceholderText(/sub.example.com/), { target: { value: "https://sub.example.com/x" } });
    fireEvent.click(screen.getByText("Проанализировать"));
    await screen.findByText("Выход");

    fireEvent.click(screen.getByText("Проверить все выходы"));
    await waitFor(() => expect(screen.getByText("5.188.9.10")).toBeInTheDocument());
    expect(calls.filter(c => c.url === "/api/subscription-analyze/egress")).toHaveLength(1);
  });
});
