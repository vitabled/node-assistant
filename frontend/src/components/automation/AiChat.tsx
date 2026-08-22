import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Loader2, Send, Square, Bot, PanelLeft, Wrench, AlertCircle, Paperclip, X, FileText, FileArchive, Image as ImageIcon, Globe, Trash2, MessageSquarePlus } from "lucide-react";
import { usePermissions } from "../../auth/usePermissions";
import {
  listSessions, getActive, setActive, newSession, clearActive,
  replaceMessages, removeSession, pushReplace, pushDelete,
  type Msg, type SessionsState,
} from "./aiSessions";
import * as runner from "./aiRunner";
import { RichText } from "./chatMarkdown";

/** Только то, что нужно чату: гейт композера. Форма настроек живёт в
 *  «Настройки → Ассистент» (`settings/AiSettingsTab.tsx`) — эта страница НИЧЕГО
 *  не сохраняет, поэтому и полного конфига ей не требуется. */
interface AiChatConfig {
  enabled: boolean;
  has_key: boolean;
  /** Есть ЧЕМ авторизоваться: через шлюз CLIProxyAPI ключ провайдера не нужен —
   *  доступ даёт OAuth-аккаунт внутри шлюза. Старый бэкенд поля не отдаёт,
   *  поэтому читаем с откатом на `has_key`. */
  auth_ready?: boolean;
}

/** Ответ `GET /api/ai/tools`. Показываем ДО вопроса: иначе границы агента
 *  выясняются только из отказа («не могу писать», «веб выключен»). */
interface ToolsInfo {
  builtin: number;
  writes: boolean;
  web: boolean;
  web_provider: string;
  mcp?: number;
}

/** Вложение живёт ровно один вопрос: файлы не персистятся, они относятся к
 *  сообщению, а не к аккаунту (см. api/ai.py::Attachment). */
interface AttachmentImage { index: number; mime: string; data_b64: string }
interface Attachment {
  name: string; mime: string; text: string; data_b64: string;
  /** Картинки, вынесенные из текста. В промпт не попадают — ассистент сохраняет
   *  их в медиатеку инструментом save_attachment_image по номеру маркера. */
  images?: AttachmentImage[];
}

const MAX_FILES = 5;
/** Сервер режет историю сам (ai_agent.MAX_HISTORY_MESSAGES), но гонять по сети
 *  весь разговор незачем — отправляем тот же хвост. */
const MAX_HISTORY_MESSAGES = 20;
// ⚠️ Потолок на ВЕСЬ файл, а не на то, что уедет в промпт: сервер кладёт в
// сообщение только начало, а остальное ассистент дочитывает инструментом
// read_attachment. Прежние 40 000 резали каталог хостингов на 22 МБ до 0,18%, и
// ассистент честно сообщал, что данных в файле нет — обрезали его мы.
const MAX_TEXT_CHARS = 2_000_000;

// Встроенные картинки выносим в ОТДЕЛЬНОЕ поле, а в тексте оставляем маркер с
// номером. Их нельзя ни оставить, ни выбросить: в каталоге хостингов на них
// приходилось 97% объёма (21 МБ из 22) — с ними не помещаются сами данные; а
// выбросить значит потерять то, что и просят перенести (у карточки хостинга
// есть вложения). Маркер стоит РОВНО на месте картинки, поэтому по нему видно,
// какому хостингу она принадлежит.
const DATA_URI_RE = /data:(image\/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=]+)/gi;
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const IMAGE_MIME = ["image/png", "image/jpeg", "image/gif", "image/webp"];

// Архивы едут на сервер СЫРЫМИ (base64), и разворачивает их бэкенд
// (`ai_archives.unpack`). Раньше их пытались прочитать как текст: `f.text()` на
// gzip даёт мусор или пустоту, файл отбрасывался с «пустой или нечитаемый», и
// агент уходил искать данные в вебе — до архива не доезжало НИЧЕГО.
const ARCHIVE_MIME = [
  "application/gzip", "application/x-gzip", "application/x-tar",
  "application/x-tgz", "application/tar+gzip", "application/x-gtar",
  "application/zip", "application/x-zip-compressed", "application/x-compressed",
  "application/x-bzip2", "application/x-xz",
];
const ARCHIVE_EXT = [".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
                     ".zip", ".tar"];
