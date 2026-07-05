import { describe, it, expect } from "vitest";
import { validateForm, FORM_DEFAULT, type FormData } from "./DeployForm";

// A fully-valid Remnanode form (manual token, cloudflare provider).
const validRemna: FormData = {
  ...FORM_DEFAULT,
  ip: "1.2.3.4",
  ssh_password: "pw",
  domain: "node1.example.com",
  cert_provider: "cloudflare",
  cloudflare_api_key: "cf-token",
  email: "a@b.co",
  remnanode_token: "eyJ.token",
  country_code: "DE",
};

const validHaproxy: FormData = {
  ...FORM_DEFAULT,
  mode: "haproxy",
  ip: "1.2.3.4",
  ssh_password: "pw",
  haproxy_dest_ip: "10.0.0.5",
};

describe("validateForm", () => {
  it("passes a fully-valid remnanode form (happy path)", () => {
    expect(validateForm(validRemna)).toEqual({});
  });

  it("passes a fully-valid haproxy form", () => {
    expect(validateForm(validHaproxy)).toEqual({});
  });

  // ── empty / required ──
  it("flags empty required fields (ip, password, domain, email, token, country)", () => {
    const e = validateForm({ ...FORM_DEFAULT, ip: "", ssh_password: "" });
    expect(e.ip).toBeTruthy();
    expect(e.ssh_password).toBeTruthy();
    expect(e.domain).toBeTruthy();     // remnanode default has empty domain
    expect(e.email).toBeTruthy();
    expect(e.remnanode_token).toBeTruthy();
    expect(e.country_code).toBeTruthy();
  });

  // ── cert provider gating (the Ф4 change) ──
  it("requires the Cloudflare token ONLY for the cloudflare provider", () => {
    const noToken = { ...validRemna, cloudflare_api_key: "" };
    expect(validateForm({ ...noToken, cert_provider: "cloudflare" }).cloudflare_api_key).toBeTruthy();
    expect(validateForm({ ...noToken, cert_provider: "letsencrypt" }).cloudflare_api_key).toBeUndefined();
    expect(validateForm({ ...noToken, cert_provider: "zerossl" }).cloudflare_api_key).toBeUndefined();
  });

  // ── malformed input ──
  it("rejects malformed ip / domain / email", () => {
    expect(validateForm({ ...validRemna, ip: "999.1.1.1" }).ip).toBeTruthy();
    expect(validateForm({ ...validRemna, ip: "not-an-ip" }).ip).toBeTruthy();
    expect(validateForm({ ...validRemna, domain: "no-tld" }).domain).toBeTruthy();
    expect(validateForm({ ...validRemna, email: "bad@" }).email).toBeTruthy();
  });

  // ── boundary ──
  it("enforces the new-SSH-port boundary (1024–65535) only when change_ssh_port", () => {
    expect(validateForm({ ...validRemna, change_ssh_port: true, new_ssh_port: "80" }).new_ssh_port).toBeTruthy();
    expect(validateForm({ ...validRemna, change_ssh_port: true, new_ssh_port: "70000" }).new_ssh_port).toBeTruthy();
    // off → not validated
    expect(validateForm({ ...validRemna, change_ssh_port: false, new_ssh_port: "80" }).new_ssh_port).toBeUndefined();
  });

  it("rejects bad open_ports", () => {
    expect(validateForm({ ...validRemna, open_ports: "" }).open_ports).toBeTruthy();
    expect(validateForm({ ...validRemna, open_ports: "80,99999" }).open_ports).toBeTruthy();
  });

  // ── whitelist accepts anything (normalized server-side in Ф5) ──
  it("never rejects whitelist_ips, even garbage", () => {
    expect(validateForm({ ...validRemna, whitelist_ips: "" }).whitelist_ips).toBeUndefined();
    expect(validateForm({ ...validRemna, whitelist_ips: "garbage 1.2.3.4 ;; 10/8" }).whitelist_ips).toBeUndefined();
  });

  // ── mode-gated: remnanode-only fields are ignored in haproxy mode ──
  it("does not require domain/email/token/country in haproxy mode", () => {
    const e = validateForm({ ...validHaproxy, domain: "", email: "", cloudflare_api_key: "", country_code: "" });
    expect(e.domain).toBeUndefined();
    expect(e.email).toBeUndefined();
    expect(e.cloudflare_api_key).toBeUndefined();
    expect(e.country_code).toBeUndefined();
    // but haproxy_dest_ip IS required
    expect(validateForm({ ...validHaproxy, haproxy_dest_ip: "" }).haproxy_dest_ip).toBeTruthy();
  });

  // ── remnawave: token not required when auto-registering, template then is ──
  it("swaps token requirement for template when create_in_remnawave is on", () => {
    const auto = { ...validRemna, create_in_remnawave: true, remnanode_token: "", template_id: "" };
    expect(validateForm(auto).remnanode_token).toBeUndefined();
    expect(validateForm(auto).template_id).toBeTruthy();
  });
});
