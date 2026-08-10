import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CliproxyOAuth } from "./CliproxyOAuth";

// Wave-4 PR-5: OAuth-вход через CLIProxyAPI — start → ссылка → callback → status.

function installFetch(handlers: Record<string, (init?: RequestInit) => Response>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    for (const [prefix, h] of Object.entries(handlers)) {
      if (url.startsWith(prefix)) return h(init);
    }
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const OK_START = { url: "https://auth.example/authorize", state: "st-1" };

describe("CliproxyOAuth", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("start → показывает ссылку; callback+status ok → «Вход выполнен»", async () => {
    const calls = installFetch({
      "/api/cliproxy/accounts": () => new Response("[]", { status: 200 }),
      "/api/cliproxy/oauth/start": () => new Response(JSON.stringify(OK_START), { status: 200 }),
      "/api/cliproxy/oauth/callback": () => new Response("{}", { status: 200 }),
      "/api/cliproxy/oauth/status": () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    });
    render(<CliproxyOAuth />);

    fireEvent.click(screen.getByText("Получить ссылку входа"));
    expect(await screen.findByText("Открыть авторизацию у провайдера")).toBeInTheDocument();
    const start = calls.find(c => c.url === "/api/cliproxy/oauth/start");
    expect(JSON.parse(String(start!.init!.body))).toEqual({ provider: "claude" });

    fireEvent.change(screen.getByPlaceholderText(/auth\/callback/), {
      target: { value: "http://localhost:1455/auth/callback?code=abc" },
    });
    fireEvent.click(screen.getByText("Завершить"));

    await waitFor(() => expect(screen.getByText(/Вход выполнен/)).toBeInTheDocument(), { timeout: 4000 });
    const cb = calls.find(c => c.url === "/api/cliproxy/oauth/callback");
    expect(JSON.parse(String(cb!.init!.body))).toEqual({
      redirect_url: "http://localhost:1455/auth/callback?code=abc", state: "st-1",
    });
  });

  it("ошибка start показывается", async () => {
    installFetch({
      "/api/cliproxy/accounts": () => new Response("[]", { status: 200 }),
      "/api/cliproxy/oauth/start": () =>
        new Response(JSON.stringify({ detail: "Шлюз не запущен" }), { status: 502 }),
    });
    render(<CliproxyOAuth />);
    fireEvent.click(screen.getByText("Получить ссылку входа"));
    expect(await screen.findByText("Шлюз не запущен")).toBeInTheDocument();
  });
});
