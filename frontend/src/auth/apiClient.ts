// Global fetch interceptor: attaches the active account's bearer token to every
// same-origin /api request and reacts to 401 by dropping the (now invalid)
// session. Installed once from main.tsx so all existing fetch call sites —
// including the infra-billing api.ts — get auth without per-call changes.

import { getActiveToken, getActiveId, forget } from "./store";

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
        init = { ...init, headers };
      }
    }

    const res = await original(input, init);

    // Session no longer valid → forget it so the app returns to the login gate.
    if (res.status === 401 && isApi(url) && !isAuthRoute(url)) {
      const id = getActiveId();
      if (id) forget(id);
    }
    return res;
  };
}
