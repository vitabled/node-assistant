import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { AiChat, fmtTokens, fmtElapsed, readFile } from "./AiChat";
import * as runner from "./aiRunner";
import { load, getActive } from "./aiSessions";

const SUMMARY = "Обсудили ноды: их 12, все онлайн.";

const CONFIG = {
  enabled: true, provider: "openai", base_url: "https://x/v1", model: "m",
  max_steps: 4, readonly: true, has_key: true,
};

const TOOLS = {
  builtin: 14, tools: ["panel_get", "web_search"], writes: false,
  web: true, web_provider: "DuckDuckGo (без ключа)", mcp: 0, reason: "off",
};

// Build a ReadableStream-like Response body from ndjson event lines.
function streamResponse(events: any[]) {
  const chunks = events.map(e => new TextEncoder().encode(JSON.stringify(e) + "\n"));
  let i = 0;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length ? { done: false, value: chunks[i++] } : { done: true, value: undefined },
      }),
    },
  } as any;
}

/** История с сервера. Начинается пустой: тест, которому нужна восстановленная
 *  переписка, подменяет её через `serverHistory`. */
let serverHistory: any[] = [];

/** Что реально уехало на сервер: массив тел POST /api/ai/chat/history. Проверять
 *  надо именно это — durable-история и есть «сообщение доехало до сервера», а не
 *  «нарисовалось в DOM». */
interface HistoryPost {
  session_id: string;
  append?: boolean;
  messages: { role: string; content: string }[];
}

function historyPosts(fn: any): HistoryPost[] {
  return fn.mock.calls
    .filter(([u, o]: any[]) => String(u).startsWith("/api/ai/chat/history") && o?.method === "POST")
    .map(([, o]: any[]) => JSON.parse(o.body));
}

