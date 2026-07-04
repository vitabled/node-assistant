import { beforeEach, describe, expect, it, vi } from "vitest";

// store.ts reads localStorage once at module load, so each test re-imports it
// fresh (after clearing storage) to get an isolated state machine.
async function freshStore() {
  vi.resetModules();
  localStorage.clear();
  return await import("./store");
}

const A = { id: "id-a", login: "alice", token: "tok-a" };
const B = { id: "id-b", login: "bob", token: "tok-b" };

describe("device account store", () => {
  beforeEach(() => localStorage.clear());

  it("addAccount adds, activates and persists", async () => {
    const s = await freshStore();
    s.addAccount(A);
    expect(s.getActiveId()).toBe("id-a");
    expect(s.getActive()).toEqual(A);
    expect(s.getActiveToken()).toBe("tok-a");
    expect(JSON.parse(localStorage.getItem("ni_accounts")!)).toHaveLength(1);
    expect(localStorage.getItem("ni_active_account")).toBe("id-a");
  });

  it("addAccount upserts by id (refreshes token, moves to front) without duplicating", async () => {
    const s = await freshStore();
    s.addAccount(A);
    s.addAccount(B);
    s.addAccount({ ...A, token: "tok-a2" });
    const accounts = s.getSnapshot().accounts;
    expect(accounts).toHaveLength(2);
    expect(accounts[0]).toEqual({ ...A, token: "tok-a2" }); // moved to front, new token
    expect(s.getActiveToken()).toBe("tok-a2");
  });

  it("switchTo changes active only for a known account", async () => {
    const s = await freshStore();
    s.addAccount(A);
    s.addAccount(B); // B now active
    s.switchTo("id-a");
    expect(s.getActiveId()).toBe("id-a");
    s.switchTo("ghost"); // unknown → ignored
    expect(s.getActiveId()).toBe("id-a");
  });

  it("empty state: no accounts → null active, empty token", async () => {
    const s = await freshStore();
    expect(s.getActiveId()).toBeNull();
    expect(s.getActive()).toBeNull();
    expect(s.getActiveToken()).toBe("");
  });

  it("forget on active falls back to first remaining; empties to null", async () => {
    const s = await freshStore();
    s.addAccount(A);
    s.addAccount(B); // B active, order [B, A]
    s.forget("id-b"); // active removed → fall back to remaining
    expect(s.getActiveId()).toBe("id-a");
    s.forget("id-a"); // last one → null
    expect(s.getActiveId()).toBeNull();
    expect(s.getSnapshot().accounts).toHaveLength(0);
    expect(localStorage.getItem("ni_active_account")).toBeNull();
  });

  it("forget on a non-active account leaves active untouched", async () => {
    const s = await freshStore();
    s.addAccount(A);
    s.addAccount(B); // B active
    s.forget("id-a");
    expect(s.getActiveId()).toBe("id-b");
    expect(s.getSnapshot().accounts).toHaveLength(1);
  });

  it("logoutActive forgets the active account", async () => {
    const s = await freshStore();
    s.addAccount(A);
    s.logoutActive();
    expect(s.getActiveId()).toBeNull();
    expect(s.getSnapshot().accounts).toHaveLength(0);
  });

  it("deployJobsKey / tabKey are per active account, 'none' when signed out", async () => {
    const s = await freshStore();
    expect(s.deployJobsKey()).toBe("deploy_jobs_none");
    expect(s.tabKey()).toBe("ni_tab_none");
    s.addAccount(A);
    expect(s.deployJobsKey()).toBe("deploy_jobs_id-a");
    expect(s.tabKey()).toBe("ni_tab_id-a");
    expect(s.deployJobsKey("id-x")).toBe("deploy_jobs_id-x"); // explicit override
  });

  it("persists across a simulated reload (fresh import reads localStorage)", async () => {
    const s1 = await freshStore();
    s1.addAccount(A);
    s1.addAccount(B); // B active
    // Simulate reload: re-import WITHOUT clearing storage.
    vi.resetModules();
    const s2 = await import("./store");
    expect(s2.getActiveId()).toBe("id-b");
    expect(s2.getSnapshot().accounts).toHaveLength(2);
  });

  it("malformed/corrupt localStorage degrades to empty state", async () => {
    localStorage.setItem("ni_accounts", "{not json");
    localStorage.setItem("ni_active_account", "whatever");
    vi.resetModules();
    const s = await import("./store");
    expect(s.getSnapshot().accounts).toEqual([]);
    expect(s.getActiveId()).toBeNull();
  });

  it("drops a dangling active id that isn't in the accounts list", async () => {
    localStorage.setItem("ni_accounts", JSON.stringify([A]));
    localStorage.setItem("ni_active_account", "id-b"); // not present
    vi.resetModules();
    const s = await import("./store");
    expect(s.getActiveId()).toBeNull();
  });

  it("getSnapshot reference is stable until a mutation (useSyncExternalStore contract)", async () => {
    const s = await freshStore();
    const snap1 = s.getSnapshot();
    expect(s.getSnapshot()).toBe(snap1); // same ref, no mutation
    s.addAccount(A);
    expect(s.getSnapshot()).not.toBe(snap1); // new ref after change
  });

  it("subscribe fires on change and unsubscribe stops it", async () => {
    const s = await freshStore();
    const cb = vi.fn();
    const unsub = s.subscribe(cb);
    s.addAccount(A);
    expect(cb).toHaveBeenCalledTimes(1);
    unsub();
    s.addAccount(B);
    expect(cb).toHaveBeenCalledTimes(1); // no further calls
  });

  it("generatePassword yields the requested length, safe charset, and varies", async () => {
    const s = await freshStore();
    const p1 = s.generatePassword(20);
    const p2 = s.generatePassword(20);
    expect(p1).toHaveLength(20);
    expect(s.generatePassword(8)).toHaveLength(8);
    expect(p1).not.toBe(p2); // 20 random chars colliding is effectively impossible
    expect(/^[A-Za-z0-9!@#$%^&*\-_]+$/.test(p1)).toBe(true);
  });
});