// Потолок больше картиночного: архив на то и архив, что в нём данные, а не
// одна иллюстрация. Дальше упирается лимит тела запроса на бэкенде.
const MAX_ARCHIVE_BYTES = 50 * 1024 * 1024;

const isArchive = (f: File) =>
  ARCHIVE_MIME.includes((f.type || "").toLowerCase()) ||
  ARCHIVE_EXT.some(e => f.name.toLowerCase().endsWith(e));

/** Байты → base64. Порциями: `String.fromCharCode(...buf)` на мегабайтах
 *  переполняет стек аргументов и падает прямо в момент прикрепления файла. */
function toBase64(buf: Uint8Array): string {
  let bin = "";
  const STEP = 0x8000;
  for (let i = 0; i < buf.length; i += STEP)
    bin += String.fromCharCode(...buf.subarray(i, i + STEP));
  return btoa(bin);
}

/** Читаем файл ЗДЕСЬ, а не грузим в хранилище медиа: вложение чата эфемерно,
 *  складывать его в общую библиотеку значило бы засорять её.
 *
 *  Экспортируется ради теста: путь «архив не отбрасывается» проверяется прямо
 *  на функции — собрать в jsdom настоящий gzip-`File` и провести его через DOM
 *  дороже, чем он того стоит. */
export async function readFile(f: File): Promise<Attachment | string> {
  const mime = f.type || "";
  if (IMAGE_MIME.includes(mime)) {
    if (f.size > MAX_IMAGE_BYTES) return `${f.name}: картинка больше 4 МБ`;
    return { name: f.name, mime, text: "", data_b64: toBase64(new Uint8Array(await f.arrayBuffer())) };
  }
  if (isArchive(f)) {
    if (f.size > MAX_ARCHIVE_BYTES) return `${f.name}: архив больше 50 МБ`;
    return {
      name: f.name, mime: mime || "application/octet-stream", text: "",
      data_b64: toBase64(new Uint8Array(await f.arrayBuffer())),
    };
  }
  // Всё остальное считаем текстом: логи, конфиги, куски кода. Бинарь тоже
  // прочитается, но пользы от него модели не будет — предупреждаем размером.
  const text = await f.text();
  if (!text.trim()) return `${f.name}: пустой или нечитаемый файл`;
  const images: AttachmentImage[] = [];
  const lean = text.replace(DATA_URI_RE, (_m, imgMime: string, b64: string) => {
    const index = images.length;
    images.push({ index, mime: imgMime.toLowerCase(), data_b64: b64 });
    return `«изображение #${index}»`;
  });
  return { name: f.name, mime: mime || "text/plain",
           text: lean.slice(0, MAX_TEXT_CHARS), data_b64: "", images };
}

/** Команда срабатывает, только если это ВСЁ содержимое поля. Иначе нельзя было
 *  бы спросить «что отдаёт /api/ai/config» — вопрос про путь молча превратился
 *  бы в команду. Поэтому неизвестный «/…» уходит в чат обычным текстом. */
const COMMANDS = ["/newsession", "/clear", "/compact"] as const;
type Command = (typeof COMMANDS)[number];
const asCommand = (s: string): Command | null =>
  (COMMANDS as readonly string[]).includes(s) ? (s as Command) : null;

/** Событие состояния из стрима (`ai_agent.run_agent`). */
interface AgentStatus {
  phase: "thinking" | "tools" | "done";
  step: number;
  steps: number;
  tokens: number;
  /** Инструмент, который выполняется прямо сейчас. Ставит клиент по tool_call. */
  tool?: string;
}

/** «6.4k» вместо «6432»: точное число здесь не нужно, а короткое не прыгает
 *  шириной на каждом обновлении. */
export const fmtTokens = (n: number) =>
  n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(n);

