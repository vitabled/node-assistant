import { describe, it, expect } from "vitest";
import {
  emptyRouteDraft, routeToDraft, routePayload, quotaBytes, validateDraft, type RouteDraft,
} from "./routeModel";
import { asList } from "./api";
import type { RouteRecord } from "./contracts";

const GIB = 1024 ** 3;
const TIB = 1024 ** 4;

describe("routeModel · routePayload", () => {
  it("any_tcp → fallback route, no SNIs, tcp target", () => {
    const d: RouteDraft = { ...emptyRouteDraft(), name: "edge", targetMode: "ip", targetHost: "10.0.0.8", targetPort: "443" };
    const p = routePayload(d, true) as any;
    expect(p.fallback).toBe(true);
    expect(p.snis).toEqual([]);
    expect(p.match_mode).toBe("any_tcp");
    expect(p.target_type).toBe("tcp");
    expect(p.target_host).toBe("10.0.0.8");
    expect(p.target_port).toBe(443);
    expect(p.enabled).toBe(true);
    expect(p.quota_bytes).toBeNull();
  });

  it("sni → not fallback, SNIs parsed/lowercased/deduped-order, hostname=first", () => {
    const d: RouteDraft = { ...emptyRouteDraft(), name: "tls", matchMode: "sni",
      snis: "A.Example.com,  b.example.com\n c.example.com.", targetMode: "ip", targetHost: "10.0.0.9" };
    const p = routePayload(d, true) as any;
    expect(p.fallback).toBe(false);
    expect(p.snis).toEqual(["a.example.com", "b.example.com", "c.example.com"]);
    expect(p.hostname).toBe("a.example.com");
  });

  it("unix target → target_type unix, empty host, port 0, socket path", () => {
    const d: RouteDraft = { ...emptyRouteDraft(), name: "sock", targetMode: "unix", unixSocketPath: "/run/x.sock" };
    const p = routePayload(d, false) as any;
    expect(p.target_type).toBe("unix");
    expect(p.target_host).toBe("");
    expect(p.target_port).toBe(0);
    expect(p.unix_socket_path).toBe("/run/x.sock");
    expect(p.enabled).toBe(false);
  });

  it("carries expected_version only when provided", () => {
    const d = { ...emptyRouteDraft(), name: "v", targetHost: "1.2.3.4" };
    expect((routePayload(d, true) as any).expected_version).toBeUndefined();
    expect((routePayload(d, true, 7) as any).expected_version).toBe(7);
  });
});

describe("routeModel · quota", () => {
  it("GiB and TiB conversion", () => {
    expect(quotaBytes({ ...emptyRouteDraft(), quotaEnabled: true, quotaValue: "2", quotaUnit: "GiB" })).toBe(2 * GIB);
    expect(quotaBytes({ ...emptyRouteDraft(), quotaEnabled: true, quotaValue: "1", quotaUnit: "TiB" })).toBe(TIB);
    expect(quotaBytes({ ...emptyRouteDraft(), quotaEnabled: false, quotaValue: "2" })).toBeNull();
  });
});

describe("routeModel · validateDraft", () => {
  const base = { ...emptyRouteDraft(), name: "ok", targetHost: "10.0.0.1" };
  it("passes a valid any_tcp draft", () => expect(validateDraft(base)).toBe(""));
  it("requires a name", () => expect(validateDraft({ ...base, name: "" })).toMatch(/имя/i));
  it("rejects a bad listener port", () => expect(validateDraft({ ...base, listenerPort: "0" })).toMatch(/listener/i));
  it("sni mode needs at least one SNI", () => expect(validateDraft({ ...base, matchMode: "sni", snis: "" })).toMatch(/SNI/i));
  it("any_tcp requires wildcard listener", () => expect(validateDraft({ ...base, listenerIP: "1.2.3.4" })).toMatch(/\*/));
  it("unix needs absolute path", () => expect(validateDraft({ ...base, targetMode: "unix", unixSocketPath: "run/x" })).toMatch(/сокет/i));
});

describe("routeModel · routeToDraft round-trip", () => {
  it("maps a sni RouteRecord back to a draft and payload preserves fields", () => {
    const rec: RouteRecord = {
      id: "r1", node_id: "n1", name: "edge-tls", version: 3, listener_ip: "*", listener_port: 443,
      match_mode: "sni", snis: ["a.example.com"], fallback: false, target_type: "tcp",
      target_host: "10.0.0.8", target_port: 8443, unix_socket_path: "", health_check: true,
      proxy_protocol: "v2", quota_bytes: 2 * TIB, quota_action: "block_new", quota_period: "hourly",
      enabled: true, deployed: true, deployment_state: "deployed", custom_fragment: "timeout server 1h",
    };
    const d = routeToDraft(rec);
    expect(d.matchMode).toBe("sni");
    expect(d.snis).toBe("a.example.com");
    expect(d.targetPort).toBe("8443");
    expect(d.proxyProtocol).toBe("v2");
    expect(d.quotaEnabled).toBe(true);
    expect(d.quotaUnit).toBe("TiB");
    expect(d.quotaValue).toBe("2");
    expect(d.quotaAction).toBe("block_new");
    const p = routePayload(d, true, rec.version) as any;
    expect(p.expected_version).toBe(3);
    expect(p.custom_fragment).toBe("timeout server 1h");
    expect(p.proxy_protocol).toBe("v2");
  });
});

describe("api · asList normalizer", () => {
  it("handles bare array, wrapped object, and null", () => {
    expect(asList([1, 2] as any, "nodes")).toEqual([1, 2]);
    expect(asList({ nodes: [3] } as any, "nodes")).toEqual([3]);
    expect(asList(null, "nodes")).toEqual([]);
    expect(asList({ other: [1] } as any, "nodes")).toEqual([]);
  });
});
