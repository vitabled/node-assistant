import { describe, it, expect } from "vitest";
import { resolveCountryCode, splitFlagEmoji, RU_ALIASES } from "./countryAliases";
import { COUNTRIES } from "../components/CountrySelect";

describe("splitFlagEmoji", () => {
  it("pulls an embedded flag out and returns the rest", () => {
    expect(splitFlagEmoji("🇳🇱 Нидерланды 🚀")).toEqual({ code: "NL", rest: "Нидерланды 🚀" });
  });

  it("leaves a non-flag emoji alone", () => {
    // 🚀 is not a regional indicator — it must survive untouched.
    expect(splitFlagEmoji("Амстердам 🚀")).toEqual({ code: "", rest: "Амстердам 🚀" });
  });

  it("returns the input unchanged when there is no flag", () => {
    expect(splitFlagEmoji("Frankfurt")).toEqual({ code: "", rest: "Frankfurt" });
  });
});

describe("resolveCountryCode", () => {
  it("resolves a Russian country name — the case that was broken", () => {
    // COUNTRIES only carries English names, so this returned "" (a globe) before.
    expect(resolveCountryCode("Нидерланды")).toBe("NL");
  });

  it("resolves a bare ISO code", () => {
    expect(resolveCountryCode("NL")).toBe("NL");
    expect(resolveCountryCode("nl")).toBe("NL");
  });

  it("resolves an English name", () => {
    expect(resolveCountryCode("Netherlands")).toBe("NL");
  });

  it("prefers an embedded flag over everything else", () => {
    expect(resolveCountryCode("🇩🇪 Нидерланды")).toBe("DE");
  });

  it("resolves a mixed label carrying its own code", () => {
    expect(resolveCountryCode("NL Нидерланды 🚀")).toBe("NL");
    expect(resolveCountryCode("DE-Frankfurt-01")).toBe("DE");
  });

  it("resolves colloquial names", () => {
    expect(resolveCountryCode("Голландия")).toBe("NL");
    expect(resolveCountryCode("США")).toBe("US");
    expect(resolveCountryCode("Англия")).toBe("GB");
  });

  it("ignores ё vs е", () => {
    expect(resolveCountryCode("Объединённые Арабские Эмираты")).toBe("AE");
  });

  it("returns empty for something unresolvable", () => {
    expect(resolveCountryCode("Марс")).toBe("");
    expect(resolveCountryCode("")).toBe("");
    expect(resolveCountryCode(null)).toBe("");
  });

  it("does not turn a random two-letter word into a flag", () => {
    // "ok" is not an ISO code we accept; a bare unknown pair must not resolve.
    expect(resolveCountryCode("ok")).toBe("");
  });

  // Guard against the table silently falling behind when COUNTRIES grows: a new
  // country would otherwise render a globe for every Russian-labelled group.
  it("has a Russian alias for every country in the picker", () => {
    const missing = COUNTRIES
      .filter(c => c.code !== "XX")
      .map(c => c.code)
      .filter(code => !RU_ALIASES[code]?.length);
    expect(missing).toEqual([]);
  });
});
