// ============================================================
// «Хранилище» API client (Wave-9 Plan A Ф6) — /api/vault.
// Shapes mirror backend/app/api/vault.py + services/vault_store.py:_public()
// exactly. The list/create/update responses NEVER carry a secret value — a
// plaintext leaves the backend only through `revealEntry` and `downloadKey`.
// Auth is attached globally by the fetch interceptor (auth/apiClient.ts).
// ============================================================

export type VaultKind =
  | "api_key" | "ssh_password" | "ssh_key" | "login" | "provider_creds" | "note";

/** Field editor kind declared by `GET /api/vault/schemas`. */
export type VaultFieldKind = "text" | "password" | "textarea";

export interface VaultSchemaField {
  key: string;
  label: string;
  kind: VaultFieldKind;
  required: boolean;
}

export interface VaultSchema {
  kind: VaultKind;
  title: string;
  fields: VaultSchemaField[];
}

/** Public shape of one entry (`vault_store._public`) — secrets stripped. */
export interface VaultEntry {
  id: string;
  name: string;
  kind: VaultKind;
  resource: string;
  username: string;
  note: string;
  tags: string[];
  /** Keys present inside the encrypted blob (never their values). */
  field_names: string[];
  /** Masked prefix of the first value, e.g. `sk-a********`. */
  hint: string;
  has_secret: boolean;
  /** true = the ciphertext no longer decrypts (ENCRYPTION_KEY changed). */
  broken: boolean;
  created_at: number;
  updated_at: number;
  revealed_at: number | null;
}

export interface VaultEntryBody {
  name: string;
  kind: VaultKind;
  resource: string;
  username: string;
  note: string;
  tags: string[];
  fields: Record<string, string>;
}

/**
 * PUT body. Patch semantics on the backend: an omitted key is left alone — in
 * particular an omitted `fields` KEEPS the stored secret, so renaming an entry
 * never requires the client to hold its plaintext. A present `fields` REPLACES
 * the whole blob (it is one Fernet value, not a per-field row).
 */
export type VaultEntryPatch = Partial<VaultEntryBody>;

// ── errors ────────────────────────────────────────────────────
interface DetailItem { loc?: unknown[]; msg?: string }

/**
 * FastAPI error body → readable message. CRUCIAL: never surface the validation
 * `input` field — it echoes the value the user just typed, which here is a
 * plaintext secret (would defeat the masking wholesale).
 */
function formatError(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = (detail as DetailItem[]).map(e => {
      const loc = Array.isArray(e?.loc) ? e.loc.filter(x => x !== "body").join(".") : "";
      const msg = typeof e?.msg === "string" ? e.msg : "ошибка";
      return loc ? `${loc}: ${msg}` : msg;
    });
    return msgs.join("; ") || fallback;
  }
  return fallback;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw Object.assign(new Error(formatError(body, res.statusText)), { status: res.status });
  }
  return (res.status === 204 ? (undefined as T) : ((await res.json()) as T));
}

const JSON_HEADERS = { "Content-Type": "application/json" };

// ── CRUD ──────────────────────────────────────────────────────
export const listEntries = async (): Promise<VaultEntry[]> => {
  const data = await fetch("/api/vault").then(r => jsonOrThrow<VaultEntry[]>(r));
  return Array.isArray(data) ? data : [];
};

export const getSchemas = async (): Promise<VaultSchema[]> => {
  const data = await fetch("/api/vault/schemas").then(r => jsonOrThrow<VaultSchema[]>(r));
  return Array.isArray(data) ? data : [];
};

export const createEntry = (body: VaultEntryBody): Promise<VaultEntry> =>
  fetch("/api/vault", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) })
    .then(r => jsonOrThrow<VaultEntry>(r));

export const updateEntry = (id: string, patch: VaultEntryPatch): Promise<VaultEntry> =>
  fetch(`/api/vault/${id}`, { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(patch) })
    .then(r => jsonOrThrow<VaultEntry>(r));

export const deleteEntry = (id: string): Promise<void> =>
  fetch(`/api/vault/${id}`, { method: "DELETE" }).then(r => jsonOrThrow<void>(r));

/**
 * The only route that returns plaintext field values. POST (not GET) on purpose:
 * an id in a URL lands in access logs and browser history, and GET is exposed to
 * prefetch. Also bumps the entry's `revealed_at` audit stamp server-side.
 */
export const revealEntry = async (id: string): Promise<Record<string, string>> => {
  const data = await fetch(`/api/vault/${id}/reveal`, { method: "POST" })
    .then(r => jsonOrThrow<{ fields?: Record<string, string> }>(r));
  return data?.fields ?? {};
};

/** Private-key download path (`kind === "ssh_key"` only). */
export const downloadUrl = (id: string): string => `/api/vault/${id}/download`;

const safeFileName = (name: string): string =>
  name.replace(/[^A-Za-z0-9._-]/g, "") || "key";

/**
 * Download an SSH private key. Goes through fetch + Blob rather than a plain
 * `<a href>`: the bearer token is attached by the window.fetch interceptor, so a
 * bare link would reach the backend unauthenticated and 401.
 */
export async function downloadKey(entry: { id: string; name: string }): Promise<void> {
  const res = await fetch(downloadUrl(entry.id));
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(formatError(body, res.statusText));
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `${safeFileName(entry.name)}.pem`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── UI metadata ───────────────────────────────────────────────
/** Fallback titles — the authoritative list comes from `GET /api/vault/schemas`. */
export const KIND_LABELS: Record<VaultKind, string> = {
  api_key: "API-ключ",
  ssh_password: "SSH-пароль",
  ssh_key: "SSH-ключ",
  login: "Логин и пароль",
  provider_creds: "Доступ к хостинг-провайдеру",
  note: "Заметка",
};

/**
 * Which field is «the» secret when one value has to be copied in a single click.
 * Ordered by how the schemas name their primary field; anything unlisted falls
 * back to the first non-empty value.
 */
const PRIMARY_KEYS = ["password", "token", "private_key", "text", "secret"];

export function primaryValue(fields: Record<string, string>): string {
  for (const k of PRIMARY_KEYS) {
    if (fields[k]) return fields[k];
  }
  return Object.values(fields).find(v => !!v) ?? "";
}

/** Seconds a revealed value stays on screen before it is hidden again. */
export const REVEAL_TTL_MS = 30_000;
