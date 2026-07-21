// Pure helpers for the reusable HeadersEditor (Wave-5 Plan F). No dependencies.

// RFC 7230 header field-name token.
export const HEADER_NAME_RE = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/;

export function isValidHeaderName(name: string): boolean {
  return HEADER_NAME_RE.test(name);
}

// Strip CR/LF — prevents header/response-splitting injection into nginx/Xray.
export function sanitizeHeaderValue(v: string): string {
  return String(v ?? "").replace(/[\r\n]/g, "");
}

export interface HeaderRow { name: string; value: string }

export function recordToRows(rec: Record<string, string> | undefined | null): HeaderRow[] {
  return Object.entries(rec ?? {}).map(([name, value]) => ({ name, value: String(value) }));
}

// Normalise editor rows → a Record. Invalid/empty names are dropped; on a
// duplicate name the last value wins (matches wsSettings.headers semantics).
export function rowsToRecord(rows: HeaderRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) {
    const n = r.name.trim();
    if (!n || !isValidHeaderName(n)) continue;
    out[n] = sanitizeHeaderValue(r.value);
  }
  return out;
}

export const HEADER_PRESETS: { name: string; placeholder: string }[] = [
  { name: "Host", placeholder: "example.com" },
  { name: "User-Agent", placeholder: "Mozilla/5.0" },
  { name: "X-Forwarded-For", placeholder: "1.2.3.4" },
  { name: "Accept", placeholder: "*/*" },
  { name: "Connection", placeholder: "keep-alive" },
];
