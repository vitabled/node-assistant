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

  // Regression: the add-account overlay must portal to <body> (not render inside
  // AccountMenu, whose topbar ancestor has backdrop-filter → a containing block
  // that clipped the fixed scrim to the 52px header and pushed the form up).
  it("portals the add-account overlay to document.body with a centered full-screen scrim", () => {
    const { container } = render(<AuthScreen overlay onClose={() => {}} />);
    expect(container.querySelector(".fixed")).toBeNull(); // nothing rendered in-place
    const scrim = Array.from(document.body.children).find(
      el => el.classList.contains("fixed") && el.classList.contains("inset-0"));
    expect(scrim).toBeTruthy();
    expect(scrim!.className).toContain("items-center");
    expect(scrim!.className).toContain("justify-center");
    // solid full-screen backdrop (matches the login gate)
    expect(scrim!.getAttribute("style") || "").toContain("var(--bg0)");
  });

  it("has no explicit close button and dismisses only on a click outside the form", () => {
    const onClose = vi.fn();
    render(<AuthScreen overlay onClose={onClose} />);
    const scrim = Array.from(document.body.children).find(
      el => el.classList.contains("fixed") && el.classList.contains("inset-0"))!;
    // no X / close button any more
    expect(scrim.querySelector("button.rounded-full")).toBeNull();
    // clicking inside the form must NOT close
    fireEvent.mouseDown(screen.getByPlaceholderText("Логин"));
    expect(onClose).not.toHaveBeenCalled();
    // clicking the backdrop itself closes
    fireEvent.mouseDown(scrim);
    expect(onClose).toHaveBeenCalledTimes(1);
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
