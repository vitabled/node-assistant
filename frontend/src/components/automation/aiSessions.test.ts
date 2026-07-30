import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  load, save, sessionsKey, listSessions, getActive, setActive, newSession,
  clearActive, replaceMessages, appendMessages, renameActive, removeSession,
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
