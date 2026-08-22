// Исполнитель запроса к ассистенту — ВНЕ компонента.
//
// Три потери прогресса лечатся здесь и на сервере:
//
// 1. Уход в другой раздел панели размонтировал чат и обрывал поток. Поэтому
//    запрос ведёт модуль-синглтон: компонент при возврате просто ПОДКЛЮЧАЕТСЯ
//    к идущему ответу.
// 2. Перезагрузка страницы убивает и модуль вместе с соединением. Поэтому сам
//    цикл агента крутится ФОНОВОЙ задачей на сервере (`services/ai_runs.py`), а
//    здешний поток — только читатель её буфера. После F5 `resume()` спрашивает
//    сервер, не осталось ли незаконченного ответа, и дочитывает его.
// 3. Браузер чистил localStorage (Safari ITP, приватное окно, «очистить данные»)
//    — и переписка пропадала целиком, хотя ни одна из машин не падала. Поэтому
//    каждая реплика уезжает на сервер (`services/ai_chat_store.py`), а
//    localStorage остаётся кэшем. См. `aiSessions.ts`.
//
// ⚠️ Хранилище сессий тоже живёт здесь, а не в компоненте: иначе задача решалась
// бы наполовину — работа продолжается, а писать ответ некуда, `setState`
// размонтированного компонента ничего не делает.
//
// ⚠️ Автоматической отмены нет вовсе. Только явная кнопка «Остановить», и она
// просит остановиться СЕРВЕР — обрыв нашего чтения работу не прекращает.

import {
  appendMessages, getActive, load, pushAppend, pushReplace, replaceMessages,
  save, syncFromServer, type Msg, type SessionsState,
} from "./aiSessions";

export interface AgentStatus {
  phase: "thinking" | "tools" | "done";
  step: number;
  steps: number;
  tokens: number;
  /** Инструмент, который выполняется прямо сейчас. */
  tool?: string;
}

export interface RunState {
  busy: boolean;
  status: AgentStatus | null;
  /** Когда начался текущий ответ (мс). Часы считает подписчик — так их тик не
   *  заставляет перерисовываться тех, кто на другой странице. */
  startedAt: number;
  /** В каком разговоре идёт ответ: чужой прогресс показывать нельзя. */
  sessionId: string;
}

const IDLE: RunState = { busy: false, status: null, startedAt: 0, sessionId: "" };

let state: RunState = IDLE;
let ac: AbortController | null = null;
const listeners = new Set<() => void>();

let sessions: SessionsState | null = null;
let sessionsUid: string | null = null;
/** Для кого уже сходили на сервер. Синхронизация одноразовая на личность:
 *  повторный заход в раздел не должен затирать начатый разговор. */
let syncedUid: string | null | undefined = undefined;

/** Версия — снимок для `useSyncExternalStore`. Число сравнивается по значению,
 *  поэтому не нужно собирать стабильный объект на каждый рендер. */
let version = 0;

function bump() {
  version += 1;
  for (const l of listeners) l();
}

function emit(next: RunState) {
  state = next;
  bump();
}

export function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function getVersion(): number {
  return version;
}

export function getRunState(): RunState {
  return state;
}

/** Поднять сессии пользователя. Перечитываем ТОЛЬКО при настоящей смене
 *  личности: иначе ответ `/api/auth/me` затирал бы уже начатый разговор. */
export function ensureSessions(uid: string | null): SessionsState {
  if (sessions === null || sessionsUid !== uid) {
    sessions = load(uid);
    sessionsUid = uid;
    bump();
  }
  return sessions;
}

export function getSessions(): SessionsState {
  return sessions ?? load(null);
}

/** Подтянуть переписку с СЕРВЕРА и, если надо, перенести туда локальную.
 *
 *  ⚠️ Отдельно от `ensureSessions`, а не внутри неё: та обязана быть
 *  синхронной — её зовут прямо из рендера, чтобы показать разговор мгновенно, до
 *  всякой сети. Сеть догоняет здесь, с монтирования компонента.
 *
 *  Один раз на личность: повторный вызов (перемонтирование при переходах между
 *  разделами) затирал бы уже начатый разговор ответом сервера. */
