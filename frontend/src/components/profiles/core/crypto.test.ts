import { describe, it, expect } from "vitest";
import {
  generateUUID,
  generateShortId,
  generateRealityKeyPair,
  generateRealitySpiderX,
  generateRealityShortIds,
} from "./crypto";

describe("profiles/core crypto (CSPRNG-backed)", () => {
  it("generateShortId returns a lowercase-hex string of the requested length", () => {
    const id = generateShortId(16);
    expect(id).toHaveLength(16);
    expect(/^[0-9a-f]+$/.test(id)).toBe(true);
  });

  it("generateShortId is non-deterministic across calls", () => {
    const seen = new Set(Array.from({ length: 50 }, () => generateShortId(8)));
    // With 32 bits of entropy, 50 draws colliding would be astronomically unlikely.
    expect(seen.size).toBeGreaterThan(45);
  });

  it("generateUUID produces a canonical v4 UUID", () => {
    const u = generateUUID();
    expect(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(u)
    ).toBe(true);
  });

  it("generateRealitySpiderX starts with / and stays in the url-safe alphabet", () => {
    const sx = generateRealitySpiderX();
    expect(sx.startsWith("/")).toBe(true);
    expect(/^\/[a-z0-9]+$/.test(sx)).toBe(true);
  });

  it("generateRealityShortIds returns `count` hex ids of length 8 or 16", () => {
    const ids = generateRealityShortIds(5);
    expect(ids).toHaveLength(5);
    for (const id of ids) {
      expect([8, 16]).toContain(id.length);
      expect(/^[0-9a-f]+$/.test(id)).toBe(true);
    }
  });

  it("generateRealityKeyPair returns url-safe base64 keys (no +,/,=)", () => {
    const { privateKey, publicKey } = generateRealityKeyPair();
    for (const k of [privateKey, publicKey]) {
      expect(k.length).toBeGreaterThan(0);
      expect(/[+/=]/.test(k)).toBe(false);
    }
    expect(privateKey).not.toBe(publicKey);
  });
});
