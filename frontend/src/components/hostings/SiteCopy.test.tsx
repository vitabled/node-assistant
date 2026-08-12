import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SiteCopy } from "./SiteCopy";

// Wave-4 PR-11: форма «Копия сайта» — запуск задачи с параметрами.

function installFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url === "/api/sitecopy") {
      return new Response(JSON.stringify({ task_id: "t-1", task_type: "sitecopy" }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  // useTaskStream ходит по WebSocket — заглушим, чтобы не падал jsdom
  class FakeWS { close() {} addEventListener() {} removeEventListener() {} }
  vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
  return calls;
}

describe("SiteCopy", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("запускает задачу с url/depth/max_mb", async () => {
    const calls = installFetch();
    render(<SiteCopy />);
    fireEvent.change(screen.getByPlaceholderText("https://example.com"),
      { target: { value: "https://docs.example.ru/page" } });
    fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "3" } });
    fireEvent.change(screen.getAllByRole("combobox")[1], { target: { value: "150" } });
    fireEvent.click(screen.getByText("Скопировать сайт"));

    await waitFor(() => {
      const post = calls.find(c => c.url === "/api/sitecopy");
      expect(post).toBeTruthy();
      expect(JSON.parse(String(post!.init!.body))).toEqual({
        url: "https://docs.example.ru/page", depth: 3, max_mb: 150,
      });
    });
  });

  it("ошибка валидации показывается в форме", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "URL не разрешён: нужен http(s) с публичным хостом" }), { status: 422 })));
    render(<SiteCopy />);
    fireEvent.change(screen.getByPlaceholderText("https://example.com"),
      { target: { value: "http://127.0.0.1/x" } });
    fireEvent.click(screen.getByText("Скопировать сайт"));
    expect(await screen.findByText(/URL не разрешён/)).toBeInTheDocument();
  });
});
