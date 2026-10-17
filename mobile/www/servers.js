// Список NA-серверов на устройстве (только адреса и имена — без токенов:
// сессией владеет сам веб-интерфейс NA на своём origin).
export const STORE_KEY = "na_mobile_servers";
export const AUTO_KEY = "na_mobile_autoopen";

/** "panel.example.com" / "http://1.2.3.4:8080/" → origin, или null если адрес негодный. */
export function normalizeUrl(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(s) ? s : `https://${s}`;
  let u;
  try { u = new URL(withScheme); } catch { return null; }
  if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  if (!u.hostname) return null;
  return u.origin;
}

export function loadServers(storage) {
  try {
    const arr = JSON.parse(storage.getItem(STORE_KEY) || "[]");
    return Array.isArray(arr) ? arr.filter(s => s && typeof s.url === "string" && normalizeUrl(s.url)) : [];
  } catch { return []; }
}

export function saveServers(storage, list) {
  storage.setItem(STORE_KEY, JSON.stringify(list));
}

/** Добавляет/поднимает сервер в начало списка (последний открытый — первый). */
export function upsertServer(list, url, name) {
  const origin = normalizeUrl(url);
  if (!origin) throw new Error("Некорректный адрес сервера");
  const prev = list.find(s => s.url === origin);
  const entry = { url: origin, name: (name ?? "").trim() || prev?.name || new URL(origin).host };
  return [entry, ...list.filter(s => s.url !== origin)];
}

export function removeServer(list, url) {
  return list.filter(s => s.url !== url);
}

/** Сервер для автозапуска: только если включено и список не пуст. ?setup в адресе — всегда экран выбора. */
export function autoOpenTarget(storage, search) {
  if (new URLSearchParams(search).has("setup")) return null;
  if (storage.getItem(AUTO_KEY) !== "1") return null;
  return loadServers(storage)[0]?.url ?? null;
}
