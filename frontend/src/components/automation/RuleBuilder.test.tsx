import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RuleBuilder, RuleModal } from "./RuleBuilder";
import { Notifications } from "./Notifications";
import type { Rule } from "./rulesApi";

// ── fetch mock router ─────────────────────────────────────────
type Handler = (body: any, url: string) => { status?: number; body?: any };
let ROUTES: Record<string, Handler> = {};

function route(method: string, path: RegExp | string, h: Handler) {
  ROUTES[`${method} ${path}`] = h;
}

function installFetch() {
  const fn = vi.fn(async (url: string, opts?: any) => {
    const method = (opts?.method || "GET").toUpperCase();
    const body = opts?.body ? JSON.parse(opts.body) : undefined;
    for (const [key, h] of Object.entries(ROUTES)) {
      const [m, ...rest] = key.split(" ");
      const pat = rest.join(" ");
      if (m !== method) continue;
      const re = new RegExp(pat);
      if (re.test(url)) {
        const { status = 200, body: resBody } = h(body, url);
        return {
          ok: status < 400,
          status,
          json: async () => resBody,
        } as any;
      }
    }
    throw new Error(`unmocked ${method} ${url}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

const telegramRule = (over: Partial<Rule> = {}): Rule => ({
  id: "r1",
  name: "Падение ноды",
  enabled: true,
  trigger: { type: "xray_down", params: { minutes: 5 } },
  conditions: [],
  actions: [{ type: "telegram", params: { chat_id: "42", text: "down $node", token_ref: "tref1", bot_token: "••••" } }],
  cooldown_sec: 300,
  dry_run: false,
  ...over,
});

beforeEach(() => { ROUTES = {}; });
afterEach(() => { vi.restoreAllMocks(); });

describe("RuleBuilder", () => {
  it("shows the empty state when there are no rules", async () => {
    route("GET", "/api/rules$", () => ({ body: [] }));
    installFetch();
    render(<RuleBuilder />);
    expect(await screen.findByText(/Правил автоматизации пока нет/)).toBeInTheDocument();
  });

  it("lists existing rules with a trigger + actions summary", async () => {
    route("GET", "/api/rules$", () => ({ body: [telegramRule()] }));
    installFetch();
    render(<RuleBuilder />);
    expect(await screen.findByText("Падение ноды")).toBeInTheDocument();
    expect(screen.getByText(/Нода недоступна ≥ 5 мин/)).toBeInTheDocument();
    expect(screen.getByText(/Telegram-уведомление/)).toBeInTheDocument();
  });

  it("blocks saving a rule with no actions (client validation mirrors the backend)", async () => {
    let posted = false;
    route("GET", "/api/rules$", () => ({ body: [] }));
    route("POST", "/api/rules$", () => { posted = true; return { status: 201, body: telegramRule() }; });
    installFetch();
    render(<RuleBuilder />);
    fireEvent.click(await screen.findByText(/Создать правило/));
    fireEvent.change(screen.getByPlaceholderText(/Например/), { target: { value: "Тест" } });
    fireEvent.click(screen.getByText("Сохранить"));
    // Exact match hits the error box only (the static hint ends with a period).
    expect(await screen.findByText("Добавьте хотя бы одно действие")).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it("shows a load error (not the empty state) when the list request fails", async () => {
    route("GET", "/api/rules$", () => ({ status: 500, body: { detail: "boom" } }));
    installFetch();
    render(<RuleBuilder />);
    expect(await screen.findByText(/Не удалось загрузить правила/)).toBeInTheDocument();
    expect(screen.queryByText(/Правил автоматизации пока нет/)).not.toBeInTheDocument();
  });
});

describe("RuleModal", () => {
  it("masks an existing telegram bot-token (shows ••••, never the plaintext)", () => {
    installFetch();
    render(<RuleModal initial={telegramRule()} onClose={() => {}} onSave={async () => {}} />);
    const tokenInput = screen.getByPlaceholderText(/оставьте, чтобы не менять/) as HTMLInputElement;
    expect(tokenInput.value).toBe("••••");
    expect(tokenInput.type).toBe("password");
  });

  it("blocks saving a rule that has an empty-field condition", async () => {
    installFetch(); // validation blocks before any fetch
    const onSave = vi.fn(async () => {});
    render(<RuleModal initial={telegramRule()} onClose={() => {}} onSave={onSave} />);
    fireEvent.click(screen.getByText(/условие/)); // adds a row with an empty field
    fireEvent.click(screen.getByText("Сохранить"));
    expect(await screen.findByText(/Условие без поля/)).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("dry-run uses the stateless endpoint — no rule is created (no orphan)", async () => {
    let createCalled = false;
    route("POST", "/api/rules$", () => { createCalled = true; return { status: 201, body: telegramRule() }; });
    route("POST", "/api/rules/test$", () => ({
      body: {
        event: { type: "xray_down" },
        evaluation: { should_fire: true, reason: "matched", dry_run: false },
        plan: [{ type: "telegram", executed: false, dry_run: true, ok: true, plan: { text: "down de-1", chat_id: "42" } }],
      },
    }));
    installFetch();

    // A brand-new rule (initial=null): previewing must NOT persist anything.
    render(<RuleModal initial={null} onClose={() => {}} onSave={async () => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/Например/), { target: { value: "Тест" } });
    // add a telegram action + fill required fields
    fireEvent.change(screen.getByLabelText("Добавить действие"), { target: { value: "telegram" } });
    fireEvent.change(await screen.findByPlaceholderText(/123456:ABC/), { target: { value: "1:token" } });
    fireEvent.change(screen.getByPlaceholderText(/-1001234567890/), { target: { value: "42" } });
    fireEvent.click(screen.getByText("Проверить"));

    expect(await screen.findByText(/Условия выполнены — правило сработает/)).toBeInTheDocument();
    expect(screen.getByText(/«down de-1»/)).toBeInTheDocument();
    expect(createCalled).toBe(false); // the key assertion: nothing persisted
  });
});

describe("Notifications", () => {
  it("renders the quick-notification form with a masked (password) token field", async () => {
    route("GET", "/api/rules$", () => ({ body: [] }));
    installFetch();
    render(<Notifications />);
    expect(await screen.findByText(/Быстрое уведомление/)).toBeInTheDocument();
    const tokenInput = screen.getByPlaceholderText(/123456:ABC/) as HTMLInputElement;
    expect(tokenInput.type).toBe("password");
  });

  it("lists only rules that have a telegram action", async () => {
    const tg = telegramRule({ id: "a", name: "Notify-A" });
    const nodeOnly = telegramRule({
      id: "b", name: "NodeOnly",
      actions: [{ type: "node_disable", params: { node_uuid: "x" } }],
    });
    route("GET", "/api/rules$", () => ({ body: [tg, nodeOnly] }));
    installFetch();
    render(<Notifications />);
    expect(await screen.findByText("Notify-A")).toBeInTheDocument();
    expect(screen.queryByText("NodeOnly")).not.toBeInTheDocument();
  });

  it("blocks creating a notification without a token", async () => {
    let posted = false;
    route("GET", "/api/rules$", () => ({ body: [] }));
    route("POST", "/api/rules$", () => { posted = true; return { status: 201, body: telegramRule() }; });
    installFetch();
    render(<Notifications />);
    await screen.findByText(/Быстрое уведомление/);
    fireEvent.change(screen.getByPlaceholderText(/Падение ноды → Telegram/), { target: { value: "N" } });
    fireEvent.change(screen.getByPlaceholderText(/-1001234567890/), { target: { value: "42" } });
    fireEvent.click(screen.getByText(/Создать уведомление/));
    expect(await screen.findByText(/Укажите токен бота/)).toBeInTheDocument();
    expect(posted).toBe(false);
  });
});
