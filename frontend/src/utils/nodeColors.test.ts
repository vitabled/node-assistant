import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NODE_COLOR_PRESETS, colorHex, setJobColor, nodeColorLookup, readJobs } from "./nodeColors";
import { resetJobs, setJobs } from "../data/deployJobsStore";

// Wave-4 PR-9: цветовая маркировка нод — пресеты, цвет в карточке на сервере,
// карта для дашборда. localStorage в контуре карточек не участвует: список
// приходит из стора (data/deployJobsStore), цвет уходит серверным upsert-ом.

function seed() {
  setJobs([
    { taskId: "t1", domain: "de.example.com", ip: "1.2.3.4", startedAt: 1,
      savedForm: { domain: "de.example.com" } },
    { taskId: "t2", domain: "nl.example.com", ip: "5.6.7.8", startedAt: 1,
      savedForm: { domain: "nl.example.com" } },
  ]);
}

function mockServer() {
  const fn = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job: {} }) });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const posts = (fn: ReturnType<typeof vi.fn>) =>
  fn.mock.calls.filter(c => (c[1] as RequestInit | undefined)?.method === "POST");

describe("nodeColors", () => {
  beforeEach(() => { resetJobs(); localStorage.clear(); vi.unstubAllGlobals(); });
  afterEach(() => { resetJobs(); localStorage.clear(); vi.unstubAllGlobals(); });

  it("пресеты валидны, colorHex резолвит", () => {
    expect(NODE_COLOR_PRESETS).toHaveLength(8);
    for (const p of NODE_COLOR_PRESETS) expect(p.hex).toMatch(/^#[0-9A-F]{6}$/i);
    expect(colorHex("green")).toBe("#3ECF8E");
    expect(colorHex("bogus")).toBeUndefined();
    expect(colorHex(null)).toBeUndefined();
  });

  it("setJobColor пишет цвет на сервер и в снимок, null — сбрасывает", async () => {
    seed();
    const fn = mockServer();

    setJobColor("t1", "violet");
    await vi.waitFor(() => {
      expect(readJobs().find(j => j.taskId === "t1")?.color).toBe("violet");
    });
    expect(readJobs().find(j => j.taskId === "t2")?.color).toBeUndefined();
    expect(posts(fn)).toHaveLength(1);
    expect(JSON.parse(String((posts(fn)[0][1] as RequestInit).body)).color).toBe("violet");

    setJobColor("t1", null);
    await vi.waitFor(() => {
      expect(readJobs().find(j => j.taskId === "t1")?.color).toBeUndefined();
    });
  });

  it("nodeColorLookup находит по точному домену, подстроке и IP", async () => {
    seed();
    mockServer();
    setJobColor("t1", "blue");
    setJobColor("t2", "red");
    await vi.waitFor(() => {
      expect(readJobs().find(j => j.taskId === "t2")?.color).toBe("red");
    });

    const lookup = nodeColorLookup();
    expect(lookup("de.example.com")).toBe("#4C8DFF");
    expect(lookup("DE de.example.com relay")).toBe("#4C8DFF");   // имя содержит домен
    expect(lookup(undefined, "5.6.7.8")).toBe("#F0716E");         // по IP
    expect(lookup("other.example.com")).toBeUndefined();
  });

  it("readJobs не читает localStorage — только серверный снимок", () => {
    localStorage.setItem("deploy_jobs_none", JSON.stringify([{ taskId: "ghost", color: "red" }]));
    expect(readJobs()).toEqual([]);
  });
});
