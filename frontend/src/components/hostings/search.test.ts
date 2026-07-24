import { describe, it, expect } from "vitest";
import { matchHosting, matchedCountries, parsePriceQuery } from "./search";
import type { Hosting } from "./api";

const H = (over: Partial<Hosting> = {}): Hosting => ({
  id: "h1", name: "Hetzner", website: "", notes: "", features: "BBR",
  tariffs: [{ name: "CX22", specs: "2 vCPU / 4 GB", bandwidth: "1 Гбит/с, 20 ТБ", price: 5.5, currency: "EUR", period: "mo" }],
  locations: [{ city: "Falkenstein", country_code: "DE", lat: 50.5, lng: 12.4, note: "" }],
  created_at: 0,
  ...over,
});

describe("parsePriceQuery", () => {
  it("recognises the three forms", () => {
    expect(parsePriceQuery("<20")).toEqual({ op: "<", a: 20 });
    expect(parsePriceQuery("> 5")).toEqual({ op: ">", a: 5 });
    expect(parsePriceQuery("10-30")).toEqual({ op: "range", a: 10, b: 30 });
  });

  it("returns null for ordinary words so they don't filter by price", () => {
    expect(parsePriceQuery("Хетцнер")).toBeNull();
    expect(parsePriceQuery("CX22")).toBeNull();
    expect(parsePriceQuery("")).toBeNull();
  });
});

describe("matchHosting", () => {
  it("matches an empty query", () => {
    expect(matchHosting(H(), "")).toBe(true);
  });

  it("matches by hosting name, city and tariff", () => {
    expect(matchHosting(H(), "hetz")).toBe(true);
    expect(matchHosting(H(), "falken")).toBe(true);
    expect(matchHosting(H(), "cx22")).toBe(true);
  });

  it("matches by country in Russian, in English and by code", () => {
    expect(matchHosting(H(), "Германия")).toBe(true);
    expect(matchHosting(H(), "germany")).toBe(true);
    expect(matchHosting(H(), "de")).toBe(true);
  });

  it("matches by channel width — the field added in Ф1", () => {
    expect(matchHosting(H(), "Гбит")).toBe(true);
    expect(matchHosting(H(), "20 ТБ")).toBe(true);
  });

  it("filters by price expression", () => {
    expect(matchHosting(H(), "<10")).toBe(true);
    expect(matchHosting(H(), "<3")).toBe(false);
    expect(matchHosting(H(), "5-6")).toBe(true);
    expect(matchHosting(H(), ">100")).toBe(false);
  });

  it("does not match unrelated text", () => {
    expect(matchHosting(H(), "марс")).toBe(false);
  });

  it("survives a hosting with no tariffs or locations", () => {
    const bare = H({ tariffs: [], locations: [] });
    expect(matchHosting(bare, "hetz")).toBe(true);
    expect(matchHosting(bare, "<20")).toBe(false);
  });

  // Tariffs stored before Ф1 have no `bandwidth` key at all.
  it("survives a tariff without the bandwidth field", () => {
    const legacy = H({ tariffs: [{ name: "old", specs: "", price: 3, currency: "USD", period: "mo" } as any] });
    expect(matchHosting(legacy, "old")).toBe(true);
  });
});

describe("matchedCountries", () => {
  it("collects the codes of matching hostings only", () => {
    const de = H();
    const nl = H({ id: "h2", name: "TransIP", locations: [{ city: "Amsterdam", country_code: "NL", lat: 0, lng: 0, note: "" }] });
    expect(matchedCountries([de, nl], "amsterdam")).toEqual(new Set(["NL"]));
    expect(matchedCountries([de, nl], "")).toEqual(new Set(["DE", "NL"]));
  });
});
