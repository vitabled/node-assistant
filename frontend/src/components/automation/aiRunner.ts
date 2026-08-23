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
import { getActiveToken, getActiveInstanceId } from "../../auth/store";

export interface AgentStatus {
  phase: "upload" | "thinking" | "tools" | "done";
  step: number;
  steps: number;
  tokens: number;
  /** Инструмент, который выполняется прямо сейчас. */
  tool?: string;
  /** Прогресс отправки файла (только в фазе "upload"). */
  upload?: { name: string; loaded: number; total: number };
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

/** Вложение так, как его собрал `AiChat.readFile`. Для отправки важны только
 *  эти поля: имя, тип и есть ли у него сырое тело (архив/картинка). */
interface FileAttachment {
  name: string;
  mime: string;
  text?: string;
  data_b64?: string;
  file?: File;
  images?: unknown[];
}

/** Сколько байт из base64. Нужно только для процентов, поэтому округления
 *  padding'а хватает с запасом. */
const b64Bytes = (b64: string) => Math.floor((b64.length * 3) / 4);

/** Подменяемая в тестах фабрика XHR: в jsdom настоящего `XMLHttpRequest` с
 *  рабочим `upload.onprogress` нет, а проверять надо именно прогресс. */
let makeXhr: () => XMLHttpRequest = () => new XMLHttpRequest();

/** Только для тестов: подсунуть фейковый XHR. */
export function setXhrFactoryForTests(f: (() => XMLHttpRequest) | null): void {
  makeXhr = f ?? (() => new XMLHttpRequest());
}

/** Отправить ОДИН файл на `/api/ai/chat/upload` и вернуть его `upload_id`.
 *
 *  ⚠️ Именно XHR, а не `fetch`: у fetch НЕТ прогресса отправки (у Request нет
 *  события upload вовсе), а 50 МБ по медленному VPN уходят минутами — без
 *  процентов человек видит только «Отправка…» и считает, что всё зависло.
 *
 *  ⚠️ Заголовок `Authorization` ставим РУКАМИ: токен добавляет перехватчик
 *  `window.fetch` (auth/apiClient.ts), а XHR мимо него проходит — без этого
 *  каждая загрузка получала бы 401. */
export function uploadFile(
  file: Blob, name: string, sessionId: string,
  onProgress: (loaded: number, total: number) => void,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = makeXhr();
    xhr.open("POST", "/api/ai/chat/upload");
    const token = getActiveToken();
    if (token) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.setRequestHeader("X-Instance-Id", getActiveInstanceId());
    }
    // ⚠️ Content-Type НЕ ставим: границу multipart браузер вычисляет сам, а
    // заданный вручную заголовок её затрёт и тело станет неразбираемым.
    xhr.upload.onprogress = (e: ProgressEvent) => {
      // `lengthComputable === false` бывает у прокси — тогда процентов нет, и
      // врать про них нельзя: показываем хотя бы отправленные байты.
      onProgress(e.loaded, e.lengthComputable ? e.total : 0);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const id = JSON.parse(xhr.responseText)?.upload_id;
          if (typeof id === "string" && id) return resolve(id);
        } catch { /* ниже — общая ошибка */ }
        return reject(new Error(`Сервер не вернул идентификатор файла «${name}».`));
      }
      let detail = "";
      try { detail = JSON.parse(xhr.responseText)?.detail || ""; } catch { /* нет тела */ }
      reject(new Error(detail
        ? `${name}: ${detail}`
        : `${name}: сервер ответил ${xhr.status} при загрузке.`));
    };
    xhr.onerror = () => reject(new Error(`${name}: обрыв связи при загрузке файла.`));
    xhr.onabort = () => reject(new Error(`${name}: загрузка отменена.`));
    const form = new FormData();
    form.append("file", file, name);
    form.append("session_id", sessionId);
    xhr.send(form);
  });
}

