import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthScreen } from "./AuthScreen";
import { getSnapshot, forget } from "./store";

function reset() {
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

function mockFetch(resp: { ok: boolean; status?: number; body: unknown }) {
  const fn = vi.fn(async () => ({
    ok: resp.ok,
    status: resp.status ?? (resp.ok ? 200 : 400),
    statusText: "err",
    json: async () => resp.body,
  }));
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fn as unknown as typeof fetch;
  return fn;
}

const setInput = (ph: string, v: string) =>
  fireEvent.change(screen.getByPlaceholderText(ph), { target: { value: v } });

describe("AuthScreen", () => {
  beforeEach(reset);
  afterEach(cleanup);

  it("renders the login form by default", () => {
    render(<AuthScreen />);
    expect(screen.getByText("Вход в аккаунт")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Логин")).toBeInTheDocument();
  });

  it("validates empty fields without calling the API", () => {
    const fetchFn = mockFetch({ ok: true, body: {} });
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: /Войти/ }));
    expect(screen.getByText("Введите логин и пароль")).toBeInTheDocument();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("logs in and activates the returned account", async () => {
    mockFetch({ ok: true, body: { id: "id-a", login: "alice", token: "T" } });
    render(<AuthScreen />);
    setInput("Логин", "alice");
    setInput("Пароль", "pw");
    fireEvent.click(screen.getByRole("button", { name: /Войти/ }));
    await waitFor(() => expect(getSnapshot().activeId).toBe("id-a"));
  });

  it("shows the backend error on a failed login", async () => {
    mockFetch({ ok: false, status: 401, body: { detail: "Неверный логин или пароль" } });
    render(<AuthScreen />);
    setInput("Логин", "alice");
    setInput("Пароль", "bad");
    fireEvent.click(screen.getByRole("button", { name: /Войти/ }));
    await waitFor(() => expect(screen.getByText("Неверный логин или пароль")).toBeInTheDocument());
    expect(getSnapshot().activeId).toBeNull();
  });

  it("switches to register and generates a strong password into the field", () => {
    render(<AuthScreen />);
    fireEvent.click(screen.getByText("Нет аккаунта? Регистрация"));
    expect(screen.getByText("Регистрация")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Сгенерировать пароль"));
    const pw = screen.getByPlaceholderText("Пароль") as HTMLInputElement;
    expect(pw.value).toHaveLength(20);
  });

  it("registers and activates the new account", async () => {
    mockFetch({ ok: true, body: { id: "id-new", login: "newbie", token: "T2" } });
    render(<AuthScreen />);
    fireEvent.click(screen.getByText("Нет аккаунта? Регистрация"));
    setInput("Логин", "newbie");
    setInput("Пароль", "pw");
    fireEvent.click(screen.getByRole("button", { name: /Создать и войти/ }));
    await waitFor(() => expect(getSnapshot().activeId).toBe("id-new"));
  });

  it("surfaces a duplicate-login (409) error on register", async () => {
    mockFetch({ ok: false, status: 409, body: { detail: "Логин уже занят" } });
    render(<AuthScreen />);
    fireEvent.click(screen.getByText("Нет аккаунта? Регистрация"));
    setInput("Логин", "taken");
    setInput("Пароль", "pw");
    fireEvent.click(screen.getByRole("button", { name: /Создать и войти/ }));
    await waitFor(() => expect(screen.getByText("Логин уже занят")).toBeInTheDocument());
  });
});
