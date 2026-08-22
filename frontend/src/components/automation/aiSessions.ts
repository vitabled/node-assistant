// Сессии чата ассистента: источник истины — СЕРВЕР, localStorage — кэш.
//
// Как было и почему сломалось. Переписка жила только в localStorage, потому что
// сервер её принципиально не хранил (историю вёл клиент и присылал в теле
// каждого запроса). Это работало, пока хранилище браузера считалось надёжным —
// а оно не надёжно ни в одном из трёх частых случаев: Safari стирает данные
// сайтов, куда не заходили 7 дней (ITP); приватное окно не переживает закрытия
// вкладки; «очистить данные сайта» уносит переписку вместе с кэшем. Отсюда и
// «долго не заходил — история пропала»: теряли её НЕ мы, но выглядело как наш
// баг, и контекст длинной работы с панелью пропадал целиком.
//
// Как стало. Разговор пишется на сервер (`/api/ai/chat/history`, per-account
// JSON в каталоге аккаунта). localStorage остался, но сменил роль: теперь это
// КЭШ — мгновенная отрисовка до ответа сети и запасной вариант, когда сеть не
// ответила. При расхождении побеждает сервер.
//
// Ключ кэша по-прежнему по ЛИЧНОСТИ, а не по устройству: за одним браузером
// работают разные пользователи панели, и чужая переписка в чужом чате — утечка.
//
// Модуль намеренно без React: всё, кроме `load`/`save` и сетевых функций, —
// чистые преобразования состояния, поэтому лимиты и вытеснение проверяются
// юнит-тестом без рендера.

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
 *  типичная переписка (сообщение ≈ 1 КБ) укладывалась в сотни килобайт.
 *
 *  ⚠️ ТЕ ЖЕ числа стоят на сервере (`services/ai_chat_store.py`) намеренно:
 *  разойдись они — сервер молча резал бы то, что клиент считает сохранённым, и
 *  пропажа хвоста выглядела бы как баг синхронизации. */
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
    // этого нельзя: это ВСЕГО ЛИШЬ кэш — переписка уже уехала (или уедет) на
    // сервер, и после перезагрузки она придёт оттуда. Раньше на этом месте
    // разговор терялся навсегда.
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

// ── сервер: источник истины ────────────────────────────────────
//
// Ниже — единственная часть модуля, которая ходит в сеть. Правило одно: НИ ОДНА
// из этих функций не бросает. Переписка ценна, но не настолько, чтобы её
// недоступность запирала чат: не ответил сервер — работаем на кэше, как раньше,
// и следующая же удачная запись всё догонит.

const HISTORY_URL = "/api/ai/chat/history";

/** Форма реплики на проводе. Отличается от `Msg` тем, что текст называется
 *  `content` (как у модели и у ручки `/api/ai/chat`), а роль — плоская строка. */
interface WireMsg {
  role: string;
  content: string;
  ts?: number;
  files?: string[];
  tools?: { id?: string; name: string; ok?: boolean }[];
}

const toWire = (m: Msg): WireMsg =>
  m.role === "user"
    ? { role: "user", content: m.text, ...(m.files?.length ? { files: m.files } : {}) }
    : { role: "assistant", content: m.text, ...(m.tools.length ? { tools: m.tools } : {}) };

function fromWire(raw: any): Msg | null {
  if (!raw || typeof raw !== "object" || typeof raw.content !== "string") return null;
  if (raw.role === "user")
    return { role: "user", text: raw.content,
             ...(Array.isArray(raw.files) && raw.files.length ? { files: raw.files.map(String) } : {}) };
  if (raw.role === "assistant")
    return {
      role: "assistant", text: raw.content,
      tools: Array.isArray(raw.tools)
        ? raw.tools.filter((t: any) => t && typeof t.name === "string")
            .map((t: any) => ({ id: t.id, name: t.name, ok: t.ok }))
        : [],
    };
  return null;
}

/** Все разговоры с сервера. `null` — «сервер не ответил», и это НЕ то же самое,
 *  что пустой список: на пустом списке кэш надо стереть (историю удалили с
 *  другого устройства), а на отказе — сохранить. */
export async function fetchAll(): Promise<Session[] | null> {
  try {
    const res = await fetch(`${HISTORY_URL}?all_sessions=true`);
    if (!res.ok) return null;
    const data = await res.json();
    if (!Array.isArray(data?.sessions)) return null;
    return data.sessions.map((s: any): Session => {
      const messages = (Array.isArray(s.messages) ? s.messages : [])
        .map(fromWire).filter(Boolean) as Msg[];
      const ts = (Number(s.updated_at) || 0) * 1000 || Date.now();
      return { id: String(s.session_id), title: titleOf(messages),
               created_at: ts, updated_at: ts, messages: cap(messages) };
    });
  } catch {
    return null;
  }
}

/** Дописать реплики в разговор. Именно этим ходом переписка становится
 *  durable: вызывается на КАЖДОЕ сообщение, а не по таймеру — таймер потерял бы
 *  последний ход при закрытии вкладки. */
export async function pushAppend(sessionId: string, messages: Msg[]): Promise<void> {
  if (!messages.length) return;
  try {
    await fetch(HISTORY_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, append: true,
                             messages: messages.map(toWire) }),
    });
  } catch { /* офлайн: реплика осталась в кэше, сервер догонит следующей записью */ }
}

/** Перезаписать разговор целиком: миграция из localStorage, `/compact`
 *  (переписка заменяется выжимкой) и `/clear` (пустым списком). */
export async function pushReplace(sessionId: string, messages: Msg[]): Promise<void> {
  try {
    await fetch(HISTORY_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, messages: messages.map(toWire) }),
    });
  } catch { /* см. pushAppend */ }
}

export async function pushDelete(sessionId: string): Promise<void> {
  try {
    await fetch(`${HISTORY_URL}?session_id=${encodeURIComponent(sessionId)}`,
                { method: "DELETE" });
  } catch { /* см. pushAppend */ }
}

/** Свести кэш браузера с сервером. Возвращает состояние, которое надо показать.
 *
 *  ⚠️ Здесь живёт вся политика «кто прав», и она НЕ симметрична:
 *
 *  1. Сервер не ответил → отдаём кэш как есть. Чат обязан работать офлайн.
 *  2. На сервере пусто, а в кэше что-то есть → МИГРАЦИЯ: заливаем локальное
 *     туда. Это единственный случай, когда клиент диктует серверу, и он
 *     одноразовый — ровно переход со старой схемы хранения.
 *  3. Иначе побеждает сервер. Разговоры, которых на нём нет, НЕ подмешиваем:
 *     их отсутствие — это чаще всего осознанное удаление с другого устройства,
 *     и воскрешать их значило бы делать удаление невозможным.
 */
export async function syncFromServer(local: SessionsState): Promise<SessionsState> {
  const remote = await fetchAll();
  if (remote === null) return local;

  const localHas = local.sessions.some(s => s.messages.length > 0);
  if (!remote.length) {
    if (!localHas) return local;
    // Миграция: заливаем каждый непустой разговор из кэша.
    await Promise.all(local.sessions.filter(s => s.messages.length)
      .map(s => pushReplace(s.id, s.messages)));
    return local;
  }

  // Порядок с сервера — свежие первыми, а храним мы в порядке создания.
  const sessions = [...remote].reverse().slice(-MAX_SESSIONS);
  // Открытый разговор сохраняем, если он уцелел; иначе показываем самый свежий.
  const activeId = sessions.some(s => s.id === local.activeId)
    ? local.activeId
    : sessions[sessions.length - 1].id;
  return { sessions, activeId };
}
