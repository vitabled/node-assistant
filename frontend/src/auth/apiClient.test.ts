import { describe, expect, it, vi } from "vitest";

// Build a fresh module graph: mock original fetch, install the interceptor over
// it, and hand back the store + the captured mock so tests can assert headers.
async function setup(status = 200) {
  vi.resetModules();
  localStorage.clear();
  const store = await import("./store");
  const { installApiClient } = await import("./apiClient");
  const original = vi.fn(async () => new Response("{}", { status }));
  // installApiClient captures window.fetch as the "original" at call time.
  (globalThis as unknown as { fetch: typeof fetch }).fetch = original as unknown as typeof fetch;
  installApiClient();
  return { store, original };
}

function headerOf(original: ReturnType<typeof vi.fn>, i = 0): Headers {
  const init = original.mock.calls[i][1] ?? {};
  return new Headers(init.headers);
}

const ACC = { id: "id-a", login: "alice", token: "TKN" };

describe("auth fetch interceptor", () => {
  it("attaches the active bearer token to /api requests", async () => {
    const { store, original } = await setup();
    store.addAccount(ACC);
    await window.fetch("/api/settings");
    expect(headerOf(original).get("Authorization")).toBe("Bearer TKN");
  });

  it("signed out: no Authorization header added", async () => {
    const { original } = await setup();
    await window.fetch("/api/settings");
    expect(headerOf(original).has("Authorization")).toBe(false);
  });

  it("does not attach a token to /api/auth/* routes", async () => {
    const { store, original } = await setup();
    store.addAccount(ACC);
    await window.fetch("/api/auth/login", { method: "POST" });
    expect(headerOf(original).has("Authorization")).toBe(false);
  });

  it("leaves non-/api requests untouched", async () => {
    const { store, original } = await setup();
    store.addAccount(ACC);
    await window.fetch("/assets/logo.svg");
    expect(headerOf(original).has("Authorization")).toBe(false);
  });

  it("preserves an explicit Authorization header (no overwrite)", async () => {
    const { store, original } = await setup();
    store.addAccount(ACC);
    await window.fetch("/api/settings", { headers: { Authorization: "Bearer custom" } });
    expect(headerOf(original).get("Authorization")).toBe("Bearer custom");
  });

  it("attaches the token when the input is a Request object", async () => {
    const { store, original } = await setup();
    store.addAccount(ACC);
    await window.fetch(new Request("http://localhost/api/nodes"));
    expect(headerOf(original).get("Authorization")).toBe("Bearer TKN");
  });

  it("401 on a protected /api route forgets the active session", async () => {
    const { store } = await setup(401);
    store.addAccount(ACC);
    expect(store.getActiveId()).toBe("id-a");
    await window.fetch("/api/settings");
    expect(store.getActiveId()).toBeNull(); // logged out
  });

  it("401 on an /api/auth route does NOT force logout", async () => {
    const { store } = await setup(401);
    store.addAccount(ACC);
    await window.fetch("/api/auth/login", { method: "POST" });
    expect(store.getActiveId()).toBe("id-a"); // still signed in
  });

  it("returns the original response unchanged", async () => {
    const { store } = await setup(200);
    store.addAccount(ACC);
    const res = await window.fetch("/api/settings");
    expect(res.status).toBe(200);
  });
});
