import { describe, it, expect } from "vitest";
import { validateFullConfig, validateInbound, validateOutbound, validateBalancer, isValidPort, isValidAddress } from "./validators";
import { runFullDiagnostics } from "./diagnostics";

const validConfig = {
  log: { loglevel: "warning" },
  inbounds: [{ tag: "in", protocol: "vless", port: 443, settings: { clients: [{ id: "u" }] }, streamSettings: { network: "tcp", security: "none" } }],
  outbounds: [{ tag: "direct", protocol: "freedom", settings: {} }],
  routing: { rules: [], balancers: [] },
};

describe("profiles/core validators", () => {
  it("accepts a structurally valid config (no ajv errors)", () => {
    expect(validateFullConfig(validConfig)).toEqual([]);
  });

  it("flags an unknown protocol via the ajv enum", () => {
    const errs = validateInbound({ tag: "x", protocol: "bogus", port: 443 });
    expect(errs.some(e => e.field === "protocol" || /protocol/.test(e.message))).toBe(true);
  });

  it("flags an out-of-range port on an inbound", () => {
    const errs = validateInbound({ tag: "x", protocol: "vless", port: 99999 });
    expect(errs.some(e => e.field === "port")).toBe(true);
  });

  it("flags a proxy outbound with a missing server address", () => {
    const errs = validateOutbound({ tag: "p", protocol: "vless", settings: {} });
    expect(errs.some(e => e.field === "address")).toBe(true);
  });

  it("surfaces >0 errors for a broken full config", () => {
    const broken = { inbounds: [{ protocol: "nope" }], outbounds: "not-an-array" };
    expect(validateFullConfig(broken).length).toBeGreaterThan(0);
  });

  it("helper: port and address validators", () => {
    expect(isValidPort(443)).toBe(true);
    expect(isValidPort(0)).toBe(false);
    expect(isValidAddress("1.2.3.4")).toBe(true);
    expect(isValidAddress("example.com")).toBe(true);
    expect(isValidAddress("!!bad!!")).toBe(false);
  });

  it("tags a closed-enum violation with keyword='enum' (so the UI can down-rank it)", () => {
    const errs = validateInbound({ tag: "x", protocol: "future-proto", port: 443 });
    const enumErr = errs.find(e => e.keyword === "enum");
    expect(enumErr).toBeDefined();
    expect(/protocol/.test(enumErr!.field)).toBe(true);
  });

  it("does NOT tag a missing-required (non-enum) error with keyword='enum'", () => {
    // Missing protocol → required-keyword error, must stay a hard blocker (not enum).
    const errs = validateInbound({ tag: "x", port: 443 });
    expect(errs.some(e => e.keyword === "enum")).toBe(false);
  });

  it("balancer with no selectors is reported", () => {
    const errs = validateBalancer({ tag: "B", selector: [] });
    expect(errs.some(m => /селектор/.test(m))).toBe(true);
  });

  it("balancer selector of wrong type surfaces the ajv structural error", () => {
    // selector must be an array — a string violates the schema; previously the
    // ajv errors were computed then dropped on the floor.
    const errs = validateBalancer({ tag: "B", selector: "vless-out" });
    expect(errs.length).toBeGreaterThan(0);
  });
});

describe("profiles/core diagnostics", () => {
  it("reports a dangling outbound reference in routing", () => {
    const cfg = { ...validConfig, routing: { rules: [{ outboundTag: "ghost", domain: ["x.com"] }], balancers: [] } };
    const diags = runFullDiagnostics(cfg as any);
    expect(diags.some(d => d.section === "routing" && /ghost/.test(d.message))).toBe(true);
  });

  it("returns nothing for a null config", () => {
    expect(runFullDiagnostics(null)).toEqual([]);
  });
});
