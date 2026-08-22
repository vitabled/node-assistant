import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  load, save, sessionsKey, listSessions, getActive, setActive, newSession,
  clearActive, replaceMessages, appendMessages, renameActive, removeSession,
  fetchAll, pushAppend, pushReplace, pushDelete, syncFromServer,
  type Msg, type SessionsState,
} from "./aiSessions";

const user = (text: string): Msg => ({ role: "user", text });
const bot = (text: string): Msg => ({ role: "assistant", text, tools: [] });

/** N ходов подряд в активной сессии. */
function fill(state: SessionsState, n: number): SessionsState {
  let s = state;
  for (let i = 0; i < n; i++) s = appendMessages(s, [user(`q${i}`)]);
  return s;
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("aiSessions", () => {
  it("always yields an active session, even from an empty store", () => {
    const s = load(null);
    expect(s.sessions).toHaveLength(1);
    expect(getActive(s).id).toBe(s.activeId);
    expect(getActive(s).messages).toEqual([]);
  });

  it("round-trips a conversation through localStorage", () => {
    const s = appendMessages(load("u1"), [user("привет"), bot("здравствуйте")]);
    save("u1", s);
    expect(getActive(load("u1")).messages).toEqual([user("привет"), bot("здравствуйте")]);
  });

  // Ключ по личности: за одним браузером работают разные пользователи панели.
  it("keeps each user's conversations apart", () => {
    save("u1", appendMessages(load("u1"), [user("секрет u1")]));
    expect(getActive(load("u2")).messages).toEqual([]);
    expect(localStorage.getItem(sessionsKey("u2"))).toBeNull();
    expect(sessionsKey(null)).toBe("ai_sessions_none");
  });

  it("titles a session from the first user message", () => {
    const long = "ю".repeat(80);
    const s = appendMessages(load(null), [user(long), bot("ок")]);
    expect(getActive(s).title).toHaveLength(40);
    // Ответ ассистента заголовком не становится.
    expect(getActive(appendMessages(load(null), [bot("привет")])).title).toBe("");
  });

  it("keeps the title stable once it is set", () => {
    let s = appendMessages(load(null), [user("первый")]);
    s = appendMessages(s, [user("второй")]);
    expect(getActive(s).title).toBe("первый");
    expect(getActive(renameActive(s, "мой разговор")).title).toBe("мой разговор");
  });

  it("clears the active session without dropping it", () => {
    const s = clearActive(appendMessages(load(null), [user("q"), bot("a")]));
    expect(s.sessions).toHaveLength(1);
    expect(getActive(s).messages).toEqual([]);
    // Заголовок выведен из стёртого сообщения — иначе он врал бы про содержимое.
    expect(getActive(s).title).toBe("");
  });

  it("caps a session at 200 messages, keeping the recent tail", () => {
    const s = fill(load(null), 250);
    const msgs = getActive(s).messages;
    expect(msgs).toHaveLength(200);
    expect(msgs[0].text).toBe("q50");
    expect(msgs[199].text).toBe("q249");
    // replaceMessages подчиняется тому же потолку.
    expect(getActive(replaceMessages(s, Array.from({ length: 300 }, (_, i) => user(`x${i}`)))).messages)
      .toHaveLength(200);
  });

  it("caps the store at 20 sessions and never evicts the open one", () => {
    let s = load(null);
    for (let i = 0; i < 24; i++) s = appendMessages(newSession(s), [user(`s${i}`)]);
    expect(s.sessions).toHaveLength(20);
    expect(s.sessions.some(x => x.id === s.activeId)).toBe(true);
    // Вытесняются самые старые: первые разговоры не пережили лимит.
    const titles = listSessions(s).map(x => x.title);
    expect(titles[0]).toBe("s23");
    expect(titles).not.toContain("s0");
  });

  it("switches, lists newest-first and deletes", () => {
    const first = load(null);
    const firstId = first.activeId;
    let s = appendMessages(newSession(appendMessages(first, [user("старый")])), [user("новый")]);
    expect(listSessions(s).map(x => x.title)).toEqual(["новый", "старый"]);

    s = setActive(s, firstId);
    expect(getActive(s).title).toBe("старый");
    expect(setActive(s, "нет такой").activeId).toBe(firstId); // неизвестный id не двигает выбор

    s = removeSession(s, firstId);
    expect(s.sessions).toHaveLength(1);
    expect(getActive(s).title).toBe("новый");
  });

  it("re-creates an empty session when the last one is deleted", () => {
    const start = appendMessages(load(null), [user("q")]);
    const after = removeSession(start, start.activeId);
    expect(after.sessions).toHaveLength(1);
    expect(getActive(after).messages).toEqual([]);
  });

  // Ключ правится руками и переживает смену формата.
  it("survives garbage in storage", () => {
    localStorage.setItem(sessionsKey("u1"), "{не json");
    expect(load("u1").sessions).toHaveLength(1);
    localStorage.setItem(sessionsKey("u1"), JSON.stringify({
      sessions: [{ id: "a", messages: [{ role: "user", text: "ок" }, { role: "нет" }, 42] }, { nope: 1 }],
      activeId: "призрак",
    }));
    const s = load("u1");
    expect(s.sessions).toHaveLength(1);
    expect(s.activeId).toBe("a");
    expect(getActive(s).messages).toEqual([user("ок")]);
  });

  // Квота ~5 МБ делится с карточками деплоя и профилями Xray: переполнение не
  // должно ронять чат.
  it("swallows a quota error instead of throwing", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceededError"); });
    expect(() => save("u1", load("u1"))).not.toThrow();
  });
});

