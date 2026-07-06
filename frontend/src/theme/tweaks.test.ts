import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyAccent, applyDensity, applyThemeMode, resolveThemeMode,
  loadAccent, loadDensity, loadThemeMode,
  saveAccent, saveDensity, saveThemeMode, THEME_MODES,
} from "./tweaks";

// jsdom has no matchMedia — install a controllable stub. `_light` flips the
// emulated OS preference; `_emit` fires the "change" listeners.
function mockMatchMedia(light: boolean) {
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const state = { light };
  (window as unknown as { matchMedia: unknown }).matchMedia = (q: string) => ({
    matches: q.includes("light") ? state.light : !state.light,
    media: q,
    addEventListener: (_: string, l: (e: MediaQueryListEvent) => void) => listeners.add(l),
    removeEventListener: (_: string, l: (e: MediaQueryListEvent) => void) => listeners.delete(l),
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  });
  return {
    set(v: boolean) { state.light = v; },
    emit() { listeners.forEach(l => l({ matches: state.light } as MediaQueryListEvent)); },
  };
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  delete document.body.dataset.density;
  document.documentElement.removeAttribute("style");
});
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

describe("theme mode", () => {
  it("applyThemeMode sets data-theme on :root to the explicit mode", () => {
    mockMatchMedia(false);
    applyThemeMode("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyThemeMode("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("system mode resolves from prefers-color-scheme and reacts to OS changes", () => {
    const mm = mockMatchMedia(false); // OS = dark
    applyThemeMode("system");
    expect(document.documentElement.dataset.theme).toBe("dark");
    mm.set(true); mm.emit(); // OS flips to light at runtime
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("resolveThemeMode maps system to the OS preference", () => {
    mockMatchMedia(true);
    expect(resolveThemeMode("system")).toBe("light");
    expect(resolveThemeMode("dark")).toBe("dark");
  });

  it("loadThemeMode defaults to system and is per-account", () => {
    expect(loadThemeMode("acc-a")).toBe("system");
    localStorage.setItem("ni_thememode_acc-a", "bogus");
    expect(loadThemeMode("acc-a")).toBe("system");
    saveThemeMode("acc-a", "light");
    saveThemeMode("acc-b", "dark");
    expect(loadThemeMode("acc-a")).toBe("light");
    expect(loadThemeMode("acc-b")).toBe("dark"); // isolated per account
  });

  it("exposes exactly the three mode options", () => {
    expect(THEME_MODES.map(t => t.key)).toEqual(["system", "light", "dark"]);
  });
});

describe("accent", () => {
  it("applyAccent writes the accent CSS variables on :root", () => {
    applyAccent("green");
    const s = document.documentElement.style;
    expect(s.getPropertyValue("--accent")).toBe("#3ECF8E");
    expect(s.getPropertyValue("--accent-dim")).toContain("rgba");
  });

  it("loadAccent defaults to blue for missing/invalid values", () => {
    expect(loadAccent()).toBe("blue");
    localStorage.setItem("ni_accent", "chartreuse");
    expect(loadAccent()).toBe("blue");
    saveAccent("violet");
    expect(loadAccent()).toBe("violet");
  });
});

describe("density", () => {
  it("applyDensity sets the body data-density and persists", () => {
    applyDensity("compact");
    expect(document.body.dataset.density).toBe("compact");
    expect(loadDensity()).toBe("comfortable"); // not saved yet
    saveDensity("compact");
    expect(loadDensity()).toBe("compact");
  });
});
