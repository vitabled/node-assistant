import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  applyAccent, applyDensity, applyTheme,
  loadAccent, loadDensity, loadTheme,
  saveAccent, saveDensity, saveTheme, THEMES,
} from "./tweaks";

beforeEach(() => {
  localStorage.clear();
  delete document.body.dataset.theme;
  delete document.body.dataset.density;
  document.documentElement.removeAttribute("style");
});
afterEach(() => localStorage.clear());

describe("theme", () => {
  it("applyTheme sets the Apple data-theme and clears it for console", () => {
    applyTheme("apple-light");
    expect(document.body.dataset.theme).toBe("apple-light");
    applyTheme("apple-dark");
    expect(document.body.dataset.theme).toBe("apple-dark");
    applyTheme("console");
    expect(document.body.dataset.theme).toBeUndefined(); // no attribute = default skin
  });

  it("loadTheme defaults to console for missing/invalid values", () => {
    expect(loadTheme()).toBe("console");
    localStorage.setItem("ni_theme", "bogus");
    expect(loadTheme()).toBe("console");
  });

  it("saveTheme round-trips through loadTheme", () => {
    saveTheme("apple-dark");
    expect(loadTheme()).toBe("apple-dark");
  });

  it("exposes exactly the three theme options", () => {
    expect(THEMES.map(t => t.key)).toEqual(["console", "apple-light", "apple-dark"]);
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
