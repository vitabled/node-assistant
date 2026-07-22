import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AiChat } from "./AiChat";

const CONFIG = {
  enabled: true, provider: "openai", base_url: "https://x/v1", model: "m",
  max_steps: 4, readonly: true, has_key: true,
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

function installFetch(chatEvents: any[]) {
  const fn = vi.fn(async (url: string, opts?: any) => {
    if (url === "/api/ai/config" && (!opts || opts.method !== "POST"))
      return { ok: true, json: async () => CONFIG } as any;
    if (url === "/api/ai/config") return { ok: true, json: async () => CONFIG } as any;
    if (url === "/api/ai/chat") return streamResponse(chatEvents);
    throw new Error(`unmocked ${url}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

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
    fireEvent.change(input, { target: { value: "сколько правил?" } });
    fireEvent.keyDown(input, { key: "Enter" });

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
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByText(/ИИ-агент выключен/)).toBeInTheDocument());
  });
});