// ── сервер как источник истины ─────────────────────────────────

/** Мок сети: `sessions` = что «лежит на сервере», `fail` = сервер недоступен. */
function mockNet(sessions: any[] | null, fail = false) {
  const fn = vi.fn(async (url: string, opts?: any) => {
    if (fail) throw new Error("network down");
    if (opts?.method === "POST" || opts?.method === "DELETE")
      return { ok: true, json: async () => ({ ok: true }) } as any;
    return { ok: true, json: async () => ({ sessions: sessions ?? [] }) } as any;
  });
  (globalThis as any).fetch = fn;
  return fn;
}

const wire = (fn: any) =>
  fn.mock.calls.filter(([, o]: any[]) => o?.method === "POST").map(([, o]: any[]) => JSON.parse(o.body));

describe("aiSessions ↔ сервер", () => {
  it("reads conversations back from the server", async () => {
    mockNet([{
      session_id: "s1", updated_at: 1_700_000_000,
      messages: [{ role: "user", content: "привет" },
                 { role: "assistant", content: "здравствуйте",
                   tools: [{ name: "panel_get", ok: true }] }],
    }]);
    const got = await fetchAll();
    expect(got).toHaveLength(1);
    expect(got![0].id).toBe("s1");
    expect(got![0].messages).toEqual([
      { role: "user", text: "привет" },
      { role: "assistant", text: "здравствуйте", tools: [{ id: undefined, name: "panel_get", ok: true }] },
    ]);
    // Заголовок восстанавливается из первой реплики пользователя — сервер его
    // не хранит, и без этого список разговоров стал бы безымянным.
    expect(got![0].title).toBe("привет");
  });

  // ⚠️ Главное различие политики: «сервер молчит» и «на сервере пусто» — РАЗНЫЕ
  // вещи. На первом кэш надо сохранить, на втором — подчиниться серверу.
  it("returns null when the server is unreachable, not an empty list", async () => {
    mockNet(null, true);
    expect(await fetchAll()).toBeNull();
    mockNet([]);
    expect(await fetchAll()).toEqual([]);
  });

  it("keeps the local cache when the server does not answer", async () => {
    mockNet(null, true);
    const local = appendMessages(load(null), [user("офлайн-вопрос")]);
    const synced = await syncFromServer(local);
    expect(synced).toBe(local); // ровно тот же объект — ничего не подменили
  });

  it("migrates a browser-only conversation to the server", async () => {
    // Первый заход после обновления: локально есть, на сервере пусто.
    const fn = mockNet([]);
    const local = appendMessages(load(null), [user("из localStorage")]);
    const synced = await syncFromServer(local);

    expect(synced).toBe(local); // показываем локальное — оно и есть актуальное
    const sent = wire(fn);
    expect(sent).toHaveLength(1);
    expect(sent[0].append).toBeFalsy();     // перезапись, а не дописывание
    expect(sent[0].messages[0].content).toBe("из localStorage");
  });

  it("does not migrate an empty local store", async () => {
    const fn = mockNet([]);
    await syncFromServer(load(null));
    expect(wire(fn)).toHaveLength(0);
  });

  it("lets the server win when both sides have data", async () => {
    // Иначе чистка браузера была бы неотличима от «разговора не было», и
    // серверная копия затиралась бы пустотой.
    mockNet([{ session_id: "s-serv", updated_at: 2,
               messages: [{ role: "user", content: "с сервера" }] }]);
    const local = appendMessages(load(null), [user("из кэша")]);
    const synced = await syncFromServer(local);

    expect(synced.sessions).toHaveLength(1);
    expect(synced.activeId).toBe("s-serv");
    expect(getActive(synced).messages).toEqual([user("с сервера")]);
  });

  it("keeps the open conversation selected if the server still has it", async () => {
    const local = appendMessages(load(null), [user("мой")]);
    mockNet([
      { session_id: "другой", updated_at: 9, messages: [{ role: "user", content: "чужой" }] },
      { session_id: local.activeId, updated_at: 5, messages: [{ role: "user", content: "мой" }] },
    ]);
    const synced = await syncFromServer(local);
    expect(synced.activeId).toBe(local.activeId);
  });

  it("obeys the session cap on what the server returns", async () => {
    mockNet(Array.from({ length: 30 }, (_, i) => ({
      session_id: `s${i}`, updated_at: i,
      messages: [{ role: "user", content: `q${i}` }],
    })));
    const synced = await syncFromServer(load(null));
    expect(synced.sessions).toHaveLength(20);
  });

  it("sends append, replace and delete in the shape the API expects", async () => {
    const fn = mockNet([]);
    await pushAppend("s1", [user("вопрос")]);
    await pushReplace("s1", [bot("выжимка")]);
    await pushDelete("s1");

    const [appended, replaced] = wire(fn);
    expect(appended).toEqual({ session_id: "s1", append: true,
                               messages: [{ role: "user", content: "вопрос" }] });
    expect(replaced).toEqual({ session_id: "s1",
                               messages: [{ role: "assistant", content: "выжимка" }] });
    const del = fn.mock.calls.find(([, o]: any[]) => o?.method === "DELETE");
    expect(String(del![0])).toContain("session_id=s1");
  });

  it("never throws when the network is down", async () => {
    mockNet(null, true);
    // Переписка ценна, но её недоступность не должна запирать чат.
    await expect(pushAppend("s1", [user("q")])).resolves.toBeUndefined();
    await expect(pushReplace("s1", [user("q")])).resolves.toBeUndefined();
    await expect(pushDelete("s1")).resolves.toBeUndefined();
  });

  it("skips the request entirely for an empty append", async () => {
    const fn = mockNet([]);
    await pushAppend("s1", []);
    expect(fn).not.toHaveBeenCalled();
  });

  it("drops malformed messages coming from the server", async () => {
    mockNet([{ session_id: "s1", updated_at: 1, messages: [
      { role: "user", content: "ок" },
      { role: "system", content: "не наша роль" },
      { role: "user" },
      null,
    ] }]);
    const got = await fetchAll();
    expect(got![0].messages).toEqual([user("ок")]);
  });
});
