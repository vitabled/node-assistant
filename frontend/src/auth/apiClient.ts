// Global fetch interceptor: attaches the active account's bearer token to every
// same-origin /api request and reacts to 401 by dropping the (now invalid)
// session. Installed once from main.tsx so all existing fetch call sites —
// including the infra-billing api.ts — get auth without per-call changes.

import { getActiveToken, getActiveId, getActiveInstanceId, forget } from "./store";

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

// Only our backend paths; leave Vite assets/HMR alone.
function isApi(url: string): boolean {
  return url.startsWith("/api") || url.includes("/api/");
}
function isAuthRoute(url: string): boolean {
  return url.includes("/api/auth/");
}

export function installApiClient() {
  const original = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = urlOf(input);

    if (isApi(url) && !isAuthRoute(url)) {
      const token = getActiveToken();
      if (token) {
        const headers = new Headers(
          init.headers ?? (input instanceof Request ? input.headers : undefined),
        );
        if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
        if (!headers.has("X-Instance-Id")) headers.set("X-Instance-Id", getActiveInstanceId());
        init = { ...init, headers };
      }
    }

    const res = await original(input, init);

    // Session no longer valid → forget it so the app returns to the login gate.
    // Разлогиниваем ТОЛЬКО по маркеру x-session-invalid: 401 от downstream
    // (например, панель Remnawave с плохим токеном, проброшенный старым
    // backend'ом) сессии не касается — иначе оператора выбивало бы в вечный
    // логаут (см. backend/app/api/downstream.py).
    if (res.status === 401 && isApi(url) && !isAuthRoute(url) &&
        res.headers.get("x-session-invalid") === "1") {
      const id = getActiveId();
      if (id) forget(id);
    }
    return res;
  };
}
