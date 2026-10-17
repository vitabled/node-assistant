import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchDeployJobs, upsertDeployJob, deleteDeployJob, syncRemnawaveCards,
} from "./deployJobsSync";

const j = (taskId: string) => ({
  taskId, domain: `${taskId}.example`, ip: "1.2.3.4", newSshPort: 2222, startedAt: 1, savedForm: {},
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

  it("fetchDeployJobs throws on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
    await expect(fetchDeployJobs()).rejects.toThrow("deploy-jobs GET 500");
  });

  it("upsertDeployJob POSTs a single job and reports a normal write", async () => {
    const fn = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job: j("t1") }) });
    vi.stubGlobal("fetch", fn);
    await expect(upsertDeployJob(j("t1"))).resolves.toEqual({ ignored: false });
    const [url, init] = fn.mock.calls[0];
    expect(String(url)).toContain("/api/deploy-jobs");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual(j("t1"));
  });

  it("upsertDeployJob reports ignored:true when the server refused a tombstoned card", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ignored: true }) }));
    await expect(upsertDeployJob(j("dead"))).resolves.toEqual({ ignored: true });
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
