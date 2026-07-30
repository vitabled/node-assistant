// Сессии чата ассистента: переписка в localStorage, ПЕР-ПОЛЬЗОВАТЕЛЬСКИ.
//
// Сервер переписку не хранит принципиально (историю ведёт клиент и присылает в
// теле каждого запроса — см. §20f в CLAUDE.md), поэтому единственное место, где
// разговор может пережить F5, — браузер. Ключ по ЛИЧНОСТИ, а не по устройству:
// за одним браузером работают разные пользователи панели, и чужая переписка в
// чужом чате — утечка (в ней и содержимое заметок, и ответы ручек).
//
// Модуль намеренно без React: всё, кроме `load`/`save`, — чистые преобразования
// состояния, поэтому лимиты и вытеснение проверяются юнит-тестом без рендера.

/** `text` — ровно то, что уходит в историю. Имена вложений держим ОТДЕЛЬНЫМ
 *  полем, а не приписываем к тексту: вложения эфемерны, и в истории следующего
 *  хода строка «📎 …» была бы враньём — файлов у модели там уже нет. */
export type Msg =
  | { role: "user"; text: string; files?: string[] }
  | { role: "assistant"; text: string; tools: { id?: string; name: string; ok?: boolean }[] };

export interface Session {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  messages: Msg[];
}

export interface SessionsState {
  sessions: Session[];
  activeId: string;
}

/** Лимиты существуют не для красоты: квота localStorage — около 5 МБ на origin,
 *  а её делят карточки деплоя (там SSH-креды), профили Xray и раскладки
 *  виджетов. Переполнение выбрасывает исключение на КАЖДУЮ запись, и без
 *  потолка чат утащил бы за собой чужие данные. Числа выбраны так, чтобы
 *  типичная переписка (сообщение ≈ 1 КБ) укладывалась в сотни килобайт. */
const MAX_SESSIONS = 20;
const MAX_MESSAGES = 200;
const TITLE_LEN = 40;

export function sessionsKey(userId?: string | null): string {
  return `ai_sessions_${userId || "none"}`;
}

