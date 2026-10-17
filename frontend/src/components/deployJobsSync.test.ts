import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchDeployJobs, upsertDeployJob, deleteDeployJob, reconcileJobs, syncDeployJobs,
  fetchDeployJobTombstones, syncRemnawaveCards,
} from "./deployJobsSync";

const j = (taskId: string) => ({
  taskId, domain: `${taskId}.example`, ip: "1.2.3.4", newSshPort: 2222, startedAt: 1, savedForm: {},
});

describe("reconcileJobs", () => {
  it("returns server jobs first, then local-only pending cards", () => {
    const { merged, localOnly } = reconcileJobs([j("s1"), j("s2")], [j("s2"), j("local")]);
    expect(merged.map(x => x.taskId)).toEqual(["s1", "s2", "local"]);
    expect(localOnly.map(x => x.taskId)).toEqual(["local"]);
  });

  it("server wins when the same taskId is on both lists", () => {
    const server = { ...j("t1"), domain: "server.example" };
    const local = { ...j("t1"), domain: "local.example" };
    const { merged, localOnly } = reconcileJobs([server], [local]);
    expect(merged).toEqual([server]);
    expect(localOnly).toEqual([]);
  });
});

describe("wire calls", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("fetchDeployJobs returns the jobs array", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ jobs: [j("t1")] }) }));
    expect(await fetchDeployJobs()).toEqual([j("t1")]);
  });

  it("fetchDeployJobs returns [] when the body has no jobs array", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
    expect(await fetchDeployJobs()).toEqual([]);
  });

  it("upsertDeployJob POSTs a single job", async () => {
    const fn = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fn);
    await upsertDeployJob(j("t1"));
    const [url, init] = fn.mock.calls[0];
    expect(String(url)).toContain("/api/deploy-jobs");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual(j("t1"));
  });

  it("deleteDeployJob DELETEs by taskId", async () => {
    const fn = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fn);
    await deleteDeployJob("t1");
    const [url, init] = fn.mock.calls[0];
    expect(String(url)).toContain("/api/deploy-jobs/t1");
    expect((init as RequestInit).method).toBe("DELETE");
  });

  it("deleteDeployJob treats a 404 as success (idempotent)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }));
    await expect(deleteDeployJob("gone")).resolves.toBeUndefined();
  });
});

describe("syncDeployJobs", () => {
  afterEach(() => vi.unstubAllGlobals());

  const ids = (arr: { taskId: string }[]) => arr.map(x => x.taskId);
  const deletes = (m: ReturnType<typeof vi.fn>) =>
    m.mock.calls.filter(c => (c[1] as RequestInit | undefined)?.method === "DELETE");

  it("merges server cards into a short local cache and never DELETEs", async () => {
    const server = [j("s1"), j("s2"), j("s3")];
    const local = [j("s1")]; // stale/short local cache
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ jobs: server }) });
    vi.stubGlobal("fetch", fetchMock);

    const saved: { taskId: string }[][] = [];
    const res = await syncDeployJobs({
      loadLocal: () => local,
      saveLocal: jobs => { saved.push(jobs); },
    });

    expect(res.offline).toBe(false);
    expect(res.pushFailed).toBe(false);
    // merged list contains every server card
    expect(ids(res.jobs)).toEqual(["s1", "s2", "s3"]);
    // cache was written once with the full merged list (server cards ADDED)
    expect(saved).toHaveLength(1);
    expect(ids(saved[0])).toEqual(["s1", "s2", "s3"]);
    // no DELETE ever issued
    expect(deletes(fetchMock)).toHaveLength(0);
  });

  it("uploads a local-only card to the server (POST)", async () => {
    const server = [j("s1")];
    const local = [j("s1"), j("local")];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ jobs: server }) });
    vi.stubGlobal("fetch", fetchMock);

    const res = await syncDeployJobs({ loadLocal: () => local, saveLocal: () => {} });

    expect(res.pushFailed).toBe(false);
    expect(ids(res.jobs)).toEqual(["s1", "local"]);
    const posts = fetchMock.mock.calls.filter(c => (c[1] as RequestInit | undefined)?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0][1] && (posts[0][1] as RequestInit).body))).toEqual(j("local"));
    expect(deletes(fetchMock)).toHaveLength(0);
  });

  it("never overwrites an existing server card with the local copy (server creds survive)", async () => {
    const server = [{ ...j("t1"), savedForm: { ssh_password: "SERVER-X" } }];
    const local  = [{ ...j("t1"), savedForm: { ssh_password: "LOCAL-Y" } }];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ jobs: server }) });
    vi.stubGlobal("fetch", fetchMock);

    const res = await syncDeployJobs({ loadLocal: () => local, saveLocal: () => {} });

    // The server already has this taskId, so the local copy (with password Y)
    // is NOT re-uploaded. Server password X survives; Y never reaches the server.
    const posts = fetchMock.mock.calls.filter(c => (c[1] as RequestInit | undefined)?.method === "POST");
    expect(posts).toHaveLength(0);
    expect(res.jobs).toEqual(server);
    expect((res.jobs[0].savedForm as { ssh_password?: string }).ssh_password).toBe("SERVER-X");
  });

  it("uploads a local-only card (not on the server) to the server", async () => {
    const server = [j("s1")];
    const localNew = { ...j("local"), savedForm: { ssh_password: "NEW-PW" } };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ jobs: server }) });
    vi.stubGlobal("fetch", fetchMock);

    await syncDeployJobs({ loadLocal: () => [localNew], saveLocal: () => {} });

    const posts = fetchMock.mock.calls.filter(c => (c[1] as RequestInit | undefined)?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String((posts[0][1] as RequestInit).body))).toEqual(localNew);
    expect(deletes(fetchMock)).toHaveLength(0);
  });

  it("degrades to the local cache when the API is down, without deleting", async () => {
    const local = [j("a"), j("b")];
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const res = await syncDeployJobs({ loadLocal: () => local, saveLocal: () => {} });

    expect(res.offline).toBe(true);
    expect(res.jobs).toEqual(local); // local data preserved
    expect(deletes(fetchMock)).toHaveLength(0);
  });

  it("keeps a local-only card in the cache when its upload fails", async () => {
    const server = [j("s1")];
    const local = [j("local")];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ jobs: server }) }) // GET
      .mockRejectedValueOnce(new Error("500"));                                   // POST fails
    vi.stubGlobal("fetch", fetchMock);

    const saved: { taskId: string }[][] = [];
    const res = await syncDeployJobs({
      loadLocal: () => local,
      saveLocal: jobs => { saved.push(jobs); },
    });

    expect(res.offline).toBe(false);
    expect(res.pushFailed).toBe(true);
    expect(ids(res.jobs)).toEqual(["s1", "local"]);
    expect(ids(saved[0])).toEqual(["s1", "local"]); // pending card not lost
    expect(deletes(fetchMock)).toHaveLength(0);
  });
});