export async function syncSessions(uid: string | null): Promise<void> {
  ensureSessions(uid);
  if (syncedUid === uid) return;
  syncedUid = uid;
  // Пока идёт ответ, синхронизацию не трогаем: она заменила бы состоянием с
  // сервера ту самую реплику, которая прямо сейчас дописывается.
  if (state.busy) return;
  const next = await syncFromServer(getSessions());
  if (next !== sessions) {
    sessions = next;
    save(sessionsUid, next);
    bump();
  }
}

/** Изменить сессии и сохранить. Единственная точка записи — и из компонента,
 *  и из идущего ответа. */
export function updateSessions(fn: (s: SessionsState) => SessionsState): void {
  const next = fn(getSessions());
  sessions = next;
  save(sessionsUid, next);
  bump();
}

/** Сбросить состояние модуля: он синглтон, и его память переживает
 *  размонтирование компонента — в этом весь смысл. Нужна там, где хранилище
 *  сменилось под ним: смена пользователя, очистка localStorage в тестах. */
export function reset(): void {
  ac?.abort();
  ac = null;
  sessions = null;
  sessionsUid = null;
  syncedUid = undefined;
  state = IDLE;
  bump();
}

/** Явная остановка. Просит остановиться СЕРВЕР: работа идёт там, и обрыв
 *  нашего чтения её не прекратил бы. */
export function stop(): void {
  const sid = state.sessionId;
  ac?.abort();
  ac = null;
  if (sid) {
    void fetch(`/api/ai/chat/stop?session_id=${encodeURIComponent(sid)}`,
               { method: "POST" }).catch(() => {});
  }
  emit({ ...IDLE });
}

export interface SendArgs {
  prompt: string;
  attachments: unknown[];
  history: { role: string; content: string }[];
}

export async function send(args: SendArgs): Promise<void> {
  const sessionId = getActive(getSessions()).id;
  // Отправка в ЭТУ сессию невозможна, пока в ней идёт ответ. Ответ в другой
  // сессии — не блокер: задачи на сервере независимы по session_id, и у каждой
  // сессии может идти своя работа (см. переключение чатов в AiChat).
  if (state.busy && state.sessionId === sessionId) return;

  const asked: Msg = {
    role: "user", text: args.prompt,
    files: args.attachments.length
      ? args.attachments.map(a => (a as { name: string }).name) : undefined,
  };
  updateSessions(s => appendMessages(s, [
    asked,
    { role: "assistant", text: "", tools: [] },
  ]));
  emit({ busy: true, status: null, startedAt: Date.now(), sessionId });

  // Вопрос сохраняем СРАЗУ, не дожидаясь ответа: именно долгий ответ и есть тот
  // момент, когда вкладку закрывают, — и вопрос пропал бы вместе с ней.
  void pushAppend(sessionId, [asked]);

  await consume("/api/ai/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    // session_id: по нему и вложение доживает до следующего сообщения, и
    // сервер узнаёт свой идущий ответ при переподключении.
    body: JSON.stringify({ prompt: args.prompt, attachments: args.attachments,
                           history: args.history, session_id: sessionId }),
  }, sessionId);
}

/** Подключиться к ответу, который идёт на сервере.
 *
 *  ⚠️ Ради этого всё и делалось: перезагрузка убивает наш поток, но не работу.
 *  События приходят С НАЧАЛА — клиент не знает, сколько успел применить до F5,
 *  поэтому последняя реплика собирается заново, а не дописывается. */
