import { describe, expect, it, vi, beforeEach } from "vitest";
import { NODE_COLOR_PRESETS, colorHex, setJobColor, nodeColorLookup, readJobs } from "./nodeColors";

// Wave-4 PR-9: цветовая маркировка нод — пресеты, стор, карта для дашборда.

const KEY = "deploy_jobs_test-acc";

function seed() {
  localStorage.setItem(KEY, JSON.stringify([
    { taskId: "t1", domain: "de.example.com", ip: "1.2.3.4",
      savedForm: { domain: "de.example.com" } },
    { taskId: "t2", domain: "nl.example.com", ip: "5.6.7.8",
      savedForm: { domain: "nl.example.com" } },
  ]));
}

vi.mock("../auth/store", () => ({
  deployJobsKey: () => "deploy_jobs_test-acc",
}));

describe("nodeColors", () => {
  beforeEach(() => localStorage.clear());

  it("пресеты валидны, colorHex резолвит", () => {
    expect(NODE_COLOR_PRESETS).toHaveLength(8);
    for (const p of NODE_COLOR_PRESETS) expect(p.hex).toMatch(/^#[0-9A-F]{6}$/i);
    expect(colorHex("green")).toBe("#3ECF8E");
    expect(colorHex("bogus")).toBeUndefined();
    expect(colorHex(null)).toBeUndefined();
  });

  it("setJobColor пишет и сбрасывает цвет в deploy_jobs", () => {
    seed();
    setJobColor("t1", "violet");
    let jobs = readJobs();
    expect(jobs.find(j => j.taskId === "t1")?.color).toBe("violet");
    expect(jobs.find(j => j.taskId === "t2")?.color).toBeUndefined();
    setJobColor("t1", null);
    jobs = readJobs();
    expect(jobs.find(j => j.taskId === "t1")?.color).toBeUndefined();
  });

  it("nodeColorLookup находит по точному домену, подстроке и IP", () => {
    seed();
    setJobColor("t1", "blue");
    setJobColor("t2", "red");
    const lookup = nodeColorLookup();
    expect(lookup("de.example.com")).toBe("#4C8DFF");
    expect(lookup("DE de.example.com relay")).toBe("#4C8DFF");   // имя содержит домен
    expect(lookup(undefined, "5.6.7.8")).toBe("#F0716E");         // по IP
    expect(lookup("other.example.com")).toBeUndefined();
  });
});
