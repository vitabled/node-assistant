import { describe, it, expect } from "vitest";
import { parseXrayLink, parseWireguardConfig, generateXrayLink } from "./links";

describe("profiles/core links", () => {
  it("parses a vless:// link into an outbound", () => {
    const ob: any = parseXrayLink("vless://11111111-1111-1111-1111-111111111111@node.example.com:443?security=reality&pbk=KEY&sni=microsoft.com&fp=chrome#Node1");
    expect(ob).not.toBeNull();
    expect(ob.protocol).toBe("vless");
    expect(ob.tag).toBe("Node1");
    expect(ob.settings.vnext[0].address).toBe("node.example.com");
    expect(ob.settings.vnext[0].port).toBe(443);
    expect(ob.streamSettings.security).toBe("reality");
    expect(ob.streamSettings.realitySettings.publicKey).toBe("KEY");
  });

  it("returns null for unparseable input", () => {
    expect(parseXrayLink("this is not a link")).toBeNull();
  });

  it("round-trips a vless outbound back into a vless:// link", () => {
    const ob = { tag: "T", protocol: "vless", settings: { vnext: [{ address: "1.2.3.4", port: 443, users: [{ id: "abc" }] }] }, streamSettings: { network: "tcp", security: "none" } };
    expect(generateXrayLink(ob).startsWith("vless://abc@1.2.3.4:443")).toBe(true);
  });

  it("parses a WireGuard .conf into a wireguard outbound", () => {
    const conf = "[Interface]\nPrivateKey = AAAA\nAddress = 10.0.0.2/32\n[Peer]\nPublicKey = BBBB\nEndpoint = 1.2.3.4:51820";
    const ob: any = parseWireguardConfig(conf);
    expect(ob).not.toBeNull();
    expect(ob.protocol).toBe("wireguard");
    expect(ob.settings.secretKey).toBe("AAAA");
    expect(ob.settings.peers[0].publicKey).toBe("BBBB");
  });

  it("returns null for a WireGuard config without a private key", () => {
    expect(parseWireguardConfig("[Peer]\nPublicKey = X")).toBeNull();
  });

  it("parses a vmess:// link (base64 JSON) into a vmess outbound", () => {
    const vmess = {
      v: "2", ps: "MyVmess", add: "vm.example.com", port: "443",
      id: "22222222-2222-2222-2222-222222222222", aid: "0", scy: "auto",
      net: "ws", host: "vm.example.com", path: "/ray", tls: "tls", sni: "vm.example.com",
    };
    const link = "vmess://" + btoa(JSON.stringify(vmess));
    const ob: any = parseXrayLink(link);
    expect(ob).not.toBeNull();
    expect(ob.protocol).toBe("vmess");
    expect(ob.tag).toBe("MyVmess");
    expect(ob.settings.vnext[0].address).toBe("vm.example.com");
    expect(ob.settings.vnext[0].port).toBe(443);
    expect(ob.settings.vnext[0].users[0].id).toBe("22222222-2222-2222-2222-222222222222");
    expect(ob.streamSettings.security).toBe("tls");
    expect(ob.streamSettings.network).toBe("ws");
    expect(ob.streamSettings.wsSettings.path).toBe("/ray");
  });

  it("round-trips a vmess outbound through generate → parse", () => {
    const ob = { tag: "RT", protocol: "vmess", port: 8443, settings: { vnext: [{ address: "9.9.9.9", port: 8443, users: [{ id: "abc-id" }] }] }, streamSettings: { network: "tcp", security: "none" } };
    const link = generateXrayLink(ob as any);
    expect(link.startsWith("vmess://")).toBe(true);
    const back: any = parseXrayLink(link);
    expect(back.protocol).toBe("vmess");
    expect(back.settings.vnext[0].address).toBe("9.9.9.9");
    expect(back.settings.vnext[0].port).toBe(8443);
  });

  it("returns null for a vmess:// link with malformed base64", () => {
    expect(parseXrayLink("vmess://!!!not-base64!!!")).toBeNull();
  });
});
