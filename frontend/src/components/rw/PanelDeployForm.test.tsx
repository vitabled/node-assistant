import { describe, it, expect } from "vitest";
import { validatePanelForm, toPayload, PANEL_FORM_DEFAULT, type PanelFormData } from "./PanelDeployForm";

// A fully-valid panel-only form (caddy → no cert provider / email needed).
const validPanel: PanelFormData = {
  ...PANEL_FORM_DEFAULT,
  target: "panel",
  ip: "1.2.3.4",
  ssh_password: "pw",
  panel_domain: "panel.example.com",
};

const validBoth: PanelFormData = {
  ...validPanel,
  target: "both",
  sub_domain: "sub.example.com",
  // Волна 6: обязателен для target subpage/both — без него контейнер страницы
  // подписок падает на старте.
  subpage_api_token: "tok",
};

describe("validatePanelForm", () => {
  it("passes a fully-valid panel form (happy path)", () => {
    expect(validatePanelForm(validPanel)).toEqual({});
  });

  // ── server fields ──
  it("flags a bad ip / missing password / bad port", () => {
    expect(validatePanelForm({ ...validPanel, ip: "999.1.1.1" }).ip).toBeTruthy();
    expect(validatePanelForm({ ...validPanel, ip: "not-ip" }).ip).toBeTruthy();
    expect(validatePanelForm({ ...validPanel, ssh_password: "" }).ssh_password).toBeTruthy();
    expect(validatePanelForm({ ...validPanel, ssh_port: "70000" }).ssh_port).toBeTruthy();
  });

  // ── target-gated domains ──
  it("requires panel_domain for target=panel/both, sub_domain for subpage/both", () => {
    // panel
    expect(validatePanelForm({ ...validPanel, panel_domain: "" }).panel_domain).toBeTruthy();
    // subpage: sub_domain required, panel_domain NOT
    const sub = { ...PANEL_FORM_DEFAULT, target: "subpage" as const, ip: "1.2.3.4",
                  ssh_password: "pw", subpage_api_token: "tok" };
    expect(validatePanelForm(sub).sub_domain).toBeTruthy();
    expect(validatePanelForm(sub).panel_domain).toBeUndefined();
    expect(validatePanelForm({ ...sub, sub_domain: "sub.example.com" })).toEqual({});
    // без токена — ошибка на его собственном поле
    expect(validatePanelForm({ ...sub, sub_domain: "sub.example.com", subpage_api_token: "" })
      .subpage_api_token).toBeTruthy();
    // both: both required
    const both = { ...PANEL_FORM_DEFAULT, target: "both" as const, ip: "1.2.3.4", ssh_password: "pw" };
    expect(validatePanelForm(both).panel_domain).toBeTruthy();
    expect(validatePanelForm(both).sub_domain).toBeTruthy();
    // malformed domain rejected when present
    expect(validatePanelForm({ ...validPanel, panel_domain: "no-tld" }).panel_domain).toBeTruthy();
  });

  it("does NOT flag a malformed panel_domain when the target no longer needs it", () => {
    // target=subpage: panel_domain isn't rendered, so a stale bad value must not
    // silently block submit (regression: format check wasn't gated by wantPanel).
    const sub = { ...PANEL_FORM_DEFAULT, target: "subpage" as const, ip: "1.2.3.4", ssh_password: "pw",
      sub_domain: "sub.example.com", panel_domain: "leftover-bad", subpage_api_token: "tok" };
    expect(validatePanelForm(sub).panel_domain).toBeUndefined();
    expect(validatePanelForm(sub)).toEqual({});
  });

  it("rejects emails the server would 422 (client regex mirrors the server)", () => {
    // These pass a loose /[^\s@]+@.../ but fail the server's strict charset+TLD.
    for (const bad of ["a@b.c1", "user!name@example.com", "a@b_c.com"]) {
      expect(validatePanelForm({ ...validPanel, email: bad }).email, bad).toBeTruthy();
    }
    expect(validatePanelForm({ ...validPanel, email: "ops@example.com" }).email).toBeUndefined();
  });

  // ── nginx cert gating ──
  it("requires cf_api_key ONLY for nginx + cloudflare", () => {
    const base = { ...validPanel, reverse_proxy: "nginx" as const, cf_api_key: "" };
    expect(validatePanelForm({ ...base, cert_provider: "cloudflare" }).cf_api_key).toBeTruthy();
    // le/zerossl don't need cf token (but need email — set it here)
    expect(validatePanelForm({ ...base, cert_provider: "letsencrypt", email: "a@b.co" }).cf_api_key).toBeUndefined();
    // caddy never needs a cf token
    expect(validatePanelForm({ ...validPanel, reverse_proxy: "caddy", cf_api_key: "" }).cf_api_key).toBeUndefined();
  });

  it("rejects a cloudflare token with shell-metachars (nginx)", () => {
    const e = validatePanelForm({ ...validPanel, reverse_proxy: "nginx", cert_provider: "cloudflare", cf_api_key: 'tok"$x' });
    expect(e.cf_api_key).toBeTruthy();
  });

  it("requires email for nginx + letsencrypt/zerossl", () => {
    const base = { ...validPanel, reverse_proxy: "nginx" as const, email: "" };
    expect(validatePanelForm({ ...base, cert_provider: "letsencrypt" }).email).toBeTruthy();
    expect(validatePanelForm({ ...base, cert_provider: "zerossl" }).email).toBeTruthy();
    // caddy → email not required
    expect(validatePanelForm({ ...validPanel, reverse_proxy: "caddy", email: "" }).email).toBeUndefined();
    // valid email clears it
    expect(validatePanelForm({ ...base, cert_provider: "letsencrypt", email: "a@b.co" }).email).toBeUndefined();
  });

  // ── webhooks ──
  it("requires a http(s) webhook_url only when webhooks are enabled", () => {
    expect(validatePanelForm({ ...validPanel, enable_webhooks: true, webhook_url: "" }).webhook_url).toBeTruthy();
    expect(validatePanelForm({ ...validPanel, enable_webhooks: true, webhook_url: "ftp://x" }).webhook_url).toBeTruthy();
    expect(validatePanelForm({ ...validPanel, enable_webhooks: true, webhook_url: "https://ok/webhook" }).webhook_url).toBeUndefined();
    // disabled → ignored even if garbage
    expect(validatePanelForm({ ...validPanel, enable_webhooks: false, webhook_url: "garbage" }).webhook_url).toBeUndefined();
  });

  // ── extra .env ──
  it("validates extra .env keys and blocks protected keys", () => {
    expect(validatePanelForm({ ...validPanel, extra_env: [{ key: "bad-key", value: "x" }] }).extra_env).toBeTruthy();
    expect(validatePanelForm({ ...validPanel, extra_env: [{ key: "JWT_AUTH_SECRET", value: "x" }] }).extra_env).toBeTruthy();
    expect(validatePanelForm({ ...validPanel, extra_env: [{ key: "MY_VAR", value: "ok" }] }).extra_env).toBeUndefined();
    // blank rows are ignored
    expect(validatePanelForm({ ...validPanel, extra_env: [{ key: "", value: "" }] }).extra_env).toBeUndefined();
  });

  // ── separate sub server (target=both only) ──
  it("requires sub_server fields when enabled with target=both", () => {
    const on = { ...validBoth, use_sub_server: true, sub_ip: "", sub_ssh_password: "" };
    expect(validatePanelForm(on).sub_ip).toBeTruthy();
    expect(validatePanelForm(on).sub_ssh_password).toBeTruthy();
    // valid sub server clears it
    const ok = { ...validBoth, use_sub_server: true, sub_ip: "1.2.3.5", sub_ssh_password: "pw" };
    expect(validatePanelForm(ok)).toEqual({});
  });

  it("rejects use_sub_server when target is not both", () => {
    // (UI hides the toggle for non-both, but stale state must fail closed)
    const bad = { ...validPanel, target: "panel" as const, use_sub_server: true };
    expect(validatePanelForm(bad).sub_server).toBeTruthy();
  });
});