export const fmtElapsed = (sec: number) =>
  sec < 60 ? `${sec}с` : `${Math.floor(sec / 60)}м ${String(sec % 60).padStart(2, "0")}с`;

const PHASE_LABEL: Record<AgentStatus["phase"], string> = {
  thinking: "Думает",
  tools: "Работает с панелью",
  done: "Готово",
};

/** Пометка сжатого контекста: пузырь выглядит как ответ ассистента, и без неё
 *  выжимка читалась бы как реплика в разговоре. */
const COMPACT_PREFIX = "📝 Сжатый контекст:";

export function AiChat() {
  const { user } = usePermissions();
  const uid = user?.id ?? null;
  const [cfg, setCfg] = useState<AiChatConfig | null>(null);
  const [caps, setCaps] = useState<ToolsInfo | null>(null);
  // Состояние ответа и сам лог живут в `aiRunner` — модуле вне компонента:
  // иначе уход в другой раздел панели размонтирует чат и обрывает запрос.
  useSyncExternalStore(runner.subscribe, runner.getVersion);
  const store = runner.ensureSessions(uid);
  const run = runner.getRunState();

  const [cmdErr, setCmdErr] = useState("");
  /** Сжатие идёт одним запросом и уходом со страницы не портится — держим его
   *  локально, в отличие от длинного ответа агента. */
  const [compacting, setCompacting] = useState(false);
  // Композер запираем и на сжатие: оно тоже ходит к модели.
  const busy = run.busy || compacting;
  // Список разговоров. На узком экране закрыт по умолчанию: панель в 224px
  // съела бы больше половины ширины телефона (там она ложится ПОВЕРХ чата —
  // правило `.ni-ai-sessions` в index.css).
  const [panelOpen, setPanelOpen] = useState(
    () => typeof window === "undefined" || window.innerWidth > 820);
  const [input, setInput] = useState("");
  const [attach, setAttach] = useState<Attachment[]>([]);
  const [attachErr, setAttachErr] = useState("");
  const [over, setOver] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const status = run.status;
  const [elapsed, setElapsed] = useState(0);
  const [loadErr, setLoadErr] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/ai/config")
      .then(r => { if (!r.ok) throw new Error("bad"); return r.json(); })
      .then(setCfg)
      .catch(() => setLoadErr(true));
    // Строка возможностей необязательна: не ответила ручка — просто не рисуем
    // её. Заглушка с нулями врала бы про агента сильнее, чем её отсутствие.
    fetch("/api/ai/tools")
      .then(r => { if (!r.ok) throw new Error("bad"); return r.json(); })
      .then(setCaps)
      .catch(() => {});
    // ⚠️ Уход со страницы НЕ отменяет запрос: ответ по большому файлу идёт
    // минутами, и переключиться на другой раздел на это время нормально.
    // Отмена осталась только явной — кнопкой «Остановить».
    //
    // А это — подхват ответа, который продолжал идти на сервере, пока страницу
    // перезагружали. Без него F5 выглядел бы как потеря работы, хотя работа
    // никуда не девалась.
    void runner.resume(uid);
    // Переписка с СЕРВЕРА. До ответа сети на экране уже лежит локальный кэш,
    // поэтому подмена происходит без «пустого чата»: сервер только уточняет
    // (или восстанавливает — если localStorage вычистил браузер).
    void runner.syncSessions(uid);
  }, []);

  // Часы тикают ТОЛЬКО во время ответа: интервал, живущий всегда, перерисовывал
  // бы страницу раз в секунду просто так.
  useEffect(() => {
    if (!busy || !run.startedAt) return;
    // ⚠️ Отсчёт от `startedAt` ИСПОЛНИТЕЛЯ, а не от монтирования: вернувшись на
    // страницу, человек должен видеть настоящее время работы, а не ноль.
    const tick = () => setElapsed(Math.floor((Date.now() - run.startedAt) / 1000));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [busy, run.startedAt]);

  const msgs = getActive(store).messages;
  useEffect(() => { scrollRef.current?.scrollTo?.(0, scrollRef.current.scrollHeight); }, [msgs]);

  // Чтение под ключом личности и запись в localStorage делает `aiRunner`:
  // писать обязан тот, кто владеет состоянием, иначе ответ, пришедший при
  // закрытой странице, оседать было бы некуда.
  const commit = (next: SessionsState) => runner.updateSessions(() => next);

  /** Изменение, которое обязано доехать до СЕРВЕРА. Очистка, сжатие и удаление
   *  разговора — не косметика: не отправь мы их, следующая синхронизация
   *  вернула бы стёртое с сервера, и кнопка «Очистить» перестала бы работать
   *  после перезагрузки. */
  const commitSynced = (next: SessionsState, sessionId: string, messages: Msg[]) => {
    commit(next);
    void pushReplace(sessionId, messages);
  };

  const dropSession = (id: string) => {
    commit(removeSession(store, id));
    void pushDelete(id);
  };

  const addFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    const room = MAX_FILES - attach.length;
    if (room <= 0) { setAttachErr(`Не больше ${MAX_FILES} файлов`); return; }
    const out: Attachment[] = [];
    const errs: string[] = [];
    for (const f of list.slice(0, room)) {
      const r = await readFile(f);
      typeof r === "string" ? errs.push(r) : out.push(r);
    }
    if (out.length) setAttach(a => [...a, ...out]);
    setAttachErr(errs.join("; "));
  };

  // История собирается ДО добавления текущей пары: без неё «а теперь то же для
  // второй ноды» не к чему привязать. Пустые пузыри (прерванный ответ)
  // пропускаем — они ничего не сообщают модели. После `/compact` тут уже лежит
  // одна выжимка, поэтому сборка ничего про сжатие знать не должна.
  const buildHistory = (list: Msg[]) =>
    list.filter(m => m.text.trim())
      .slice(-MAX_HISTORY_MESSAGES)
      .map(m => ({ role: m.role, content: m.text }));

  /** `/compact` — заменить переписку выжимкой. Сжимать пустой разговор незачем. */
  const runCompact = async () => {
    const history = buildHistory(msgs);
    if (!history.length) { setCmdErr("Сжимать нечего — переписка пуста."); return; }
    setCompacting(true);
    try {
      const res = await fetch("/api/ai/compact", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ history }),
      });
      const data = await res.json().catch(() => ({} as any));
      // Причину отказа показываем дословно: «ИИ-агент выключен» и «модель не
      // ответила» требуют разных действий от человека.
      if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Не удалось сжать переписку.");
      // Пустая выжимка — тоже отказ: заменить ею разговор значило бы стереть
      // его молча.
      if (typeof data?.summary !== "string" || !data.summary.trim())
        throw new Error("Модель вернула пустую выжимку — переписка не тронута.");
      const summary: Msg[] = [
        { role: "assistant", text: `${COMPACT_PREFIX} ${data.summary}`, tools: [] },
      ];
      commitSynced(replaceMessages(store, summary), getActive(store).id, summary);
    } catch (e: any) {
      setCmdErr(String(e?.message || "Не удалось сжать переписку."));
    } finally { setCompacting(false); }
  };

  const send = async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    setCmdErr("");

    // Команды разбираем ДО сети: они ничего не спрашивают у модели.
    const cmd = asCommand(prompt);
    if (cmd) {
      setInput("");
      if (cmd === "/newsession") commit(newSession(store));
      else if (cmd === "/clear") commitSynced(clearActive(store), getActive(store).id, []);
      else await runCompact();
      return;
    }

    const files = attach;
    setInput("");
    setAttach([]); setAttachErr("");
    // Сам запрос ведёт `aiRunner`: он переживает уход со страницы, пишет ответ
    // в хранилище и рассылает прогресс подписчикам.
    void runner.send({ prompt, attachments: files, history: buildHistory(msgs) });
  };

  if (loadErr) return <p className="text-sm text-[var(--err)]">Не удалось загрузить конфигурацию ИИ.</p>;
  if (!cfg) return null;

  return (
    // Полноэкранная колонка: лог занимает остаток и прокручивается сам, композер
    // приколот снизу. `min-h-0` обязателен — без него flex-ребёнок не сжимается
    // и лог выдавливает композер за экран (страница целиком не скроллится:
    // `body{overflow:hidden}` в index.css).
    //
    // `flex-1`, а НЕ `h-full`: родитель (`<Screen>` в App.tsx) — flex-колонка с
    // `flex:1; min-height:0`, поэтому процентная высота зависела бы от того,
    // разрешима ли высота выше по дереву, а flex-растяжение — нет.
    // Строка: слева список разговоров, справа сам чат. `relative` — чтобы на
    // узком экране панель легла поверх чата (правило в index.css), а не
    // отъедала половину ширины.
    <div className="flex flex-1 min-h-0 relative">
      {panelOpen && (
        // Панель-«карточка» как в Claude Desktop: отступ от краёв, рамка
        // целиком, скруглённые углы; overflow:hidden обрезает контент по
        // закруглению (раньше — панель впол роста с border-r, рамка рвалась).
        <aside className="ni-ai-sessions shrink-0 w-56 flex flex-col min-h-0 m-2 rounded-xl border"
          style={{ borderColor: "var(--line)", background: "var(--bg2)", overflow: "hidden" }}
          data-testid="ai-sessions">
          <div className="p-2 shrink-0">
            <button disabled={busy} onClick={() => commit(newSession(store))}
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs
                         text-[var(--t-hi)] hover:bg-[var(--bg3)] disabled:opacity-40">
              <MessageSquarePlus size={14} /> Новый разговор
            </button>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto px-2 pb-2 flex flex-col gap-0.5">
            {listSessions(store).map(s2 => {
              const active = s2.id === getActive(store).id;
              return (
                // Строка целиком кликабельна, крестик — поверх неё; иначе на
                // узкой панели в 224px попасть в маленькую цель тяжело.
                <div key={s2.id} className="group relative">
                  <button onClick={() => { commit(setActive(store, s2.id)); void runner.resume(uid, s2.id); }}
                    title={s2.title || "Новый разговор"} data-testid="ai-session-row"
                    className="w-full text-left pl-2 pr-7 py-1.5 rounded-lg text-xs truncate
                               disabled:opacity-40"
                    style={active
                      ? { background: "var(--accent-dim)", color: "var(--t-hi)" }
                      : { color: "var(--t-mid)" }}>
                    {s2.title || "Новый разговор"}
                  </button>
                  <button title="Удалить разговор" disabled={busy}
                    onClick={() => dropSession(s2.id)}
                    className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded
                               text-[var(--t-faint)] opacity-0 group-hover:opacity-100
                               hover:text-[var(--err)] disabled:opacity-0">
                    <X size={12} />
                  </button>
                </div>
              );
            })}
          </div>
        </aside>
      )}

      <div className="flex flex-col flex-1 min-w-0 min-h-0 ni-pagebody">
      <div className="shrink-0 flex items-center gap-2 h-11 px-4 ni-pagehead">
        <button title={panelOpen ? "Скрыть разговоры" : "Показать разговоры"}
          aria-label="Список разговоров" aria-expanded={panelOpen}
          onClick={() => setPanelOpen(o => !o)}
          className="p-1.5 rounded-lg text-[var(--t-low)] hover:text-[var(--accent-hi)]">
          <PanelLeft size={15} />
        </button>
        <Bot size={16} className="text-[var(--accent-hi)]" />
        {/* `min-w-0` + `truncate`: заголовок разговора задаёт пользователь, и
            длинный текст иначе распирает шапку (было именно так с выпадающим
            списком — он не сжимался и вылезал за рамку). */}
        <span className="text-sm font-semibold text-[var(--t-hi)] truncate min-w-0"
          title={getActive(store).title || "Новый разговор"}>
          {getActive(store).title || "Новый разговор"}
        </span>
        <span className="text-[11px] text-[var(--t-low)] ml-auto shrink-0"
          data-testid="ai-msg-count">
          Сообщений: {msgs.length}
        </span>
      </div>

      {!cfg.enabled && (
        <div className="shrink-0 mx-4 mb-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertCircle size={14} /> Агент выключен — включите его в «Настройки → Ассистент».
        </div>
      )}
      {cfg.enabled && !(cfg.auth_ready ?? cfg.has_key) && (
        <div className="shrink-0 mx-4 mb-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertCircle size={14} /> Нечем авторизоваться — добавьте API-ключ или войдите через
          CLIProxyAPI в «Настройки → AI».
        </div>
      )}

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-3 mx-4 rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] p-4" data-testid="ai-chat-log">
        {msgs.length === 0 && (
          <p className="text-[12px] text-[var(--t-faint)]">
            Спросите про любой раздел панели — ноды, правила, подписки, хостинги, заметки.
            {/* Про веб пишем, только если он не выключен: обещать умение, которое
                тут же будет отклонено, — ровно та проблема, что решает строка
                возможностей ниже. */}
            {caps?.web !== false && " Могу поискать в интернете и открыть страницу."}
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={m.role === "user" ? "self-end max-w-[85%]" : "self-start max-w-[90%]"}>
            {m.role === "user" ? (
              <div className="flex flex-col items-end gap-1">
                <div className="px-3 py-2 rounded-lg bg-[var(--accent-dim)] text-[var(--t-hi)] text-sm">{m.text}</div>
                {!!m.files?.length && (
                  <span className="flex items-center gap-1 text-[11px] text-[var(--t-low)]">
                    <Paperclip size={10} /> {m.files.join(", ")}
                  </span>
                )}
              </div>
            ) : (
              <div className="flex flex-col gap-1.5">
                {m.tools.map((t, j) => (
                  <div key={j} className="flex items-center gap-1.5 text-[11px] text-[var(--t-low)]">
                    <Wrench size={11} /> {t.name}
                    {t.ok === true && <span className="text-[var(--ok)]">✓</span>}
                    {t.ok === false && <span className="text-[var(--err)]">✗</span>}
                    {t.ok === undefined && <Loader2 size={10} className="animate-spin" />}
                  </div>
                ))}
                {/* Ответ модели — markdown/HTML, а не plain text: списки,
                    таблицы и код-блоки иначе приезжали сплошной простынёй.
                    Рендер идёт через санитайзер с белым списком тегов и БЕЗ
                    dangerouslySetInnerHTML (chatMarkdown.tsx). */}
                {m.text && (
                  <RichText text={m.text}
                    className="px-3 py-2 rounded-lg bg-[var(--bg1)] border border-[var(--line-soft)] text-[var(--t-hi)] text-sm" />
                )}
              </div>
            )}
          </div>
        ))}

        {/* Строка состояния — В КОНЦЕ ЛОГА, а не в шапке: лог автопрокручивается
            вниз, поэтому она всегда на виду, и её видно рядом с тем, что агент
            уже успел сделать. */}
        {busy && (
          <div className="self-start flex items-center gap-2 text-[11px] text-[var(--t-low)]"
            data-testid="ai-status" aria-live="polite">
            <Loader2 size={12} className="animate-spin text-[var(--accent-hi)]" />
            <span className="text-[var(--t-mid)]">
              {status ? PHASE_LABEL[status.phase] : "Отправка"}
            </span>
            <span>·</span>
            <span>{fmtElapsed(elapsed)}</span>
            {status && (
              <>
                <span>·</span>
                <span>шаг {status.step} из {status.steps}</span>
              </>
            )}
            {status && status.tokens > 0 && (
              <>
                <span>·</span>
                <span>{fmtTokens(status.tokens)} токенов</span>
              </>
            )}
            {status?.tool && (
              <>
                <span>·</span>
                <span className="font-mono truncate max-w-[160px]">{status.tool}</span>
              </>
            )}
          </div>
        )}
      </div>

      <div className="shrink-0 flex flex-col gap-2 p-4"
        onDragOver={e => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={e => { e.preventDefault(); setOver(false); void addFiles(e.dataTransfer.files); }}
        style={over ? { outline: "1px dashed var(--accent)", outlineOffset: -6, borderRadius: 12 } : undefined}>

        {caps && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--t-low)]" data-testid="ai-caps">
            <span className="flex items-center gap-1">
              <Wrench size={11} /> Инструментов: {caps.builtin + (caps.mcp ?? 0)}
            </span>
            <span className="flex items-center gap-1">
              <Globe size={11} /> {caps.web ? `Веб: ${caps.web_provider}` : "Веб выключен"}
            </span>
            <span>{caps.writes ? "Режим: чтение и запись" : "Режим: только чтение"}</span>
          </div>
        )}

        {/* Команды — строкой под композером, а не модалкой: подсказка нужна
            ровно в момент набора. */}
        <div className="text-[11px] text-[var(--t-faint)]" data-testid="ai-commands">
          Команды: <code>/newsession</code> — новая сессия, <code>/clear</code> — очистить переписку,{" "}
          <code>/compact</code> — сжать её в выжимку.
        </div>
        {cmdErr && <div className="text-[11px] text-[var(--err)]">{cmdErr}</div>}

        {(attach.length > 0 || attachErr) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {attach.map((a, i) => (
              <span key={i} title={a.name}
                className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg max-w-[220px]"
                style={{ background: "var(--bg3)", border: "1px solid var(--line-soft)", color: "var(--t-mid)" }}>
                {/* ⚠️ Иконку выбираем по mime, а не по наличию `data_b64`:
                    после появления архивов base64 есть и у них, и картиночная
                    иконка врала бы про содержимое. */}
                {IMAGE_MIME.includes(a.mime) ? <ImageIcon size={11} />
                  : a.data_b64 ? <FileArchive size={11} /> : <FileText size={11} />}
                <span className="truncate">{a.name}</span>
                <button onClick={() => setAttach(list => list.filter((_, j) => j !== i))}
                  className="text-[var(--t-low)] hover:text-[var(--err)]"><X size={11} /></button>
              </span>
            ))}
            {attachErr && <span className="text-[11px] text-[var(--err)]">{attachErr}</span>}
          </div>
        )}

        <div className="flex items-center gap-2">
          <input ref={fileRef} type="file" multiple hidden
            onChange={e => { void addFiles(e.target.files || []); e.target.value = ""; }} />
          <button title="Прикрепить файл" disabled={busy || !cfg.enabled}
            onClick={() => fileRef.current?.click()}
            className="p-2.5 rounded-lg text-[var(--t-low)] hover:text-[var(--accent-hi)] disabled:opacity-40">
            <Paperclip size={16} />
          </button>
          {/* Обрыв разговора: чистит и лог, и историю — они одно и то же.
              Сессия остаётся, как и у команды `/clear`. */}
          <button title="Очистить переписку" disabled={busy || msgs.length === 0}
            onClick={() => commitSynced(clearActive(store), getActive(store).id, [])}
            className="p-2.5 rounded-lg text-[var(--t-low)] hover:text-[var(--err)] disabled:opacity-40">
            <Trash2 size={16} />
          </button>
          <input className="input flex-1" value={input} disabled={busy || !cfg.enabled}
            placeholder={over ? "Отпустите файлы здесь" : "Сообщение агенту..."}
            onChange={e => setInput(e.target.value)}
            onPaste={e => {
              // Скриншот из буфера — самый частый способ приложить картинку.
              const files = Array.from(e.clipboardData.files || []);
              if (files.length) { e.preventDefault(); void addFiles(files); }
            }}
            onKeyDown={e => { if (e.key === "Enter") send(); }} />
        {run.busy ? (
          // Отмена теперь ТОЛЬКО отсюда: уход со страницы запрос не обрывает,
          // значит должен быть явный способ его прекратить.
          <button onClick={() => runner.stop()} title="Остановить ответ"
            className="p-2.5 rounded-lg bg-[var(--bg3)] hover:bg-[var(--err-dim)] text-[var(--err)]">
            <Square size={16} />
          </button>
        ) : (
          <button onClick={send} disabled={busy || !cfg.enabled || !input.trim()}
            className="p-2.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-40">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
          </button>
        )}
        </div>
      </div>
    </div>
      </div>
  );
}
