import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthScreen } from "./AuthScreen";
import { getSnapshot, forget } from "./store";

function reset() {
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

interface Reply { ok?: boolean; status?: number; body?: unknown }

/** Мок fetch с маршрутизацией по URL: экран теперь спрашивает /api/auth/state
 *  перед отрисовкой любой формы, поэтому один общий ответ уже не годится. */
function api(handler: (url: string, init?: RequestInit) => Reply | Promise<Reply>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const r = await handler(String(input), init);
    const ok = r.ok ?? true;
    return {
      ok,
      status: r.status ?? (ok ? 200 : 400),
      statusText: "err",
      json: async () => r.body ?? {},
    };
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fn as unknown as typeof fetch;
  return fn;
}

const STATE = "/api/auth/state";
/** Установка уже настроена: `state` отвечает bootstrap:false, POST — по `post`. */
const configured = (post: (url: string, init?: RequestInit) => Reply = () => ({})) =>
  api((url, init) => (url.includes(STATE) ? { body: { bootstrap: false } } : post(url, init)));
/** Свежая установка: владельца ещё нет. */
const fresh = (post: (url: string, init?: RequestInit) => Reply = () => ({})) =>
  api((url, init) => (url.includes(STATE) ? { body: { bootstrap: true } } : post(url, init)));

const setInput = (ph: string, v: string) =>
  fireEvent.change(screen.getByPlaceholderText(ph), { target: { value: v } });

const bodyOf = (call: unknown[]): Record<string, unknown> =>
  JSON.parse(String((call[1] as RequestInit).body));

describe("AuthScreen", () => {
  beforeEach(reset);
  afterEach(cleanup);

  // ── первичная настройка ─────────────────────────────────────
  // Мигание «Первичная настройка → Вход» на первом запуске читается как сбой
  // панели, поэтому до ответа сервера не показываем НИ ОДНОЙ формы.
  it("shows no form until /api/auth/state answers", async () => {
    api(() => new Promise<Reply>(() => {}));   // ответ не приходит никогда
    render(<AuthScreen />);
    expect(await screen.findByText(/Проверяем установку/)).toBeInTheDocument();
    expect(screen.queryByText("Вход в аккаунт")).toBeNull();
    expect(screen.queryByText("Первичная настройка")).toBeNull();
    expect(screen.queryByPlaceholderText("Логин")).toBeNull();
  });

  it("renders the owner-creation form when the install needs bootstrap", async () => {
    fresh();
    render(<AuthScreen />);
    expect(await screen.findByText("Первичная настройка")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Пароль ещё раз")).toBeInTheDocument();
    expect(screen.queryByText("Вход в аккаунт")).toBeNull();
  });

  it("creates the owner and activates the returned session", async () => {
    const fetchFn = fresh(() => ({ status: 201, body: { id: "id-owner", login: "root", token: "T" } }));
    render(<AuthScreen />);
    await screen.findByText("Первичная настройка");
    setInput("Логин", "root");
    setInput("Пароль", "long-enough-pw");
    setInput("Пароль ещё раз", "long-enough-pw");
    fireEvent.click(screen.getByRole("button", { name: /Создать владельца/ }));
    await waitFor(() => expect(getSnapshot().activeId).toBe("id-owner"));
    const post = fetchFn.mock.calls.find(c => String(c[0]).includes("/api/auth/bootstrap"))!;
    expect(bodyOf(post)).toMatchObject({ login: "root", password: "long-enough-pw" });
  });

  it("mirrors the server password policy and the confirmation without calling the API", async () => {
    const fetchFn = fresh();
    render(<AuthScreen />);
    await screen.findByText("Первичная настройка");
    setInput("Логин", "root");
    setInput("Пароль", "short");
    setInput("Пароль ещё раз", "short");
    fireEvent.click(screen.getByRole("button", { name: /Создать владельца/ }));
    expect(screen.getByText("Пароль короче 10 символов")).toBeInTheDocument();

    setInput("Пароль", "long-enough-pw");
    setInput("Пароль ещё раз", "long-enough-pX");
    fireEvent.click(screen.getByRole("button", { name: /Создать владельца/ }));
    expect(screen.getByText("Пароли не совпадают")).toBeInTheDocument();
    expect(fetchFn.mock.calls.some(c => String(c[0]).includes("/bootstrap"))).toBe(false);
  });

  it("generates a password into both fields and warns that it is not stored", async () => {
    fresh();
    render(<AuthScreen />);
    await screen.findByText("Первичная настройка");
    expect(screen.queryByText(/скопируйте его сейчас/)).toBeNull();
    fireEvent.click(screen.getByText("Сгенерировать пароль"));
    const pw = screen.getByPlaceholderText("Пароль") as HTMLInputElement;
    const confirm = screen.getByPlaceholderText("Пароль ещё раз") as HTMLInputElement;
    expect(pw.value).toHaveLength(20);
    // Сгенерированный пароль руками не набирали — подтверждение заполняется само.
    expect(confirm.value).toBe(pw.value);
    expect(screen.getByText(/скопируйте его сейчас/)).toBeInTheDocument();
    setInput("Пароль", "manual-password");
    expect(screen.queryByText(/скопируйте его сейчас/)).toBeNull();
  });

  it("keeps the bootstrap token collapsed and sends it once filled", async () => {
    const fetchFn = fresh(() => ({ status: 201, body: { id: "id-owner", login: "root", token: "T" } }));
    render(<AuthScreen />);
    await screen.findByText("Первичная настройка");
    expect(screen.queryByPlaceholderText("Токен первичной настройки")).toBeNull();
    fireEvent.click(screen.getByText("Установка защищена токеном"));
    setInput("Логин", "root");
    setInput("Пароль", "long-enough-pw");
    setInput("Пароль ещё раз", "long-enough-pw");
    setInput("Токен первичной настройки", "boot-secret");
    fireEvent.click(screen.getByRole("button", { name: /Создать владельца/ }));
    await waitFor(() => expect(getSnapshot().activeId).toBe("id-owner"));
    const post = fetchFn.mock.calls.find(c => String(c[0]).includes("/api/auth/bootstrap"))!;
    expect(bodyOf(post).bootstrap_token).toBe("boot-secret");
  });

  it("surfaces a bootstrap refusal from the server", async () => {
    fresh(() => ({ ok: false, status: 403, body: { detail: "Неверный токен первичной настройки" } }));
    render(<AuthScreen />);
    await screen.findByText("Первичная настройка");
    setInput("Логин", "root");
    setInput("Пароль", "long-enough-pw");
    setInput("Пароль ещё раз", "long-enough-pw");
    fireEvent.click(screen.getByRole("button", { name: /Создать владельца/ }));
    await waitFor(() =>
      expect(screen.getByText("Неверный токен первичной настройки")).toBeInTheDocument());
    expect(getSnapshot().activeId).toBeNull();
  });

  // ── вход ────────────────────────────────────────────────────
  it("renders the login form on a configured install, with no way to self-register", async () => {
    configured();
    render(<AuthScreen />);
    expect(await screen.findByText("Вход в аккаунт")).toBeInTheDocument();
    expect(screen.queryByText(/Регистрация/)).toBeNull();
    expect(screen.queryByText(/Нет аккаунта/)).toBeNull();
    expect(screen.queryByText("Первичная настройка")).toBeNull();
  });

  it("validates empty fields without calling the API", async () => {
    const fetchFn = configured();
    render(<AuthScreen />);
    await screen.findByText("Вход в аккаунт");
    fireEvent.click(screen.getByRole("button", { name: /Войти/ }));
    expect(screen.getByText("Введите логин и пароль")).toBeInTheDocument();
    expect(fetchFn.mock.calls.some(c => String(c[0]).includes("/api/auth/login"))).toBe(false);
  });

  it("logs in and activates the returned account", async () => {
    configured(() => ({ body: { id: "id-a", login: "alice", token: "T" } }));
    render(<AuthScreen />);
    await screen.findByText("Вход в аккаунт");
    setInput("Логин", "alice");
    setInput("Пароль", "pw");
    fireEvent.click(screen.getByRole("button", { name: /Войти/ }));
    await waitFor(() => expect(getSnapshot().activeId).toBe("id-a"));
  });

  it("shows the backend error on a failed login", async () => {
    configured(() => ({ ok: false, status: 401, body: { detail: "Неверный логин или пароль" } }));
    render(<AuthScreen />);
    await screen.findByText("Вход в аккаунт");
    setInput("Логин", "alice");
    setInput("Пароль", "bad");
    fireEvent.click(screen.getByRole("button", { name: /Войти/ }));
    await waitFor(() => expect(screen.getByText("Неверный логин или пароль")).toBeInTheDocument());
    expect(getSnapshot().activeId).toBeNull();
  });

  // ── overlay («Войти другим пользователем») ───────────────────
  // Мы уже внутри панели: владелец создан по определению, и лишнего запроса
  // (а с ним и задержки открытия) быть не должно.
  it("skips the state probe in overlay mode and shows the login form at once", () => {
    const fetchFn = api(() => ({ body: {} }));
    render(<AuthScreen overlay onClose={() => {}} />);
    expect(screen.getByText("Вход в аккаунт")).toBeInTheDocument();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  // Regression: the add-account overlay must portal to <body> (not render inside
  // AccountMenu, whose topbar ancestor has backdrop-filter → a containing block
  // that clipped the fixed scrim to the 52px header and pushed the form up).
  it("portals the overlay to document.body with a centered full-screen scrim", () => {
    api(() => ({ body: {} }));
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
    api(() => ({ body: {} }));
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
});
