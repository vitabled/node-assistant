import { describe, it, expect } from "vitest";
import { validate, type CertsFormData } from "./CertsForm";

const base: CertsFormData = {
  ip: "1.2.3.4", ssh_user: "root", ssh_password: "pw", ssh_port: "22",
  domain: "node1.example.com", cert_provider: "cloudflare", email: "", cf_api_key: "cf", force: false,
};

describe("CertsForm.validate", () => {
  it("passes a valid cloudflare form", () => {
    expect(validate(base)).toEqual({});
  });

  it("requires the CF token only for cloudflare", () => {
    expect(validate({ ...base, cf_api_key: "" }).cf_api_key).toBeTruthy();
    // non-cloudflare doesn't need the CF token (but needs email)
    expect(validate({ ...base, cert_provider: "letsencrypt", cf_api_key: "", email: "a@b.co" }).cf_api_key).toBeUndefined();
  });

  it("requires email for letsencrypt/zerossl, not cloudflare", () => {
    expect(validate({ ...base, cert_provider: "letsencrypt", email: "" }).email).toBeTruthy();
    expect(validate({ ...base, cert_provider: "zerossl", email: "bad" }).email).toBeTruthy();
    expect(validate({ ...base, cert_provider: "zerossl", email: "a@b.co" }).email).toBeUndefined();
    // cloudflare: no email needed
    expect(validate({ ...base, email: "" }).email).toBeUndefined();
  });

  it("rejects malformed ip / domain / port", () => {
    expect(validate({ ...base, ip: "999.1.1.1" }).ip).toBeTruthy();
    expect(validate({ ...base, domain: "no-tld" }).domain).toBeTruthy();
    expect(validate({ ...base, ssh_port: "0" }).ssh_port).toBeTruthy();
    expect(validate({ ...base, ssh_port: "70000" }).ssh_port).toBeTruthy();
  });

  it("flags empty required connection fields", () => {
    const e = validate({ ...base, ip: "", ssh_password: "" });
    expect(e.ip).toBeTruthy();
    expect(e.ssh_password).toBeTruthy();
  });
});