export async function resume(uid: string | null, targetSessionId?: string): Promise<void> {
  // Переключение на ДРУГУЮ сессию разрешено даже во время ответа в текущей:
  // задачи на сервере независимы по session_id, и здешний поток обязан уметь
  // переезжать. Повторный resume той же сессии во время её же ответа — no-op.
  ensureSessions(uid);
  const sessionId = targetSessionId || getActive(getSessions()).id;
  if (state.busy && state.sessionId === sessionId) return;
  try {
    const res = await fetch(
      `/api/ai/chat/state?session_id=${encodeURIComponent(sessionId)}`);
    const st = res.ok ? await res.json() : null;
    if (!st?.active) return;
  } catch {
    return;
  }

  // Место под восстанавливаемую реплику: недописанную заменяем целиком.
  updateSessions(s => {
    const cur = getActive(s).messages;
    const last = cur[cur.length - 1];
    const head = last && last.role === "assistant" ? cur.slice(0, -1) : cur;
    return replaceMessages(s, [...head, { role: "assistant", text: "", tools: [] }]);
  });
  emit({ busy: true, status: null, startedAt: Date.now(), sessionId });

  await consume(
    `/api/ai/chat/resume?session_id=${encodeURIComponent(sessionId)}`, {},
    // ⚠️ Переподключение собирает реплику ЗАНОВО (события приходят с начала),
    // поэтому дописывать её нельзя — на сервере получилась бы вторая копия.
    // Перезаписываем разговор целиком: это ещё и самолечение, если вкладки
    // разошлись.
    sessionId, "replace");
}

/** Читать ndjson-поток событий и раскладывать его по хранилищу и прогрессу.
 *  ОДИН код на отправку и на переподключение: разойдись они — восстановленный
 *  ответ отличался бы от живого.
 *
 *  `sessionId` нужен, чтобы сохранить готовый ответ на сервер: в `state` его
 *  брать нельзя — к моменту записи он уже сброшен в IDLE. */
async function consume(url: string, init: RequestInit,
                       sessionId: string,
                       persist: "append" | "replace" = "append"): Promise<void> {
  // Чистое обновление: последняя реплика заменяется НОВЫМ объектом (без мутации
  // на месте — безопасно под двойным вызовом React StrictMode).
  const patchLast = (fn: (m: Extract<Msg, { role: "assistant" }>) => void) =>
    updateSessions(s => {
      const cur = getActive(s).messages;
      const last = cur[cur.length - 1];
      if (!last || last.role !== "assistant") return s;
      const next = { ...last, tools: last.tools.map(t => ({ ...t })) };
      fn(next);
      return replaceMessages(s, [...cur.slice(0, -1), next]);
    });

  const controller = new AbortController();
  ac = controller;
  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    if (!res.ok || !res.body) throw new Error("stream failed");
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const ln of lines) {
        if (!ln.trim()) continue;
        let ev: any;
        try { ev = JSON.parse(ln); } catch { continue; }
        if (ev.type === "text") patchLast(a => { a.text += ev.delta; });
        else if (ev.type === "tool_call") {
          patchLast(a => { a.tools.push({ id: ev.id, name: ev.name }); });
          emit({ ...state, status: state.status
            ? { ...state.status, tool: ev.name } : state.status });
        } else if (ev.type === "tool_result") {
          patchLast(a => {
            const t = a.tools.find(x => (ev.id ? x.id === ev.id
              : x.name === ev.name && x.ok === undefined));
            if (t) t.ok = ev.ok;
          });
        } else if (ev.type === "status") {
          emit({ ...state, status: {
            phase: ev.phase, step: ev.step, steps: ev.steps, tokens: ev.tokens,
            // Инструмент сбрасываем на новом «думает»: он уже отработал.
            tool: ev.phase === "thinking" ? undefined : state.status?.tool,
          } });
        } else if (ev.type === "error") {
          patchLast(a => { a.text += `\n⚠️ ${ev.message}`; });
        }
      }
    }
  } catch {
    if (!controller.signal.aborted) {
      patchLast(a => { a.text += "\n⚠️ Ошибка соединения с ИИ."; });
    }
  } finally {
    if (ac === controller) ac = null;
    // Готовый ответ — на сервер. Именно здесь, в `finally`, а не по событию
    // `done`: ответ бывает и оборванным (ошибка сети, «Остановить»), и его
    // огрызок всё равно надо сохранить — он уже показан человеку, и после
    // перезагрузки переписка обязана выглядеть так же.
    //
    // ⚠️ Пустую реплику не пишем: она ничего не сообщает, а в истории
    // следующего хода занимала бы место под лимитом.
    const cur = getActive(getSessions()).messages;
    const last = cur[cur.length - 1];
    if (persist === "replace") {
      void pushReplace(sessionId, cur);
    } else if (last && last.role === "assistant" && last.text.trim()) {
      void pushAppend(sessionId, [last]);
    }
    emit({ ...IDLE });
  }
}
