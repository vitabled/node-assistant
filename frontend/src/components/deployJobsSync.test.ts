import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchDeployJobs, upsertDeployJob, deleteDeployJob, reconcileJobs, syncDeployJobs,
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