describe("tombstones", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("fetchDeployJobTombstones returns the taskIds array", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ taskIds: ["a", "b"] }) }));
    expect(await fetchDeployJobTombstones()).toEqual(["a", "b"]);
  });

  it("fetchDeployJobTombstones returns [] when the body has no taskIds", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
    expect(await fetchDeployJobTombstones()).toEqual([]);
  });

  it("drops a tombstoned card from the local cache and never uploads it back", async () => {
    const server = [j("keep")];
    const local = [j("keep"), j("dead")]; // "dead" was deleted on the server
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/tombstones")) return { ok: true, json: async () => ({ taskIds: ["dead"] }) };
      if ((init?.method ?? "GET").toUpperCase() === "GET") return { ok: true, json: async () => ({ jobs: server }) };
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    const saved: { taskId: string }[][] = [];
    const res = await syncDeployJobs({ loadLocal: () => local, saveLocal: j => { saved.push(j); } });

    expect(res.jobs.map(x => x.taskId)).toEqual(["keep"]);
    expect(saved[0].map(x => x.taskId)).toEqual(["keep"]);
    // the tombstoned card is never POSTed back (that was the resurrection bug)
    expect(fetchMock.mock.calls.filter(c => (c[1] as RequestInit | undefined)?.method === "POST")).toHaveLength(0);
  });

  it("still syncs when the tombstones route is unavailable (old backend)", async () => {
    const local = [j("local")];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/tombstones")) throw new Error("404");
      if ((init?.method ?? "GET").toUpperCase() === "GET") return { ok: true, json: async () => ({ jobs: [] }) };
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await syncDeployJobs({ loadLocal: () => local, saveLocal: () => {} });

    expect(res.offline).toBe(false);
    expect(res.pushFailed).toBe(false);
    expect(res.jobs.map(x => x.taskId)).toEqual(["local"]);
  });
});

describe("syncRemnawaveCards", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs dry_run and returns the report", async () => {
    const report = {
      ok: true, dry_run: true, added: [{ taskId: "rw-1", ip: "1.2.3.4", domain: "" }],
      removed: [], skipped: [], nodes_total: 1, cards_before: 0, cards_after: 1,
    };
    const fn = vi.fn().mockResolvedValue({ ok: true, json: async () => report });
    vi.stubGlobal("fetch", fn);

    expect(await syncRemnawaveCards(true)).toEqual(report);
    const [url, init] = fn.mock.calls[0];
    expect(String(url)).toContain("/api/deploy-jobs/sync-remnawave");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ dry_run: true });
  });

  it("throws on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 400 }));
    await expect(syncRemnawaveCards(false)).rejects.toThrow("sync-remnawave 400");
  });
});
