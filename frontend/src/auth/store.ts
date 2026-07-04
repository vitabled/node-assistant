// Device-side account store (the "Google accounts on this device" model).
//
// Holds the set of accounts the user has signed into ON THIS DEVICE, each with
// its bearer token, plus which one is active. Persisted to localStorage so the
// session survives page reloads and browser restarts. Switching between added
// accounts is instant (token already stored); only adding a NEW account needs a
// password. This module is framework-agnostic (the fetch interceptor reads the
// active token from here); React subscribes via `useAuth` (useSyncExternalStore).

export interface DeviceAccount {
  id: string;
  login: string;
  token: string;
}

interface AuthState {
  accounts: DeviceAccount[];
  activeId: string | null;
}

const ACCOUNTS_KEY = "ni_accounts";
const ACTIVE_KEY = "ni_active_account";

function read(): AuthState {
  try {
    const accounts = JSON.parse(localStorage.getItem(ACCOUNTS_KEY) || "[]");
    const activeId = localStorage.getItem(ACTIVE_KEY);
    const list: DeviceAccount[] = Array.isArray(accounts) ? accounts : [];
    const active = list.some(a => a.id === activeId) ? activeId : null;
    return { accounts: list, activeId: active };
  } catch {
    return { accounts: [], activeId: null };
  }
}

let state: AuthState = read();
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function commit(next: AuthState) {
  state = next;
  try {
    localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(next.accounts));
    if (next.activeId) localStorage.setItem(ACTIVE_KEY, next.activeId);
    else localStorage.removeItem(ACTIVE_KEY);
  } catch {}
  emit();
}

// ── external-store API (for useSyncExternalStore) ──────────────
export function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}
export function getSnapshot(): AuthState {
  return state;
}

// ── reads ──────────────────────────────────────────────────────
export function getActiveId(): string | null {
  return state.activeId;
}
export function getActive(): DeviceAccount | null {
  return state.accounts.find(a => a.id === state.activeId) || null;
}
export function getActiveToken(): string {
  return getActive()?.token || "";
}

// ── mutations ──────────────────────────────────────────────────
/** Add (or refresh) an account and make it active. */
export function addAccount(acc: DeviceAccount) {
  const accounts = [acc, ...state.accounts.filter(a => a.id !== acc.id)];
  commit({ accounts, activeId: acc.id });
}

/** Switch to an already-added account (instant, no password). */
export function switchTo(id: string) {
  if (state.accounts.some(a => a.id === id)) {
    commit({ ...state, activeId: id });
  }
}

/** Forget an account on this device (server data is untouched). If it was the
 *  active one, fall back to the first remaining account, else signed-out. */
export function forget(id: string) {
  const accounts = state.accounts.filter(a => a.id !== id);
  const activeId = state.activeId === id ? (accounts[0]?.id ?? null) : state.activeId;
  commit({ accounts, activeId });
}

/** Log out of the active account (ends its session on this device). */
export function logoutActive() {
  if (state.activeId) forget(state.activeId);
}

// ── per-account storage-key helpers ────────────────────────────
export function deployJobsKey(id: string | null = state.activeId): string {
  return `deploy_jobs_${id ?? "none"}`;
}
export function tabKey(id: string | null = state.activeId): string {
  return `ni_tab_${id ?? "none"}`;
}

// ── password generator (Register screen) ───────────────────────
export function generatePassword(length = 20): string {
  const alphabet =
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*-_";
  const bytes = new Uint32Array(length);
  crypto.getRandomValues(bytes);
  let out = "";
  for (let i = 0; i < length; i++) out += alphabet[bytes[i] % alphabet.length];
  return out;
}
