import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AiChat } from "./AiChat";

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

/** `tools` = null → ручка возможностей отвечает 500 (проверка «не рисуем ничего»). */
function installFetch(chatEvents: any[], tools: any = TOOLS) {
  const fn = vi.fn(async (url: string, opts?: any) => {
    if (url === "/api/ai/config" && (!opts || opts.method !== "POST"))
      return { ok: true, json: async () => CONFIG } as any;
    if (url === "/api/ai/config") return { ok: true, json: async () => CONFIG } as any;
    if (url === "/api/ai/tools")
      return tools ? { ok: true, json: async () => tools } as any : { ok: false } as any;
    if (url === "/api/ai/chat") return streamResponse(chatEvents);
    throw new Error(`unmocked ${url}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

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

afterEach(() => vi.restoreAllMocks());

describe("AiChat", () => {
  it("renders the chat once loaded", async () => {
    installFetch([]);
    render(<AiChat />);
    expect(await screen.findByText(/Встроенный ИИ-агент/)).toBeInTheDocument();
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
    expect(await screen.findByText("сколько правил?")).toBeInTheDocument();
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
});
