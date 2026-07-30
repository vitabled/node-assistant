import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Stub the whole App so the gate can be tested without mounting the real tree.
vi.mock("../App", () => ({ default: () => <div>APP_ROOT</div> }));

import { AuthGate } from "./AuthGate";
import { addAccount, forget, getSnapshot } from "./store";

function reset() {
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

describe("AuthGate", () => {
  beforeEach(reset);
  afterEach(cleanup);

  it("shows the login screen when no account is active", async () => {
    // Экран входа сначала спрашивает /api/auth/state («нужна ли первичная
    // настройка») и до ответа не рисует НИ ОДНОЙ формы — поэтому ждём её.
    (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({ bootstrap: false }),
    })) as unknown as typeof fetch;
    render(<AuthGate />);
    expect(await screen.findByText("Вход в аккаунт")).toBeInTheDocument();
    expect(screen.queryByText("APP_ROOT")).not.toBeInTheDocument();
  });

  it("renders the app when an account is active", () => {
    addAccount({ id: "id-a", login: "alice", token: "t" });
    render(<AuthGate />);
    expect(screen.getByText("APP_ROOT")).toBeInTheDocument();
    expect(screen.queryByText("Вход в аккаунт")).not.toBeInTheDocument();
  });
});
