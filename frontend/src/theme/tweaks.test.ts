import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyAccent, applyDensity, applyThemeMode, resolveThemeMode,
  loadAccent, loadDensity, loadThemeMode,
  saveAccent, saveDensity, saveThemeMode, THEME_MODES,
  applySkin, loadSkin, saveSkin, SKINS, resolveAccentForSkin,
  PALETTES, STYLE_PRESETS, applyPalette, applyStyle,
  loadPalette, savePalette, loadStylePreset, saveStylePreset,
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

  it("nodeflow accent uses its light variant on light theme and reverts on dark", () => {
    mockMatchMedia(false);
    applyThemeMode("light");
    applyAccent("nodeflow");
    const s = document.documentElement.style;
    expect(s.getPropertyValue("--accent")).toBe("#157A2B");
    expect(s.getPropertyValue("--accent-ink")).toBe("#FFFFFF");
    // Смена темы переприменяет текущий акцент — без ручного повтора applyAccent.
    applyThemeMode("dark");
    expect(s.getPropertyValue("--accent")).toBe("#48BD54");
    expect(s.getPropertyValue("--accent-ink")).toBe("#062110");
  });

  it("accents without a light variant stay identical on light theme", () => {
    mockMatchMedia(false);
    applyThemeMode("light");
    applyAccent("blue");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#8b5cf6");
  });

  it("resolveAccentForSkin: saved choice wins, nodeflow skin defaults to its green", () => {
    expect(resolveAccentForSkin("nodeflow")).toBe("nodeflow");
    expect(resolveAccentForSkin("apple")).toBe("blue");
    saveAccent("violet");
    expect(resolveAccentForSkin("nodeflow")).toBe("violet"); // явный выбор важнее дефолта скина
    localStorage.setItem("ni_accent", "chartreuse");
    expect(resolveAccentForSkin("nodeflow")).toBe("nodeflow"); // мусор = «не выбирал»
  });

  it("resolveAccentForSkin: axottle skin defaults to the panel accent of the palette", () => {
    localStorage.clear();
    expect(resolveAccentForSkin("axottle")).toBe("axottle");
    applyAccent("axottle");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#FC9CA1");
    expect(document.documentElement.style.getPropertyValue("--accent-ink")).toBe("#2A0E10");
    saveAccent("lime");
    expect(resolveAccentForSkin("axottle")).toBe("lime");
  });

  it("axottle palette mint несёт свой акцент — #34D8A8", () => {
    localStorage.clear();
    expect(resolveAccentForSkin("axottle", "mint")).toBe("axomint");
    expect(resolveAccentForSkin("axottle", "blush")).toBe("axottle");
    applyAccent("axomint");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#34D8A8");
    expect(document.documentElement.style.getPropertyValue("--accent-ink")).toBe("#06120E");
    // Явный выбор акцента важнее палитры.
    saveAccent("cyan");
    expect(resolveAccentForSkin("axottle", "mint")).toBe("cyan");
  });

  it("axottle accent uses the panel light variant on light theme and reverts on dark", () => {
    mockMatchMedia(false);
    applyThemeMode("light");
    applyAccent("axottle");
    const s = document.documentElement.style;
    expect(s.getPropertyValue("--accent")).toBe("#C82C3C");
    expect(s.getPropertyValue("--accent-ink")).toBe("#FFFFFF");
    applyThemeMode("dark");
    expect(s.getPropertyValue("--accent")).toBe("#FC9CA1");
  });

  it("resolveAccentForSkin: remnawave defaults to its cyan accent", () => {
    localStorage.clear();
    expect(resolveAccentForSkin("remnawave")).toBe("remnawave");
    applyAccent("remnawave");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#0096b5");
    expect(document.documentElement.style.getPropertyValue("--accent-hi")).toBe("#2ad4f0");
  });

  it("remnawave accent uses its light variant (cyan-8) on light theme and reverts on dark", () => {
    mockMatchMedia(false);
    applyThemeMode("light");
    applyAccent("remnawave");
    const s = document.documentElement.style;
    expect(s.getPropertyValue("--accent")).toBe("#00677a");
    expect(s.getPropertyValue("--accent-hi")).toBe("#00677a");
    expect(s.getPropertyValue("--accent-ink")).toBe("#FFFFFF");
    // Смена темы переприменяет текущий акцент (как у nodeflow).
    applyThemeMode("dark");
    expect(s.getPropertyValue("--accent")).toBe("#0096b5");
    expect(s.getPropertyValue("--accent-hi")).toBe("#2ad4f0");
    expect(s.getPropertyValue("--accent-ink")).toBe("#00161a");
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

describe("skin", () => {
  beforeEach(() => { document.documentElement.removeAttribute("data-skin"); });

  it("defaults to axottle (дизайн Axottle) when nothing stored", () => {
    expect(loadSkin("acc1")).toBe("axottle");
    expect(loadSkin(null)).toBe("axottle");
  });

  it("persists per-account and reads back (isolated)", () => {
    saveSkin("acc1", "console");
    expect(loadSkin("acc1")).toBe("console");
    expect(loadSkin("acc2")).toBe("axottle");
  });

  it("applySkin sets data-skin on :root", () => {
    applySkin("axottle");
    expect(document.documentElement.dataset.skin).toBe("axottle");
    applySkin("apple");
    expect(document.documentElement.dataset.skin).toBe("apple");
    applySkin("console");
    expect(document.documentElement.dataset.skin).toBe("console");
  });

  it("ignores garbage stored value → axottle", () => {
    localStorage.setItem("ni_skin_acc1", "frutiger");
    expect(loadSkin("acc1")).toBe("axottle");
  });

  it("exposes exactly the six skin options, Axottle first", () => {
    expect(SKINS.map(s => s.key)).toEqual(["axottle", "apple", "console", "neon", "nodeflow", "remnawave"]);
  });

  it("keeps the five legacy skins selectable", () => {
    for (const s of ["apple", "console", "neon", "nodeflow", "remnawave"] as const) {
      saveSkin("acc1", s);
      expect(loadSkin("acc1")).toBe(s);
      applySkin(s);
      expect(document.documentElement.dataset.skin).toBe(s);
    }
  });

  it("persists and reads back the remnawave skin", () => {
    saveSkin("acc1", "remnawave");
    expect(loadSkin("acc1")).toBe("remnawave");
    applySkin("remnawave");
    expect(document.documentElement.dataset.skin).toBe("remnawave");
  });
});

// ── Оси дизайн-системы панели Axottle: палитра и стиль ────────────────────────
// Обе оси — атрибуты на <html> (data-palette / data-style), как в панели
// (data-axo-palette / data-style), и хранятся per-account, как skin и mode.
describe("palette & style (оси панели Axottle)", () => {
  beforeEach(() => {
    delete document.documentElement.dataset.palette;
    delete document.documentElement.dataset.style;
  });

  it("палитра по умолчанию blush, мусор тоже даёт blush", () => {
    expect(loadPalette("acc1")).toBe("blush");
    expect(loadPalette(null)).toBe("blush");
    localStorage.setItem("ni_palette_acc1", "chartreuse");
    expect(loadPalette("acc1")).toBe("blush");
  });

  it("applyPalette ставит data-palette, save/load изолированы по аккаунтам", () => {
    applyPalette("mint");
    expect(document.documentElement.dataset.palette).toBe("mint");
    savePalette("acc1", "mint");
    expect(loadPalette("acc1")).toBe("mint");
    expect(loadPalette("acc2")).toBe("blush");
  });

  it("стиль по умолчанию cloud, sharp читается обратно", () => {
    expect(loadStylePreset("acc1")).toBe("cloud");
    localStorage.setItem("ni_style_acc1", "bogus");
    expect(loadStylePreset("acc1")).toBe("cloud");
    saveStylePreset("acc1", "sharp");
    applyStyle("sharp");
    expect(loadStylePreset("acc1")).toBe("sharp");
    expect(document.documentElement.dataset.style).toBe("sharp");
    expect(loadStylePreset("acc2")).toBe("cloud");
  });

  it("PALETTES/STYLE_PRESETS описывают ровно то, что умеет панель", () => {
    expect(PALETTES.map(p => p.key)).toEqual(["blush", "mint"]);
    expect(PALETTES.map(p => p.accent)).toEqual(["axottle", "axomint"]);
    expect(STYLE_PRESETS.map(s => s.key)).toEqual(["cloud", "sharp"]);
  });
});
