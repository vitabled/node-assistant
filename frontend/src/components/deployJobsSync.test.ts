import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchDeployJobs, upsertDeployJob, deleteDeployJob, reconcileJobs,
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
