import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeTab } from "./Settings";

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