/** Из base64 обратно в Blob. Файл читается в `readFile` ещё до отправки (там же
 *  считаются лимиты), а на диск браузера мы его не сохраняем — поэтому
 *  восстанавливаем тело из того, что уже в памяти. */
function b64ToBlob(b64: string, mime: string): Blob {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return new Blob([buf], { type: mime || "application/octet-stream" });
}

/** «12.3 МБ» — читаемый размер для строки состояния. */
export const fmtBytes = (n: number) =>
  n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(1)} МБ`
                   : `${Math.max(1, Math.round(n / 1024))} КБ`;

/** Развести вложения на два потока: тяжёлые (есть сырое тело) уезжают
 *  отдельными запросами, лёгкие текстовые — как раньше, телом чата.
 *
 *  Текст остаётся в теле осознанно: он и так лёгкий, а агенту нужен ИМЕННО
 *  текст — гонять его через диск сервера значило бы добавить круг на ровном
 *  месте. Архив же в теле — это те самые 67 МБ, из-за которых всё и рвалось. */
async function uploadHeavy(attachments: unknown[], sessionId: string):
    Promise<{ inline: unknown[]; uploadIds: string[] }> {
  const inline: unknown[] = [];
  const uploadIds: string[] = [];
  const heavy = (attachments as FileAttachment[])
    .filter(a => !!(a?.data_b64 || a?.file));
  for (const a of attachments as FileAttachment[]) {
    if (!(a?.data_b64 || a?.file)) inline.push(a);
  }

  let done = 0;
  for (const a of heavy) {
    const blob = a.file ?? b64ToBlob(a.data_b64 || "", a.mime);
    const total = a.file ? a.file.size : b64Bytes(a.data_b64 || "");
    const label = heavy.length > 1 ? ` (${done + 1} из ${heavy.length})` : "";
    const show = (loaded: number, tot: number) => {
      const size = tot || total;
      const pct = size > 0 ? Math.min(100, Math.round((loaded / size) * 100)) : 0;
      emit({ ...state, status: {
        phase: "upload", step: 1, steps: 0, tokens: 0,
        tool: `Загрузка ${a.name}${label} — ${fmtBytes(loaded)} из ${fmtBytes(size)} · ${pct}%`,
        upload: { name: a.name, loaded, total: size },
      } });
    };
    show(0, total);
    uploadIds.push(await uploadFile(blob, a.name, sessionId, show));
    done += 1;
  }
  return { inline, uploadIds };
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

  // ФАЗА 1 — файлы. Отдельными запросами и с прогрессом: одним куском на 67 МБ
  // запрос рвался у клиентов за VPN, а браузер молчал об этом до самого конца.
  let inline: unknown[] = args.attachments;
  let uploadIds: string[] = [];
  if (args.attachments.length) {
    try {
      ({ inline, uploadIds } = await uploadHeavy(args.attachments, sessionId));
    } catch (e: any) {
      // Ошибку дописываем в ответную реплику — туда же, куда пишутся все
      // остальные отказы, и оставляем разговор в согласованном виде.
      updateSessions(s => {
        const cur = getActive(s).messages;
        const last = cur[cur.length - 1];
        if (!last || last.role !== "assistant") return s;
        return replaceMessages(s, [...cur.slice(0, -1),
          { ...last, text: `${last.text}\n⚠️ ${e?.message || "Не удалось загрузить файл."}` }]);
      });
      emit({ ...IDLE });
      return;
    }
  }

  // ФАЗА 2 — сам вопрос. Тело лёгкое: вместо мегабайт base64 едут ссылки.
  await consume("/api/ai/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    // session_id: по нему и вложение доживает до следующего сообщения, и
    // сервер узнаёт свой идущий ответ при переподключении.
    body: JSON.stringify({ prompt: args.prompt, attachments: inline,
                           upload_ids: uploadIds,
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

/** Сервер ответил, но отказал (413, 422, 503). Отдельно от обрыва связи:
 *  переподключаться тут бессмысленно — ответ придёт тот же. */
class HttpError extends Error {}

/** Сколько раз пробуем подхватить оборванный ответ, прежде чем сдаться. */
const AUTO_RECONNECT_ATTEMPTS = 3;
// Ставится readStream при срабатывании сторожевого таймера (fetch 60s /
// простой 3 мин). consume в catch не может отличить наш abort от кнопки
// «Остановить» по одному `signal.aborted`, поэтому запоминаем причину.
let lastStallFailure: string | null = null;

/** Пауза между попытками. Не константа, чтобы тесты не ждали живые секунды. */
let reconnectDelayMs = 1800;

/** Только для тестов: обрыв и три попытки иначе стоят суите ~6 секунд. */
export function setReconnectDelayForTests(ms: number): void {
  reconnectDelayMs = ms;
}

type PatchLast = (fn: (m: Extract<Msg, { role: "assistant" }>) => void) => void;

const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

/** Прочитать ndjson-поток событий и разложить его по хранилищу и прогрессу.
 *  Бросает: HttpError — сервер отказал, любое другое — связь оборвалась. */
async function readStream(url: string, init: RequestInit,
                          controller: AbortController,
                          patchLast: PatchLast): Promise<void> {
  // ⚠️ Сторожевые таймеры: без них «Отправка» может висеть вечно. Если
  // nginx/провайдер рвёт HTTP/2 на середине большого POST (архив 50 МБ),
  // браузер не получает ни ответа, ни ошибки — fetch висит. Abort по
  // таймауту превращает это в обычный обрыв → срабатывает автопереподключение.
  const FETCH_TIMEOUT = 60_000;   // до первого байта ответа
  const STALL_TIMEOUT = 180_000;  // без единого события (агент думает ≤3 мин)
  let stale = false;
  const fail = (why: string) => {
    stale = true;
    lastStallFailure = why;   // см. ниже: реконнект по таймауту, а не по abort
    // Не abort'им controller: attemptAutoReconnect проверяет `aborted` как
    // признак кнопки «Остановить». Висящий fetch закроет сам сервер/nginx;
    // главное — читатель уже выброшен, а реконнект пойдёт новым запросом.
    throw new Error(why);
  };
  const fetchTimer = setTimeout(() => fail("Сервер не ответил за 60 секунд."), FETCH_TIMEOUT);
  const res = await fetch(url, { ...init, signal: controller.signal });
  clearTimeout(fetchTimer);
  if (!res.ok) {
    // Не «Ошибка соединения» — сервер ответил, но отказал (413 файл велик,
    // 422 валидация, 503 провайдер). Читаем реальную причину из тела.
    let detail = "";
    try {
      const t = await res.text();
      try { detail = JSON.parse(t)?.detail || t; } catch { detail = t; }
    } catch { /* тело не читается — ниже фолбэк */ }
    throw new HttpError(detail ? `Сервер ответил ${res.status}: ${detail}`
                               : `Сервер ответил ${res.status}`);
  }
  if (!res.body) throw new Error("stream failed");
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let stallTimer: ReturnType<typeof setTimeout> | undefined;
  const resetStall = () => {
    if (stallTimer) clearTimeout(stallTimer);
    stallTimer = setTimeout(() => fail("Нет данных от агента 3 минуты."), STALL_TIMEOUT);
  };
  resetStall();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) { if (stallTimer) clearTimeout(stallTimer); break; }
    resetStall();
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
}

/** Подхватить оборванный ответ. Обрыв на клиенте (упал VPN, сеть моргнула,
 *  браузер порвал HTTP/2) НЕ останавливает работу: она идёт фоновой задачей на
 *  сервере, и её буфер можно дочитать с начала — ровно тем же `resume`, что и
 *  после F5.
 *
 *  Возвращает `true`, если показывать ошибку не нужно: ответ дочитан, ответ уже
 *  завершился на сервере, или пользователь нажал «Остановить».
 *
 *  ⚠️ Зовётся ВНУТРИ `consume`, до его `finally`: иначе тот сбросил бы `busy` в
 *  IDLE, и на время переподключения чат выглядел бы завершённым. */
async function attemptAutoReconnect(sessionId: string, controller: AbortController,
                                    patchLast: PatchLast): Promise<boolean> {
  const q = encodeURIComponent(sessionId);
  for (let attempt = 1; attempt <= AUTO_RECONNECT_ATTEMPTS; attempt++) {
    // Не сбрасываем busy: работа не прервана, прервалось только наше чтение.
    emit({ ...state, status: {
      phase: state.status?.phase ?? "thinking",
      step: state.status?.step ?? 1,
      steps: state.status?.steps ?? 0,
      tokens: state.status?.tokens ?? 0,
      tool: `переподключение… (${attempt} из ${AUTO_RECONNECT_ATTEMPTS})`,
    } });
    await sleep(reconnectDelayMs);
    if (controller.signal.aborted) return true;

    try {
      const res = await fetch(`/api/ai/chat/state?session_id=${q}`,
                              { signal: controller.signal });
      const st = res.ok ? await res.json() : null;
      // Ответ на сервере уже завершён — это не ошибка. То, что успело дойти,
      // и есть весь ответ; молча заканчиваем.
      if (!st?.active) return true;
    } catch {
      if (controller.signal.aborted) return true;
      continue; // сеть всё ещё лежит — следующая попытка
    }

    try {
      // Буфер отдаётся С НАЧАЛА, поэтому реплику собираем заново, а не
      // дописываем: иначе к огрызку приклеился бы весь ответ целиком.
      //
      // ⚠️ Чистим ЛЕНИВО, перед первой пришедшей правкой: попытка может опять
      // умереть на самом `fetch`, и очистка наперёд стёрла бы уже показанный
      // человеку огрызок — а он единственное, что осталось бы после трёх
      // неудач.
      let wiped = false;
      const patchFresh: PatchLast = fn => {
        if (!wiped) {
          wiped = true;
          updateSessions(s => {
            const cur = getActive(s).messages;
            const last = cur[cur.length - 1];
            if (!last || last.role !== "assistant") return s;
            return replaceMessages(s,
              [...cur.slice(0, -1), { role: "assistant", text: "", tools: [] }]);
          });
        }
        patchLast(fn);
      };
      await readStream(`/api/ai/chat/resume?session_id=${q}`, {}, controller, patchFresh);
      return true;
    } catch (e) {
      if (controller.signal.aborted) return true;
      if (e instanceof HttpError) return false; // сервер отказал — не сеть
    }
  }
  return false;
}

/** Провести запрос к ассистенту от начала до записи ответа.
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
  const patchLast: PatchLast = fn =>
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
    await readStream(url, init, controller, patchLast);
  } catch (e) {
    // Таймаут (нет ответа / агент молчит) — это НЕ пользовательская отмена:
    // пробуем подхватить ответ через resume, как при обычном обрыве сети.
    const stall = lastStallFailure;
    lastStallFailure = null;
    if (stall) {
      if (!await attemptAutoReconnect(sessionId, controller, patchLast)) {
        patchLast(a => { a.text += `\n⚠️ ${stall}`; });
      }
      return;
    }
    // Остановил пользователь — молчим: он и так знает, что прервал ответ.
    if (!controller.signal.aborted) {
      if (e instanceof HttpError) {
        patchLast(a => { a.text += `\n⚠️ ${e.message}`; });
      } else if (!await attemptAutoReconnect(sessionId, controller, patchLast)) {
        patchLast(a => {
          a.text += "\n⚠️ Ошибка соединения с ИИ: не удалось переподключиться" +
                    ` (${AUTO_RECONNECT_ATTEMPTS} попытки).`;
        });
      }
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
