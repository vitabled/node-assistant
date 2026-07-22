import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeTab, Settings } from "./Settings";

// jsdom has no matchMedia — install a stub so applyThemeMode("system") works.
function mockMatchMedia(light: boolean) {
  (window as unknown as { matchMedia: unknown }).matchMedia = (q: string) => ({
    matches: q.includes("light") ? light : !light,
    media: q, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  });
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  delete document.body.dataset.density;
  mockMatchMedia(false); // OS = dark by default
});
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

describe("Settings › ThemeTab", () => {
  it("picking a mode applies data-theme on :root and persists it", () => {
    render(<ThemeTab />);
    fireEvent.click(screen.getByText("Светлая"));
    expect(document.documentElement.dataset.theme).toBe("light");
    // no active account in the test → device-global key
    expect(localStorage.getItem("ni_thememode")).toBe("light");

    fireEvent.click(screen.getByText("Тёмная"));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("ni_thememode")).toBe("dark");
  });

  it("system mode resolves data-theme from the OS preference", () => {
    mockMatchMedia(true); // OS = light
    render(<ThemeTab />);
    fireEvent.click(screen.getByText("Системная"));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("ni_thememode")).toBe("system");
  });

  it("picking density persists it and sets the body attribute", () => {
    render(<ThemeTab />);
    fireEvent.click(screen.getByText("Плотная"));
    expect(document.body.dataset.density).toBe("compact");
    expect(localStorage.getItem("ni_density")).toBe("compact");
  });

  it("picking an accent persists it and writes the --accent variable", () => {
    render(<ThemeTab />);
    // accent swatches are titled by key; green = #3ECF8E
    fireEvent.click(screen.getByTitle("green"));
    expect(localStorage.getItem("ni_accent")).toBe("green");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#3ECF8E");
  });
});

// ── Wave-7 Plan E Ф1: the tab bar must fit on one screen ───────
describe("Settings › tab bar", () => {
  const TABS = [
    "Remnawave", "Деплой (умолчания)", "Оптимизация ОС", "Мониторинг",
    "Сервера для тестирования", "MCP", "Ассистент", "Токены API",
    "Экспорт/импорт", "Инфраструктура", "Тема",
  ];

  beforeEach(() => {
    // Every tab body fetches something on mount; a permissive stub keeps the
    // test about layout rather than about each tab's data.
    (globalThis as any).fetch = vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({}), text: async () => "",
    }));
  });

  it("renders every tab", async () => {
    render(<Settings />);
    for (const label of TABS) expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("wraps onto multiple rows instead of scrolling horizontally", () => {
    const { container } = render(<Settings />);
    const bar = container.querySelector(".seg");
    // `seg-wrap` is what allows the second row; without it `width:fit-content`
    // forces a single overflowing row.
    expect(bar?.className).toContain("seg-wrap");
    expect((bar as HTMLElement).style.width).toBe("");
  });

  it("the last tab is reachable and switches the content", async () => {
    render(<Settings />);
    fireEvent.click(screen.getByText("Тема"));
    await waitFor(() => expect(screen.getByText("Плотность")).toBeInTheDocument());
  });
});