describe("toPayload", () => {
  it("coerces ports to numbers and drops blank env rows", () => {
    const p = toPayload({ ...validPanel, ssh_port: "2222", extra_env: [{ key: "A", value: "1" }, { key: "", value: "x" }] });
    expect(p.ssh_port).toBe(2222);
    expect(p.extra_env).toEqual({ A: "1" });
    expect(p.sub_server).toBeNull();
  });

  it("zeroes cf_api_key unless nginx+cloudflare, and sub_domain unless target≠panel", () => {
    // caddy → cf token dropped
    expect(toPayload({ ...validPanel, reverse_proxy: "caddy", cf_api_key: "tok" }).cf_api_key).toBe("");
    // target=panel → sub_domain dropped even if a stale value is present
    expect(toPayload({ ...validPanel, sub_domain: "stale.example.com" }).sub_domain).toBe("");
    // nginx+cloudflare → kept
    expect(toPayload({ ...validPanel, reverse_proxy: "nginx", cert_provider: "cloudflare", cf_api_key: "tok" }).cf_api_key).toBe("tok");
  });

  it("emits sub_server only for target=both + use_sub_server", () => {
    const p = toPayload({ ...validBoth, use_sub_server: true, sub_ip: "1.2.3.5", sub_ssh_password: "pw", sub_ssh_port: "22" });
    expect(p.sub_server).toEqual({ ip: "1.2.3.5", ssh_user: "root", ssh_password: "pw", ssh_port: 22 });
  });
});