const newId = () =>
  globalThis.crypto?.randomUUID?.() ?? `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

function blank(): Session {
  const now = Date.now();
  return { id: newId(), title: "", created_at: now, updated_at: now, messages: [] };
}

// ── чтение и запись ────────────────────────────────────────────

/** Разбор того, что лежит в хранилище. Правится руками и переживает смену
 *  формата, поэтому проверяем форму, а не доверяем ей. Список НИКОГДА не
 *  пустой — так у вызывающего нет ветки «активной сессии нет». */
function normalize(raw: any): SessionsState {
  const parsed = Array.isArray(raw?.sessions)
    ? (raw.sessions.map(normSession).filter(Boolean) as Session[])
    : [];
  // Хранится в порядке создания (новые дописываются в конец), поэтому лишнее
  // режем с головы — там самые старые.
  const sessions = parsed.length ? parsed.slice(-MAX_SESSIONS) : [blank()];
  const activeId = sessions.some(s => s.id === raw?.activeId)
    ? String(raw.activeId)
    : sessions[sessions.length - 1].id;
  return { sessions, activeId };
}

function normSession(raw: any): Session | null {
  if (!raw || typeof raw !== "object" || typeof raw.id !== "string" || !raw.id) return null;
  const msgs = Array.isArray(raw.messages)
    ? (raw.messages.map(normMsg).filter(Boolean) as Msg[]).slice(-MAX_MESSAGES)
    : [];
  const created = Number(raw.created_at) || Date.now();
  return {
    id: raw.id,
    title: typeof raw.title === "string" ? raw.title.slice(0, TITLE_LEN) : "",
    created_at: created,
    updated_at: Number(raw.updated_at) || created,
    messages: msgs,
  };
}

function normMsg(raw: any): Msg | null {
  if (!raw || typeof raw !== "object" || typeof raw.text !== "string") return null;
  if (raw.role === "user")
    return { role: "user", text: raw.text, ...(Array.isArray(raw.files) ? { files: raw.files.map(String) } : {}) };
  if (raw.role === "assistant")
    return {
      role: "assistant",
      text: raw.text,
      tools: Array.isArray(raw.tools)
        ? raw.tools.filter((t: any) => t && typeof t.name === "string").map((t: any) => ({ id: t.id, name: t.name, ok: t.ok }))
        : [],
    };
  return null;
}

export function load(userId?: string | null): SessionsState {
  let raw: any = null;
  try { raw = JSON.parse(localStorage.getItem(sessionsKey(userId)) || "null"); } catch { /* мусор в ключе */ }
  return normalize(raw);
}

export function save(userId: string | null | undefined, state: SessionsState): void {
  try {
    localStorage.setItem(sessionsKey(userId), JSON.stringify(state));
  } catch {
    // Квота кончилась (или приватный режим запрещает запись). Ронять чат из-за
    // этого нельзя: переписка не настолько ценна, чтобы ради неё терять
    // работоспособность страницы. Разговор просто не переживёт перезагрузку.
  }
}

// ── чистые преобразования ──────────────────────────────────────

/** Для показа — новые сверху; хранится наоборот (порядок создания). */
export function listSessions(state: SessionsState): Session[] {
  return [...state.sessions].reverse();
}

export function getActive(state: SessionsState): Session {
  return state.sessions.find(s => s.id === state.activeId) || state.sessions[state.sessions.length - 1];
}

export function setActive(state: SessionsState, id: string): SessionsState {
  return state.sessions.some(s => s.id === id) ? { ...state, activeId: id } : state;
}

export function newSession(state: SessionsState): SessionsState {
  const s = blank();
  return trim({ sessions: [...state.sessions, s], activeId: s.id });
}

/** Удалили последнюю — заводим пустую: инвариант «активная сессия есть всегда»
 *  избавляет вызывающего от ветки «а если её нет». */
export function removeSession(state: SessionsState, id: string): SessionsState {
  const rest = state.sessions.filter(s => s.id !== id);
  const sessions = rest.length ? rest : [blank()];
  const activeId = sessions.some(s => s.id === state.activeId)
    ? state.activeId
    : sessions[sessions.length - 1].id;
  return { sessions, activeId };
}

/** Очистка ТЕКУЩЕЙ сессии: сама сессия остаётся. Заголовок тоже сбрасываем —
 *  он выведен из первого сообщения, а его больше нет; после следующего вопроса
 *  имя соберётся заново. */
export function clearActive(state: SessionsState): SessionsState {
  return patchActive(state, () => ({ title: "", messages: [] }));
}

export function replaceMessages(state: SessionsState, messages: Msg[]): SessionsState {
  return patchActive(state, cur => ({ title: cur.title || titleOf(messages), messages: cap(messages) }));
}

export function appendMessages(state: SessionsState, messages: Msg[]): SessionsState {
  return patchActive(state, cur => {
    const next = [...cur.messages, ...messages];
    return { title: cur.title || titleOf(next), messages: cap(next) };
  });
}

export function renameActive(state: SessionsState, title: string): SessionsState {
  return patchActive(state, () => ({ title: title.trim().slice(0, TITLE_LEN) }));
}

// ── внутреннее ─────────────────────────────────────────────────

function patchActive(state: SessionsState, fn: (cur: Session) => Partial<Session>): SessionsState {
  const cur = getActive(state);
  const next: Session = { ...cur, ...fn(cur), updated_at: Date.now() };
  return { ...state, sessions: state.sessions.map(s => (s.id === cur.id ? next : s)) };
}

/** Режем с головы: свежие реплики нужнее, и именно их читает модель. */
const cap = (m: Msg[]) => (m.length > MAX_MESSAGES ? m.slice(-MAX_MESSAGES) : m);

const titleOf = (m: Msg[]) => (m.find(x => x.role === "user" && x.text.trim())?.text.trim() || "").slice(0, TITLE_LEN);

/** Вытесняем самые давно не тронутые. Ничью разрешаем порядком в массиве (= по
 *  созданию), иначе результат зависел бы от разрешения часов: несколько сессий
 *  подряд получают одинаковый `Date.now()`. Открытую сессию не трогаем никогда. */
function trim(state: SessionsState): SessionsState {
  if (state.sessions.length <= MAX_SESSIONS) return state;
  const pos = new Map(state.sessions.map((s, i) => [s.id, i]));
  const doomed = new Set(
    state.sessions
      .filter(s => s.id !== state.activeId)
      .sort((a, b) => a.updated_at - b.updated_at || pos.get(a.id)! - pos.get(b.id)!)
      .slice(0, state.sessions.length - MAX_SESSIONS)
      .map(s => s.id),
  );
  return { ...state, sessions: state.sessions.filter(s => !doomed.has(s.id)) };
}
