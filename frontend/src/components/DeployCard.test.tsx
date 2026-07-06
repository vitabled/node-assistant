import { describe, it, expect } from "vitest";
import { manageableComponents, opPayload } from "./DeployCard";
import { FORM_DEFAULT, type FormData } from "./DeployForm";

const remna: FormData = { ...FORM_DEFAULT, mode: "remnanode", optimize: true, install_warp: true, install_trafficguard: true };
const haproxy: FormData = { ...FORM_DEFAULT, mode: "haproxy", optimize: true, install_trafficguard: true };

describe("manageableComponents", () => {
  it("lists remnanode components incl warp/ssl/hysteria2 when enabled", () => {
    const ids = manageableComponents(remna).map(c => c.id);
    expect(ids).toEqual([
      "node_accelerator", "trafficguard", "remnanode", "masking", "warp", "ssl", "hysteria2",
    ]);
  });

  it("omits warp when install_warp is off", () => {
    const ids = manageableComponents({ ...remna, install_warp: false }).map(c => c.id);
    expect(ids).not.toContain("warp");
  });

  it("omits node_accelerator when optimize is off and trafficguard when disabled", () => {
    const ids = manageableComponents({ ...remna, optimize: false, install_trafficguard: false }).map(c => c.id);
    expect(ids).not.toContain("node_accelerator");
    expect(ids).not.toContain("trafficguard");
  });

  it("haproxy mode lists haproxy, not remnanode/masking/ssl", () => {
    const ids = manageableComponents(haproxy).map(c => c.id);
    expect(ids).toContain("haproxy");
    expect(ids).not.toContain("remnanode");
    expect(ids).not.toContain("masking");
    expect(ids).not.toContain("ssl");
  });

  it("never exposes the non-manageable steps (connect/update/network SSH ports)", () => {
    const ids = manageableComponents(remna).map(c => c.id);
    for (const forbidden of ["connect", "update", "ssh_port", "reboot"]) {
      expect(ids).not.toContain(forbidden);
    }
  });
});

describe("opPayload", () => {
  it("coerces string ports to ints and nullable tokens", () => {
    const p = opPayload({ ...remna, current_ssh_port: "22", new_ssh_port: "2222", remnanode_port: "2222", remnanode_token: "", plugin_uuid: "" });
    expect(p.current_ssh_port).toBe(22);
    expect(p.new_ssh_port).toBe(2222);
    expect(p.remnanode_port).toBe(2222);
    expect(p.remnanode_token).toBeNull();
    expect(p.plugin_uuid).toBeNull();
    expect(typeof p.haproxy_source_port).toBe("number");
  });
});