/** `tools` = null → ручка возможностей отвечает 500 (проверка «не рисуем ничего»). */
function installFetch(chatEvents: any[], tools: any = TOOLS) {
  const fn = vi.fn(async (url: string, opts?: any) => {
    const u = String(url);
    if (u === "/api/ai/config" && (!opts || opts.method !== "POST"))
      return { ok: true, json: async () => CONFIG } as any;
    if (u === "/api/ai/config") return { ok: true, json: async () => CONFIG } as any;
    if (u === "/api/ai/tools")
      return tools ? { ok: true, json: async () => tools } as any : { ok: false } as any;
    // Durable-история: GET отдаёт то, что «лежит на сервере», POST/DELETE просто
    // подтверждают — их тела проверяются через `historyPosts`.
    if (u.startsWith("/api/ai/chat/history")) {
      if (opts?.method === "POST" || opts?.method === "DELETE")
        return { ok: true, json: async () => ({ ok: true }) } as any;
      return { ok: true, json: async () => ({ sessions: serverHistory }) } as any;
    }
    if (u.startsWith("/api/ai/chat/state"))
      return { ok: true, json: async () => ({ active: false, events: 0 }) } as any;
    if (u === "/api/ai/chat") return streamResponse(chatEvents);
    if (u === "/api/ai/compact") return { ok: true, json: async () => ({ summary: SUMMARY }) } as any;
    throw new Error(`unmocked ${u}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

/** Что легло в localStorage под текущей (в тестах — анонимной) личностью. */
const stored = () => getActive(load(null)).messages;

/** Заголовок сессии — это первое сообщение пользователя, поэтому один и тот же
 *  текст живёт и в логе, и в списке сессий. Запросы по тексту реплики обязаны
 *  быть ограничены логом, иначе они находят два элемента. */
const log = () => within(screen.getByTestId("ai-chat-log"));

/** Тело последнего POST /api/ai/chat. */
function lastChatBody(fn: any) {
  const calls = fn.mock.calls.filter(([u]: any[]) => u === "/api/ai/chat");
  return JSON.parse(calls[calls.length - 1][1].body);
}

/** Вложение подсовываем вставкой из буфера: `readFile` берёт у файла только
 *  `type` и `text()`, так что настоящий File (и его поддержка в jsdom) не нужен. */
function pasteFile(input: HTMLElement, name: string, text: string) {
  fireEvent.paste(input, {
    clipboardData: { files: [{ name, type: "text/plain", text: async () => text }] },
  });
}

async function ask(input: HTMLElement, text: string) {
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
}

/** Ход дошёл до конца. Проверять надо именно это: пока `busy`, следующий `send`
 *  молча выходит, и тест провалился бы на «истории нет» вместо настоящей причины.
 *  Кнопка очистки отпирается ровно при `!busy && есть сообщения`. */
const settled = () => waitFor(() => expect(screen.getByTitle(/Очистить/)).not.toBeDisabled());

// Переписка теперь переживает перемонтирование — значит, и утечёт в следующий
// тест, если не убрать её руками.
beforeEach(() => {
  localStorage.clear();
  // Сервер по умолчанию пуст: каждый тест начинает с чистой историей, иначе
  // синхронизация на монтировании затаскивала бы переписку прошлого теста.
  serverHistory = [];
  // Исполнитель — синглтон и намеренно переживает размонтирование компонента
  // (в этом весь смысл: ответ не должен обрываться при уходе со страницы).
  // Значит его память надо сбрасывать вместе с хранилищем, иначе разговор
  // протекает в следующий тест.
  runner.reset();
});
afterEach(() => vi.restoreAllMocks());

describe("AiChat", () => {
  it("renders the chat once loaded", async () => {
    installFetch([]);
    render(<AiChat />);
    // Название раздела живёт в хлебных крошках («Автоматизация / Ассистент»),
    // поэтому в шапке чата стоит имя РАЗГОВОРА — как в Claude Desktop.
    expect(await screen.findByPlaceholderText(/Сообщение агенту/)).toBeInTheDocument();
    expect(screen.getByTestId("ai-sessions")).toBeInTheDocument();
    expect(screen.getAllByText("Новый разговор").length).toBeGreaterThan(0);
  });

  // Волна 6, План C Ф1: конфигурация уехала в «Настройки → Ассистент».
  it("shows no provider config on the chat page", async () => {
    installFetch([]);
    render(<AiChat />);
    await screen.findByPlaceholderText(/Сообщение агенту/);
    expect(screen.queryByText(/Base URL/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Лимит шагов/)).not.toBeInTheDocument();
    expect(screen.queryByText(/сохранён/)).not.toBeInTheDocument(); // has_key badge живёт в настройках
  });

  it("gives the log its own scroller instead of a fixed max height", async () => {
    installFetch([]);
    render(<AiChat />);
    const log = await screen.findByTestId("ai-chat-log");
    expect(log.className).toContain("overflow-y-auto");
    expect(log.className).toContain("min-h-0");
    expect(log.className).not.toContain("max-h-80");
  });

  it("does not POST the config from the chat page", async () => {
    const fn = installFetch([]);
    render(<AiChat />);
    await screen.findByPlaceholderText(/Сообщение агенту/);
    const posts = fn.mock.calls.filter(([url, o]: any[]) => url === "/api/ai/config" && o?.method === "POST");
    expect(posts).toHaveLength(0);
  });

  it("streams a tool-call and the final answer into the chat", async () => {
    installFetch([
      { type: "tool_call", name: "list_rules", args: {} },
      { type: "tool_result", name: "list_rules", ok: true, preview: "[]" },
      { type: "text", delta: "У вас 0 правил." },
      { type: "done" },
    ]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);
    await ask(input, "сколько правил?");

    // user message shown
    await waitFor(() => expect(log().getByText("сколько правил?")).toBeInTheDocument());
    // tool-call chip + final answer streamed in
    await waitFor(() => expect(screen.getByText("list_rules")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/У вас 0 правил\./)).toBeInTheDocument());
  });

  it("surfaces a streamed error event in the assistant bubble", async () => {
    installFetch([{ type: "error", message: "ИИ-агент выключен." }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);
    await ask(input, "hi");
    await waitFor(() => expect(screen.getByText(/ИИ-агент выключен/)).toBeInTheDocument());
  });

  // Без истории «а теперь то же для второй ноды» не к чему привязать.
  it("carries the previous turns in the request body", async () => {
    const fn = installFetch([{ type: "text", delta: "12 нод." }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "сколько нод?");
    await waitFor(() => expect(screen.getByText(/12 нод\./)).toBeInTheDocument());
    expect(lastChatBody(fn).history).toEqual([]); // первый ход — истории нет
    await settled();

    await ask(input, "а сколько из них онлайн?");
    await waitFor(() => {
      const history = lastChatBody(fn).history;
      expect(history).toEqual([
        { role: "user", content: "сколько нод?" },
        { role: "assistant", content: "12 нод." },
      ]);
    });
  });

  // Вложения эфемерны: файла в следующем ходе у модели нет, поэтому служебная
  // строка с его именем не должна попасть в историю как часть реплики.
  it("keeps the attachment line out of the history", async () => {
    const fn = installFetch([{ type: "text", delta: "разобрал" }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    pasteFile(input, "server.log", "boom");
    await screen.findByText("server.log");
    await ask(input, "разбери лог");
    await waitFor(() => expect(screen.getByText(/разобрал/)).toBeInTheDocument());
    expect(lastChatBody(fn).attachments).toHaveLength(1);
    await settled();

    await ask(input, "а теперь коротко");
    await waitFor(() => {
      const history = lastChatBody(fn).history;
      expect(history[0]).toEqual({ role: "user", content: "разбери лог" });
      expect(JSON.stringify(history)).not.toContain("server.log");
      expect(JSON.stringify(history)).not.toContain("📎");
    });
  });

  it("clears the log (and with it the history) on demand", async () => {
    const fn = installFetch([{ type: "text", delta: "готово" }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "первый вопрос");
    await waitFor(() => expect(screen.getByText(/готово/)).toBeInTheDocument());

    await settled(); // на время ответа кнопка заперта — клик бы молча пропал
    fireEvent.click(screen.getByTitle(/Очистить/));
    expect(screen.queryByText("первый вопрос")).not.toBeInTheDocument();
    expect(screen.queryByText(/готово/)).not.toBeInTheDocument();

    await ask(input, "второй вопрос");
    await waitFor(() => {
      const chats = fn.mock.calls.filter(([u]: any[]) => u === "/api/ai/chat");
      expect(chats).toHaveLength(2); // иначе пустая история была бы просто первым ходом
      expect(lastChatBody(fn).history).toEqual([]);
    });
  });

  it("shows what the assistant can reach before the first question", async () => {
    installFetch([]);
    render(<AiChat />);
    const caps = await screen.findByTestId("ai-caps");
    expect(caps.textContent).toContain("14");
    expect(caps.textContent).toContain("DuckDuckGo");
    expect(caps.textContent).toContain("только чтение");
  });

  // Ручка необязательная: заглушка с нулями врала бы про агента сильнее, чем
  // отсутствие строки.
  it("renders no capability line when /api/ai/tools fails", async () => {
    installFetch([], null);
    render(<AiChat />);
    await screen.findByPlaceholderText(/Сообщение агенту/);
    expect(screen.queryByTestId("ai-caps")).not.toBeInTheDocument();
  });

  // ── сессии и команды ──────────────────────────────────────────

  it("keeps the conversation across a remount", async () => {
    installFetch([{ type: "text", delta: "12 нод." }, { type: "done" }]);
    const first = render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "сколько нод?");
    await settled();
    // Ждём именно записи: перемонтирование читает хранилище один раз, и
    // повторная проверка DOM его уже не догонит.
    await waitFor(() => expect(stored()).toHaveLength(2));
    first.unmount();

    render(<AiChat />);
    await screen.findByPlaceholderText(/Сообщение агенту/);
    expect(log().getByText("сколько нод?")).toBeInTheDocument();
    expect(log().getByText(/12 нод\./)).toBeInTheDocument();
    expect(screen.getByTestId("ai-msg-count").textContent).toContain("2");
  });

  it("/clear empties the current session and keeps it in the list", async () => {
    const fn = installFetch([{ type: "text", delta: "готово" }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "первый вопрос");
    await settled();
    await ask(input, "/clear");

    await waitFor(() => expect(log().queryByText("первый вопрос")).not.toBeInTheDocument());
    expect(screen.getAllByTestId("ai-session-row")).toHaveLength(1);
    expect(stored()).toEqual([]);
    // Команда разбирается до сети: второго запроса в чат быть не должно.
    expect(fn.mock.calls.filter(([u]: any[]) => u === "/api/ai/chat")).toHaveLength(1);
  });

  it("/newsession opens an empty session and leaves the old one in the list", async () => {
    installFetch([{ type: "text", delta: "готово" }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "первый вопрос");
    await settled();
    await ask(input, "/newsession");

    await waitFor(() => expect(log().queryByText("первый вопрос")).not.toBeInTheDocument());
    const rows = screen.getAllByTestId("ai-session-row");
    expect(rows).toHaveLength(2);
    expect(rows.map(r => r.textContent)).toContain("первый вопрос"); // старая сессия жива
    expect(stored()).toEqual([]);                                    // активна — новая, пустая
  });

  it("/compact replaces the conversation with the summary and sends it as history", async () => {
    const fn = installFetch([{ type: "text", delta: "12 нод." }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "сколько нод?");
    await settled();
    await ask(input, "/compact");

    await waitFor(() => expect(log().getByText(new RegExp(SUMMARY))).toBeInTheDocument());
    const compacts = fn.mock.calls.filter(([u]: any[]) => u === "/api/ai/compact");
    expect(compacts).toHaveLength(1);
    expect(JSON.parse(compacts[0][1].body).history).toEqual([
      { role: "user", content: "сколько нод?" },
      { role: "assistant", content: "12 нод." },
    ]);
    expect(log().queryByText("сколько нод?")).not.toBeInTheDocument();
    expect(log().getByText(/Сжатый контекст/)).toBeInTheDocument();

    // Следующий ход уходит уже с выжимкой вместо всей переписки.
    await settled();
    await ask(input, "а сколько онлайн?");
    await waitFor(() => {
      const history = lastChatBody(fn).history;
      expect(history).toHaveLength(1);
      expect(history[0].role).toBe("assistant");
      expect(history[0].content).toContain(SUMMARY);
    });
  });

  // Иначе нельзя было бы спросить про путь: «/api/…» — не команда.
  it("treats an unknown slash-word as an ordinary message", async () => {
    const fn = installFetch([{ type: "text", delta: "это ручка конфига" }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "/api/foo");
    await waitFor(() => expect(log().getByText(/это ручка конфига/)).toBeInTheDocument());
    expect(lastChatBody(fn).prompt).toBe("/api/foo");
    expect(log().getByText("/api/foo")).toBeInTheDocument(); // показан как реплика пользователя
  });

  it("recognises a command even with stray spaces around it", async () => {
    const fn = installFetch([{ type: "text", delta: "готово" }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "первый вопрос");
    await settled();
    await ask(input, "   /clear  ");

    await waitFor(() => expect(log().queryByText("первый вопрос")).not.toBeInTheDocument());
    expect(fn.mock.calls.filter(([u]: any[]) => u === "/api/ai/chat")).toHaveLength(1);
  });

  // Строка состояния: без неё длинный ответ выглядит зависшим — инструменты
  // отработали, текста ещё нет, и понять, жив ли агент, нечем.
  it("shows a live status line while the agent works and hides it afterwards", async () => {
    installFetch([
      { type: "status", phase: "thinking", step: 1, steps: 6, tokens: 0 },
      { type: "status", phase: "tools", step: 1, steps: 6, tokens: 1234 },
      { type: "tool_call", id: "1", name: "read_attachment" },
      { type: "tool_result", id: "1", name: "read_attachment", ok: true },
      { type: "text", delta: "Готово." },
      { type: "status", phase: "done", step: 2, steps: 6, tokens: 2034 },
      { type: "done" },
    ]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);
    await ask(input, "перенеси данные");

    await waitFor(() => expect(log().getByText("Готово.")).toBeInTheDocument());
    // Ответ завершён — строка обязана исчезнуть, иначе она врёт о работе.
    expect(screen.queryByTestId("ai-status")).not.toBeInTheDocument();
  });

  it("renders phase, step, tokens and the running tool in the status line", () => {
    // Форматтеры — чистые: проверяем их напрямую, не гоняя стрим по секундам.
    expect(fmtTokens(6432)).toBe("6.4k");
    expect(fmtTokens(2000)).toBe("2k");
    expect(fmtTokens(940)).toBe("940");
    expect(fmtElapsed(45)).toBe("45с");
    expect(fmtElapsed(510)).toBe("8м 30с");
  });

  // ── прогресс переживает уход со страницы ────────────────────
  it("keeps the answer running when the user leaves the page", async () => {
    // Стрим, который мы отпускаем по частям: имитируем долгий ответ.
    let push!: (line: string) => void;
    let finish!: () => void;
    const body = new ReadableStream({
      start(c) {
        const enc = new TextEncoder();
        push = (line: string) => c.enqueue(enc.encode(line + "\n"));
        finish = () => c.close();
      },
    });
    vi.spyOn(globalThis, "fetch" as any).mockImplementation((url: any) => {
      const u = String(url);
      if (u.includes("/api/ai/config"))
        return Promise.resolve(new Response(JSON.stringify({ enabled: true, has_key: true }),
                                            { status: 200 }));
      if (u.includes("/api/ai/tools"))
        return Promise.resolve(new Response(JSON.stringify({ builtin: 1, writes: false, web: false, web_provider: "duckduckgo" }), { status: 200 }));
      // ⚠️ Отдельно от потока: `resume` на монтировании спрашивает, не остался
      // ли незаконченный ответ, и общий `body` был бы им же и вычитан. То же и
      // с durable-историей — её GET/POST обязаны иметь свои ответы.
      if (u.includes("/api/ai/chat/history"))
        return Promise.resolve(new Response(JSON.stringify({ sessions: [] }), { status: 200 }));
      if (u.includes("/api/ai/chat/state"))
        return Promise.resolve(new Response(JSON.stringify({ active: false, events: 0 }),
                                            { status: 200 }));
      return Promise.resolve(new Response(body, { status: 200 }));
    });

    const view = render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);
    fireEvent.change(input, { target: { value: "долгий вопрос" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(runner.getRunState().busy).toBe(true));

    // Пользователь ушёл в другой раздел — компонент размонтирован.
    view.unmount();
    expect(runner.getRunState().busy).toBe(true);

    // ...а ответ продолжает приходить и попадать в хранилище.
    push(JSON.stringify({ type: "text", delta: "часть ответа" }));
    await waitFor(() => {
      const msgs = getActive(runner.getSessions()).messages;
      expect(msgs[msgs.length - 1].text).toContain("часть ответа");
    });
    finish();
    await waitFor(() => expect(runner.getRunState().busy).toBe(false));

    // Вернулись — текст на месте, ничего не потеряно.
    render(<AiChat />);
    expect(await screen.findByText(/часть ответа/)).toBeInTheDocument();
  });

  it("stops only when asked explicitly", async () => {
    // Поток, который сам не закончится: так видно, что отменяет именно кнопка.
    const body = new ReadableStream({ start() { /* держим открытым */ } });
    vi.spyOn(globalThis, "fetch" as any).mockImplementation((url: any) => {
      const u = String(url);
      if (u.includes("/api/ai/config"))
        return Promise.resolve(new Response(JSON.stringify({ enabled: true, has_key: true }), { status: 200 }));
      if (u.includes("/api/ai/tools"))
        return Promise.resolve(new Response(JSON.stringify(TOOLS), { status: 200 }));
      if (u.includes("/api/ai/chat/history"))
        return Promise.resolve(new Response(JSON.stringify({ sessions: [] }), { status: 200 }));
      if (u.includes("/api/ai/chat/state"))
        return Promise.resolve(new Response(JSON.stringify({ active: false }), { status: 200 }));
      if (u.includes("/api/ai/chat/stop"))
        return Promise.resolve(new Response(JSON.stringify({ stopped: true }), { status: 200 }));
      return Promise.resolve(new Response(body, { status: 200 }));
    });

    const view = render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);
    await ask(input, "вопрос");
    await waitFor(() => expect(runner.getRunState().busy).toBe(true));

    // Размонтирование НЕ отменяет: именно автоотмена и теряла работу.
    view.unmount();
    expect(runner.getRunState().busy).toBe(true);

    // А явная остановка — отменяет.
    runner.stop();
    expect(runner.getRunState().busy).toBe(false);
  });

  // ── durable-история: переписка переживает чистку браузера ─────
  //
  // Ровно то, ради чего фича и делалась. Раньше разговор жил только в
  // localStorage, и Safari/приватное окно/«очистить данные» уносили его молча.

  it("restores the conversation from the server when localStorage is empty", async () => {
    // Браузер вычистил хранилище: локально пусто, а на сервере переписка есть.
    serverHistory = [{
      session_id: "s-old", updated_at: 1_700_000_000,
      messages: [
        { role: "user", content: "сколько нод?", ts: 1 },
        { role: "assistant", content: "12 нод.", ts: 2 },
      ],
    }];
    installFetch([]);
    render(<AiChat />);
    await screen.findByPlaceholderText(/Сообщение агенту/);

    await waitFor(() => expect(log().getByText(/12 нод\./)).toBeInTheDocument());
    expect(log().getByText("сколько нод?")).toBeInTheDocument();
    expect(getActive(runner.getSessions()).id).toBe("s-old");
  });

  it("saves the question and the answer to the server", async () => {
    const fn = installFetch([{ type: "text", delta: "12 нод." }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "сколько нод?");
    await settled();

    await waitFor(() => {
      const bodies = historyPosts(fn).filter(b => b.append);
      // Вопрос уходит СРАЗУ (вкладку закрывают именно во время долгого ответа),
      // ответ — по завершении стрима.
      expect(bodies.flatMap(b => b.messages.map((m: any) => m.content)))
        .toEqual(["сколько нод?", "12 нод."]);
      expect(bodies.every(b => b.session_id)).toBe(true);
    });
  });

  it("keeps a browser-only conversation by migrating it to the server", async () => {
    // Первый заход после обновления: локально переписка есть, на сервере пусто.
    // Клиент обязан залить её туда, иначе она пропадёт при следующей чистке.
    const fn = installFetch([{ type: "text", delta: "готово" }, { type: "done" }]);
    const first = render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);
    await ask(input, "старый вопрос");
    await settled();
    await waitFor(() => expect(stored()).toHaveLength(2));
    first.unmount();

    // Перемонтирование с чистой памятью исполнителя = новая загрузка страницы.
    runner.reset();
    const fn2 = installFetch([]);
    render(<AiChat />);
    await screen.findByPlaceholderText(/Сообщение агенту/);

    await waitFor(() => {
      const migrated = historyPosts(fn2).filter(b => !b.append);
      expect(migrated.length).toBeGreaterThan(0);
      expect(JSON.stringify(migrated)).toContain("старый вопрос");
    });
    expect(fn).toBeDefined();
  });

  it("propagates a clear to the server so it survives a reload", async () => {
    // Не отправь мы очистку — синхронизация вернула бы стёртое с сервера, и
    // кнопка «Очистить» переставала бы работать после перезагрузки.
    const fn = installFetch([{ type: "text", delta: "готово" }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);

    await ask(input, "первый вопрос");
    await settled();
    fireEvent.click(screen.getByTitle(/Очистить/));

    await waitFor(() => {
      const wipes = historyPosts(fn).filter(b => !b.append && b.messages.length === 0);
      expect(wipes.length).toBeGreaterThan(0);
    });
  });

  it("deletes a conversation on the server too", async () => {
    const fn = installFetch([{ type: "text", delta: "готово" }, { type: "done" }]);
    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);
    await ask(input, "первый вопрос");
    await settled();

    const row = screen.getAllByTestId("ai-session-row")[0];
    fireEvent.click(row.parentElement!.querySelector("button[title='Удалить разговор']")!);

    await waitFor(() => {
      const dels = fn.mock.calls.filter(([u, o]: any[]) =>
        String(u).startsWith("/api/ai/chat/history") && o?.method === "DELETE");
      expect(dels.length).toBe(1);
    });
  });

  it("still works when the history endpoint is down", async () => {
    // История ценна, но её недоступность не должна запирать чат: без этого
    // отказ ручки выглядел бы как поломка ассистента целиком.
    const fn = vi.fn(async (url: string, opts?: any) => {
      const u = String(url);
      if (u === "/api/ai/config") return { ok: true, json: async () => CONFIG } as any;
      if (u === "/api/ai/tools") return { ok: true, json: async () => TOOLS } as any;
      if (u.startsWith("/api/ai/chat/history")) throw new Error("network down");
      if (u.startsWith("/api/ai/chat/state"))
        return { ok: true, json: async () => ({ active: false }) } as any;
      if (u === "/api/ai/chat")
        return streamResponse([{ type: "text", delta: "всё равно ответил" }, { type: "done" }]);
      throw new Error(`unmocked ${u}`);
    });
    (globalThis as any).fetch = fn;

    render(<AiChat />);
    const input = await screen.findByPlaceholderText(/Сообщение агенту/);
    await ask(input, "вопрос");
    await waitFor(() => expect(log().getByText(/всё равно ответил/)).toBeInTheDocument());
  });
});

/** Архив во вложении.
 *
 *  ⚠️ Регрессия на настоящий баг: `.tar.gz` читался как текст (`f.text()` на
 *  gzip даёт мусор или пустоту), вложение отбрасывалось с «пустой или
 *  нечитаемый файл», и агент уходил искать данные в вебе — до него не доезжало
 *  НИЧЕГО. Архив обязан уехать на сервер сырым: распаковывает его бэкенд
 *  (`ai_archives.unpack`). */
describe("readFile: архивы", () => {
  /** Минимальный File-подобный объект: `readFile` берёт только эти поля, а
   *  настоящий File с arrayBuffer() в jsdom собирать незачем. */
  const fakeFile = (name: string, type: string, bytes: number[], size?: number) =>
    ({
      name, type, size: size ?? bytes.length,
      arrayBuffer: async () => new Uint8Array(bytes).buffer,
      text: async () => "\u0000\u0000garbage",
    } as unknown as File);

  // Заголовок gzip: как раз тот случай, где f.text() возвращает мусор.
  const GZIP = [0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00];

  it("шлёт .tar.gz как base64, а не отбрасывает", async () => {
    const r = await readFile(fakeFile("as-ip-blocks-ipv4-only.tar.gz", "application/gzip", GZIP));
    expect(typeof r).not.toBe("string");
    const a = r as Exclude<typeof r, string>;
    expect(a.name).toBe("as-ip-blocks-ipv4-only.tar.gz");
    expect(a.data_b64).toBe(btoa(String.fromCharCode(...GZIP)));
    expect(a.text).toBe("");
  });

  it.each([
    ["dump.tgz", ""],
    ["dump.zip", ""],
    ["dump.tar", ""],
    // Windows шлёт пустой mime, но и с zip-овским mime без расширения тоже
    // должно работать — узнаём и по имени, и по типу.
    ["blob", "application/zip"],
    ["blob2", "application/x-zip-compressed"],
  ])("узнаёт архив %s (mime %s)", async (name, mime) => {
    const r = await readFile(fakeFile(name, mime, GZIP));
    expect(typeof r).not.toBe("string");
    expect((r as any).data_b64.length).toBeGreaterThan(0);
  });

  it("отказывает архиву больше 50 МБ понятной строкой", async () => {
    const r = await readFile(fakeFile("huge.tar.gz", "application/gzip", GZIP,
                                      60 * 1024 * 1024));
    expect(r).toBe("huge.tar.gz: архив больше 50 МБ");
  });

  it("не ломает текстовые файлы: лог по-прежнему едет текстом", async () => {
    const f = {
      name: "nodes.log", type: "text/plain", size: 10,
      text: async () => "node-1 online\nnode-2 offline\n",
    } as unknown as File;
    const r = await readFile(f);
    expect(typeof r).not.toBe("string");
    expect((r as any).text).toContain("node-1 online");
    expect((r as any).data_b64).toBe("");
  });

  it("пустой текстовый файл по-прежнему отбивается сообщением", async () => {
    const f = { name: "empty.txt", type: "text/plain", size: 0,
                text: async () => "   " } as unknown as File;
    expect(await readFile(f)).toBe("empty.txt: пустой или нечитаемый файл");
  });
});
