// Список карточек нод изолирован на сервере: эти тесты фиксируют, что
// localStorage НИКАК не влияет на карточки — ни при чтении, ни при записи.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deployJobsKey } from "../auth/store";
import {
  getJobs, refreshJobs, removeJob, resetJobs, saveJob, setJobs, subscribeJobs,
} from "./deployJobsStore";

const job = (taskId: string, extra: Record<string, unknown> = {}) => ({
  taskId,
  domain: `${taskId}.example`,
  ip: "1.2.3.4",
  newSshPort: 2222,
  startedAt: 1,
  savedForm: {},
  ...extra,
});

const jsonOk = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

/** fetch-мок: GET /api/deploy-jobs отдаёт `jobs`, POST отвечает `postResponse`. */
function mockApi(jobsSequence: unknown[][], opts: { postResponse?: unknown; postFails?: boolean } = {}) {
  let getIndex = 0;
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/api/deploy-jobs") && method === "GET") {
      const list = jobsSequence[Math.min(getIndex, jobsSequence.length - 1)];
      getIndex += 1;
      return jsonOk({ jobs: list });
    }
    if (method === "POST") {
      if (opts.postFails) throw new Error("500");
      return jsonOk(opts.postResponse ?? { job: {} });
    }
    if (method === "DELETE") return jsonOk({ ok: true });
    return jsonOk({});
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const posts = (fn: ReturnType<typeof vi.fn>) =>
  fn.mock.calls.filter(call => (call[1] as RequestInit | undefined)?.method === "POST");

beforeEach(() => {
  resetJobs();
  try { localStorage.clear(); } catch { /* нет storage — не мешает */ }
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetJobs();
  try { localStorage.clear(); } catch { /* ignore */ }
});

describe("сервер — единственный источник", () => {
  it("берёт список с сервера и НЕ подмешивает карточки из localStorage", async () => {
    localStorage.setItem(deployJobsKey(), JSON.stringify([job("local-stale")]));
    mockApi([[job("server-1"), job("server-2")]]);

    const jobs = await refreshJobs();

    expect(jobs.map(j => j.taskId)).toEqual(["server-1", "server-2"]);
    expect(getJobs().map(j => j.taskId)).toEqual(["server-1", "server-2"]);
  });

  it("после ответа сервера локальный ключ карточек удаляется", async () => {
    localStorage.setItem(deployJobsKey(), JSON.stringify([job("local-stale")]));
    mockApi([[job("server-1")]]);

    await refreshJobs();

    expect(localStorage.getItem(deployJobsKey())).toBeNull();
  });

  it("при недоступном сервере список пуст — localStorage не подставляется", async () => {
    localStorage.setItem(deployJobsKey(), JSON.stringify([job("local-stale")]));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));

    await expect(refreshJobs()).rejects.toThrow("network down");

    expect(getJobs()).toEqual([]);
  });

  it("не пишет карточки в localStorage при загрузке", async () => {
    mockApi([[job("server-1")]]);
    await refreshJobs();
    expect(localStorage.getItem(deployJobsKey())).toBeNull();
  });
});

describe("одноразовая миграция старого кэша", () => {
  it("заливает локальные карточки на сервер, когда сервер пуст, и перечитывает список", async () => {
    localStorage.setItem(deployJobsKey(), JSON.stringify([job("legacy-1"), job("legacy-2")]));
    // первый GET — пусто, второй (после заливки) — уже обе карточки
    const fn = mockApi([[], [job("legacy-1"), job("legacy-2")]]);

    const jobs = await refreshJobs();

    expect(posts(fn)).toHaveLength(2);
    expect(jobs.map(j => j.taskId).sort()).toEqual(["legacy-1", "legacy-2"]);
    expect(localStorage.getItem(deployJobsKey())).toBeNull();
  });

  it("не перезаливает кэш, если на сервере уже есть карточки", async () => {
    localStorage.setItem(deployJobsKey(), JSON.stringify([job("local-stale")]));
    const fn = mockApi([[job("server-1")]]);

    await refreshJobs();

    expect(posts(fn)).toHaveLength(0);
  });

  it("выполняется один раз: повторный refresh не заливает вернувшийся кэш", async () => {
    localStorage.setItem(deployJobsKey(), JSON.stringify([job("legacy-1")]));
    const fn = mockApi([[], [job("legacy-1")], [job("legacy-1"), job("late")]]);
    await refreshJobs();
    const postsAfterMigration = posts(fn).length;
    expect(postsAfterMigration).toBeGreaterThan(0);

    // кэш «вернулся» (старый браузер/расширение) — но миграция уже была
    localStorage.setItem(deployJobsKey(), JSON.stringify([job("legacy-1"), job("late")]));
    await refreshJobs();

    expect(posts(fn)).toHaveLength(postsAfterMigration);
  });
});

describe("мутации идут на сервер", () => {
  it("saveJob пишет карточку в снимок после успешного POST", async () => {
    mockApi([[job("server-1")]]);
    await refreshJobs();

    await saveJob(job("new-1"));

    expect(getJobs().map(j => j.taskId)).toEqual(["server-1", "new-1"]);
    expect(localStorage.getItem(deployJobsKey())).toBeNull();
  });

  it("removeJob убирает карточку из снимка (DELETE)", async () => {
    mockApi([[job("server-1"), job("server-2")]]);
    await refreshJobs();

    await removeJob("server-1");

    expect(getJobs().map(j => j.taskId)).toEqual(["server-2"]);
  });

  it("ignored:true (tombstone) выкидывает карточку из снимка", async () => {
    mockApi([[job("server-1"), job("dead")]], { postResponse: { ignored: true } });
    await refreshJobs();

    const accepted = await saveJob(job("dead"));

    expect(accepted).toBe(false);
    expect(getJobs().map(j => j.taskId)).toEqual(["server-1"]);
  });

  it("ошибка POST пробрасывается, снимок не меняется", async () => {
    mockApi([[job("server-1")]], { postFails: true });
    await refreshJobs();

    await expect(saveJob(job("new-1"))).rejects.toThrow("500");
    expect(getJobs().map(j => j.taskId)).toEqual(["server-1"]);
  });
});

describe("подписка", () => {
  it("уведомляет подписчиков при каждом обновлении снимка", () => {
    const seen: number[] = [];
    const off = subscribeJobs(() => seen.push(getJobs().length));

    setJobs([job("a")]);
    setJobs([job("a"), job("b")]);
    off();
    setJobs([]);

    expect(seen).toEqual([1, 2]);
  });
});
