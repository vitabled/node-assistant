import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { usePermissions, resetIdentity } from "./usePermissions";
import { addAccount, forget, getSnapshot } from "./store";

const ME = {
  id: "id-a", login: "alice", is_superuser: false, disabled: false,
  role_ids: ["operator"], roles: [{ id: "operator", name: "Оператор" }],
  permissions: ["deploy.view", "certs.view"],
};

function mockFetch(reply: { status?: number; body?: unknown }) {
  const fn = vi.fn(async () => ({
    ok: (reply.status ?? 200) < 400,
    status: reply.status ?? 200,
    json: async () => reply.body ?? {},
  }));
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fn as unknown as typeof fetch;
  return fn;
}

describe("usePermissions", () => {
  beforeEach(() => {
    localStorage.clear();
    getSnapshot().accounts.slice().forEach(a => forget(a.id));
    resetIdentity();
    addAccount({ id: "id-a", login: "alice", token: "TKN" });
  });

  it("loads the identity and answers can() by membership", async () => {
    const fetchFn = mockFetch({ body: ME });
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user?.roles[0].name).toBe("Оператор");
    expect(result.current.can("deploy.view")).toBe(true);
    expect(result.current.can("vault.view")).toBe(false);
    // ⚠️ Токен ставим сами: глобальный перехват fetch пропускает /api/auth/*.
    const init = fetchFn.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer TKN");
  });

  // Один запрос на всех потребителей (сайдбар, App, меню аккаунта) — ради этого
  // модуль и сделан синглтоном.
  it("fetches /api/auth/me once for several consumers", async () => {
    const fetchFn = mockFetch({ body: ME });
    const a = renderHook(() => usePermissions());
    const b = renderHook(() => usePermissions());
    await waitFor(() => expect(a.result.current.loading).toBe(false));
    await waitFor(() => expect(b.result.current.can("deploy.view")).toBe(true));
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  // Права неизвестны → разрешаем: спрятать всё значило бы показать пустую панель,
  // причём навсегда. Реальный отказ придёт с сервера как 403.
  it("allows everything while the identity is unknown", async () => {
    mockFetch({ status: 500 });
    const { result } = renderHook(() => usePermissions());
    expect(result.current.can("vault.view")).toBe(true);   // ещё грузим
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.can("vault.view")).toBe(true);   // запрос не удался
  });

  // Смена пароля или ролей бампит token_version на сервере → 401. Оставить
  // человека в панели без прав нельзя, возвращаем на экран входа.
  it("drops the device session when /me answers 401", async () => {
    mockFetch({ status: 401 });
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(getSnapshot().activeId).toBeNull());
    expect(result.current.user).toBeNull();
  });
});
