import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../TerminalOutput", () => ({ TerminalOutput: ({ lines }: any) => <pre>{(lines || []).join("\n")}</pre> }));
class FakeWS { close() {} set onmessage(_: any) {} set onerror(_: any) {} }
(globalThis as any).WebSocket = FakeWS as any;

import { Migration } from "./Migration";

type H = (body: any) => { status?: number; body?: any };
let ROUTES: Record<string, H> = {};
function route(re: string, h: H) { ROUTES[re] = h; }
function installFetch() {
  const fn = vi.fn(async (url: string, opts?: any) => {
    const body = opts?.body ? JSON.parse(opts.body) : undefined;
    for (const [re, h] of Object.entries(ROUTES)) {
      if (new RegExp(re).test(url)) {
        const { status = 200, body: rb } = h(body);
        return { ok: status < 400, status, json: async () => rb } as any;
      }
    }
    throw new Error(`unmocked ${url}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

beforeEach(() => { ROUTES = {}; });
afterEach(() => vi.restoreAllMocks());

describe("Migration wizard", () => {
  it("renders the 5 sections", () => {
    installFetch();
    render(<Migration />);
    expect(screen.getByText(/1\. Источник \(Marzban\)/)).toBeInTheDocument();
    expect(screen.getByText(/2\. Предпросмотр/)).toBeInTheDocument();
    expect(screen.getByText(/3\. Перенос Reality/)).toBeInTheDocument();
    expect(screen.getByText(/4\. Миграция пользователей/)).toBeInTheDocument();
    expect(screen.getByText(/5\. Legacy-ссылки/)).toBeInTheDocument();
  });

  it("preview shows counts + loss report", async () => {
    route("/api/migrate/preview", () => ({ body: {
      total_users: 88, inbound_tags: ["VLESS_R"], will_not_migrate: ["Reality-ключи (отдельный шаг)", "История трафика"],
    } }));
    installFetch();
    render(<Migration />);
    // fill required marzban creds so the preview button enables
    fireEvent.change(screen.getByPlaceholderText(/marzban.example/), { target: { value: "https://mz" } });
    const tbs = screen.getAllByRole("textbox");
    fireEvent.change(tbs[1], { target: { value: "admin" } });         // marzban login
    // password fields are not textbox role; set via the type=password inputs:
    const pws = document.querySelectorAll('input[type="password"]');
    fireEvent.change(pws[0], { target: { value: "pw" } });            // marzban pass

    fireEvent.click(screen.getByText("Предпросмотр"));
    expect(await screen.findByText(/Пользователей:/)).toBeInTheDocument();
    expect(screen.getByText("88")).toBeInTheDocument();
    expect(screen.getByText(/История трафика/)).toBeInTheDocument();
  });

  it("migrate requires a confirm and then streams", async () => {
    let ran = false;
    route("/api/migrate/run", () => { ran = true; return { body: { task_id: "tk", task_type: "marzban-migrate" } }; });
    installFetch();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Migration />);
    // fill marzban + remnawave creds
    fireEvent.change(screen.getByPlaceholderText(/marzban.example/), { target: { value: "https://mz" } });
    fireEvent.change(screen.getByPlaceholderText(/panel.example/), { target: { value: "https://rw" } });
    const tbs = screen.getAllByRole("textbox");
    fireEvent.change(tbs[1], { target: { value: "admin" } }); // marzban login
    const pws = document.querySelectorAll('input[type="password"]');
    fireEvent.change(pws[0], { target: { value: "pw" } });    // marzban pass
    fireEvent.change(pws[1], { target: { value: "tok" } });   // remnawave token

    fireEvent.click(screen.getByText("Мигрировать пользователей"));
    await waitFor(() => expect(ran).toBe(true));
  });

  it("migrate aborts if confirm is declined", async () => {
    let ran = false;
    route("/api/migrate/run", () => { ran = true; return { body: {} }; });
    installFetch();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<Migration />);
    fireEvent.change(screen.getByPlaceholderText(/marzban.example/), { target: { value: "https://mz" } });
    fireEvent.change(screen.getByPlaceholderText(/panel.example/), { target: { value: "https://rw" } });
    const tbs = screen.getAllByRole("textbox");
    fireEvent.change(tbs[1], { target: { value: "admin" } });
    const pws = document.querySelectorAll('input[type="password"]');
    fireEvent.change(pws[0], { target: { value: "pw" } });
    fireEvent.change(pws[1], { target: { value: "tok" } });
    fireEvent.click(screen.getByText("Мигрировать пользователей"));
    await new Promise(r => setTimeout(r, 20));
    expect(ran).toBe(false);
  });

  it("legacy secret is shown masked (type=password) after read", async () => {
    route("/api/migrate/legacy-secret", () => ({ body: { secret_key: "LEG-SECRET", env_hint: "MARZBAN_LEGACY_SECRET_KEY" } }));
    installFetch();
    render(<Migration />);
    // IP is the last textbox in the legacy section
    const tbs = screen.getAllByRole("textbox");
    fireEvent.change(tbs[tbs.length - 2], { target: { value: "1.2.3.4" } }); // marzban IP
    fireEvent.click(screen.getByText("Прочитать secret_key"));
    const field = await screen.findByDisplayValue("LEG-SECRET") as HTMLInputElement;
    expect(field.type).toBe("password");
    expect(screen.getByText("MARZBAN_LEGACY_SECRET_KEY")).toBeInTheDocument();
  });
});
