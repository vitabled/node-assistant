import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PanelRegistry } from "./PanelRegistry";

// Wave-4 PR-2: виджет панелей выделен + характеристики редактируются.

const PANELS = {
  panels: [
    {
      id: "p1", name: "Новая панель", kind: "custom", panel_url: "https://p1.example.com",
      api_token: "tok-1", default_internal_squad_ids: ["sq1"], default_external_squad_ids: [],
    },
    {
      id: "p2", name: "Вторая", kind: "custom", panel_url: "https://p2.example.com",
      api_token: "tok-2", default_internal_squad_ids: [], default_external_squad_ids: [],
    },
  ],
  active_panel_id: "p1",
};

function mockFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fm = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url.includes("/squads/")) return new Response("[]", { status: 200 });
    if (url === "/api/settings/remnawave/panels" && (!init || !init.method)) {
      return new Response(JSON.stringify(PANELS), { status: 200 });
    }
    if (init?.method === "PUT") return new Response("{}", { status: 200 });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fm);
  return calls;
}

describe("PanelRegistry", () => {
  beforeEach(() => { vi.unstubAllGlobals(); });

  it("у каждой панели есть кнопка «Изменить» и URL виден в плитке", async () => {
    mockFetch();
    render(<PanelRegistry />);
    expect(await screen.findAllByText("Изменить")).toHaveLength(2);
    expect(screen.getByText("https://p1.example.com")).toBeInTheDocument();
  });

  it("«Изменить» открывает модалку с предзаполненными полями, сохранение шлёт PUT", async () => {
    const calls = mockFetch();
    render(<PanelRegistry />);
    const [editBtn] = await screen.findAllByText("Изменить");
    fireEvent.click(editBtn);

    const nameInput = await screen.findByDisplayValue("Новая панель");
    const urlInput = screen.getByDisplayValue("https://p1.example.com");
    expect(nameInput).toBeInTheDocument();
    // токен предзаполнен из GET /panels — не затирается пустым
    expect(screen.getByPlaceholderText("Токен панели")).toHaveValue("tok-1");

    fireEvent.change(nameInput, { target: { value: "Главная EU" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => {
      const put = calls.find(c => c.init?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put!.url).toBe("/api/settings/remnawave/panels/p1");
      const body = JSON.parse(String(put!.init!.body));
      expect(body.name).toBe("Главная EU");
      expect(body.panel_url).toBe("https://p1.example.com");
      expect(body.api_token).toBe("tok-1"); // не потёрся
      expect(body.default_internal_squad_ids).toEqual(["sq1"]);
    });
  });

  it("ошибка сохранения показывается в модалке", async () => {
    const calls = mockFetch();
    calls; // silence
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("/squads/")) return new Response("[]", { status: 200 });
      if (init?.method === "PUT") return new Response(JSON.stringify({ detail: "Панель не найдена" }), { status: 404 });
      return new Response(JSON.stringify(PANELS), { status: 200 });
    }));
    render(<PanelRegistry />);
    const [editBtn] = await screen.findAllByText("Изменить");
    fireEvent.click(editBtn);
    await screen.findByDisplayValue("Новая панель");
    fireEvent.click(screen.getByText("Сохранить"));
    expect(await screen.findByText("Панель не найдена")).toBeInTheDocument();
  });
});
