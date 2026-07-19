import { describe, it, expect, beforeEach } from "vitest";
import { useConfigStore } from "./configStore";

// getActiveId() is null in the test env → the store persists under this key.
const KEY = "xray_profile_none";

beforeEach(() => {
  localStorage.clear();
  useConfigStore.setState({ config: null, dirty: false });
});

describe("profiles configStore", () => {
  it("setConfig marks dirty AND persists the draft to localStorage", () => {
    const cfg: any = { inbounds: [], outbounds: [] };
    useConfigStore.getState().setConfig(cfg);
    expect(useConfigStore.getState().dirty).toBe(true); // changed-since-sync
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual(cfg); // already saved
  });

  it("markSaved clears the dirty flag without touching the config", () => {
    useConfigStore.getState().setConfig({ inbounds: [], outbounds: [] } as any);
    useConfigStore.getState().markSaved();
    expect(useConfigStore.getState().dirty).toBe(false);
    expect(useConfigStore.getState().config).not.toBeNull();
  });

  it("loadConfig imports external JSON as a clean (non-dirty) draft", () => {
    const res = useConfigStore.getState().loadConfig({ inbounds: [], outbounds: [] });
    expect(res.warnings).toBe(0);
    expect(useConfigStore.getState().dirty).toBe(false);
  });

  it("hydrate reloads the persisted draft for the active account", () => {
    const cfg = { inbounds: [{ tag: "in", protocol: "vless" }], outbounds: [] };
    localStorage.setItem(KEY, JSON.stringify(cfg));
    useConfigStore.getState().hydrate();
    expect(useConfigStore.getState().config).toEqual(cfg);
    expect(useConfigStore.getState().dirty).toBe(false);
  });

  it("addOutbounds de-duplicates colliding tags", () => {
    const s = useConfigStore.getState();
    s.setConfig({ inbounds: [], outbounds: [{ tag: "dup", protocol: "vless" }] } as any);
    s.addOutbounds([{ tag: "dup", protocol: "trojan" } as any]);
    const tags = useConfigStore.getState().config!.outbounds!.map((o: any) => o.tag);
    expect(tags[0]).toBe("dup");
    expect(tags[1]).not.toBe("dup"); // second one got a unique suffix
    expect(new Set(tags).size).toBe(tags.length);
  });

  it("clearConfig removes the draft from localStorage", () => {
    useConfigStore.getState().setConfig({ inbounds: [], outbounds: [] } as any);
    useConfigStore.getState().clearConfig();
    expect(useConfigStore.getState().config).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();
  });
});
