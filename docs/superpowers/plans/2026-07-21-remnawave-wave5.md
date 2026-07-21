# Волна 5 (node-assistant): Справка/Карта · неон+motion · Библиотека · пользовательские конфиги (xray/mihomo) · редакторы хедеров/виджетов · API-токены · ИИ-инструкции · OpenCLIProxy · мультипанель · импорт/экспорт · микросервисы

> Зонтичный индекс. Волна 5 = 13 тематических планов (A–M) из нового набора идей. Всё per-account,
> secrets-not-at-rest (кроме уже существующих module-scoped Fernet-волтов). Продолжение Волн 1–4. Каждый план
> сверен с реальным кодом + внешней разведкой (mihomo-configurator, Cloudflare agent-setup, CLIProxyAPI,
> Remnawave config/sub-templates, doc-парсеры). Планы ЕЩЁ НЕ реализованы — это набор продвинутых промптов.

## Карта под-планов

| Файл | Тема | Источник |
|---|---|---|
| `2026-07-21-wave5-a-spravka-nav.md` | Навигация: группа «Хостинги»→**«Справка»**, пункт «Карта»→**«Карта хостингов»**, +слот «Библиотека» (frontend-only) | идея 1 |
| `2026-07-21-wave5-b-neon-motion.md` | Оформление: **неон**-скин/акцент, больше цветов, **motion-анимации везде** (переходы, stagger, glow, анимированные числа, скелетоны), prefers-reduced-motion + тумблеры | идеи 2, 7 |
| `2026-07-21-wave5-c-library.md` | **«Библиотека»** (в группе «Справка»): заметки/знания + файловое хранилище (pdf/doc/docx/txt/md/xlsx/xls/odt), извлечение текста + FTS5-поиск + вендорные превью | идея 9 |
| `2026-07-21-wave5-d-custom-configs.md` | **«Пользовательские конфиги»**: шаблоны по типам клиента (xray-json/mihomo/clash/…) по образцу Remnawave sub-templates + привязка нашего Xray-редактора | идея 3 |
| `2026-07-21-wave5-e-mihomo-editor.md` | **Mihomo-редактор** (реимплементация по референсу 123jjck/mihomo-configurator), привязан к mihomo-шаблонам (План D) | идея 4 |
| `2026-07-21-wave5-f-headers-editor.md` | **Редактор хедеров**: переиспользуемый key-value компонент для HTTP-заголовков в xray-транспортах/хостах/подписке | идея 6 |
| `2026-07-21-wave5-g-stats-widgets.md` | **Редактор виджетов статистики**: добавить/размер/удалить/переставить на сетке, персист per-account | идея 8 |
| `2026-07-21-wave5-h-api-tokens.md` | **API-токены доступа** (не брать JWT из браузерной сессии): выпуск/отзыв, резолв в `require_account` | идея 11 |
| `2026-07-21-wave5-i-ai-instructions.md` | **Инструкции для ИИ** (пресеты системных промптов) + вендоренный **Cloudflare agent-setup** промпт | идея 12 |
| `2026-07-21-wave5-j-opencliproxy.md` | **OpenCLIProxy** (=CLIProxyAPI): ИИ-агент через шлюз → все AI-провайдеры; «MCP через прокси» переформулировано | идея 14 |
| `2026-07-21-wave5-k-panel-selector.md` | **Селектор панелей Remnawave** в Настройках + смена главной (Settings и RW-сайдбар); ручной ввод сохраняется | идея 13 |
| `2026-07-21-wave5-l-panel-import-export.md` | **Импорт/экспорт данных**: срез 1 — наши per-account сторы; срез 2 — данные Remnawave-панели через API | идея 15 |
| `2026-07-21-wave5-m-microservices.md` | **Микросервисы** (strangler, опционально за compose-профилем): вынести deploy-worker + monitoring | идея 10 |

**Сквозное (идея 5):** каждый план расширяет backend API под свою функцию — новые роутеры под `require_account`
в `main.py` + тесты в `backend/tests/`.

## Граф зависимостей (порядок реализации)
- **A → C** — A создаёт nav-слот `Tab='library'` + группу «Справка»; C наполняет раздел (backend+frontend).
- **D → E, D → F(Ф5)** — D закладывает каркас «тип шаблона → редактор» и sub-templates в `remnawave_client`;
  E реализует mihomo-редактор в этот каркас, F(Ф5) переиспользует HeadersEditor в mihomo.
- **H → J, H → M** — API-токены нужны как машинный креденшл для шлюза (J) и сервис-в-сервис аутентификации (M).
- **K → L(срез 2)** — импорт/экспорт панельных данных берёт пары `(url, token)` из реестра панелей K.
- **B** — ортогонален, приносит пользу всем новым разделам (G/C/D/E/F получают motion «бесплатно»).
- Рекомендуемый порядок: **A, B** (быстрые, разблокируют вид) → **H** (токены, база для J/M) → **D → E, F** →
  **C, G** → **I, J** → **K → L** → **M** (последним, крупный/рискованный).

## Закреплённые решения (Alignment)
- **Переименования (A)** — только визуальные: id табов `hostings-map`/`hostings-list` НЕ меняются (не ломать
  localStorage-персист и per-account сторы). Группа «Справка» = «Карта хостингов» → «Хостинги» → «Библиотека».
- **Неон (B)** — третий **скин** (`data-skin`) или неон-акцент поверх текущей `skin×mode`-темы (apple/console ×
  light/dark), НЕ ломая существующие; всё через CSS-var токены; `motion/react` уже в проекте; глобальный тумблер
  анимаций + уважение `prefers-reduced-motion`.
- **Библиотека (C)** — файлы приватны per-account под `accounts/<id>/library/`, метаданные+FTS5 в SQLite; парсеры
  документов (pypdf/python-docx/openpyxl/xlrd/odfpy/markdown, `.doc` — опц. antiword) + вендорные вьюеры
  (pdf.js/mammoth) — всё CSP-self-contained, sandbox-рендер. Новые pip-deps + правка `backend/Dockerfile`.
- **Конфиги (D)** — источник истины = **локальный** `config_templates.json` per-account (по типам клиента),
  Remnawave-панель = опциональный экспорт/импорт (не единственный источник). Xray-редактор (`profiles/*`)
  открывается на шаблоне stateless. Каркас «тип → редактор» расширяется планом E (mihomo).
- **Mihomo (E)** — ⚠️ **апстрим 123jjck/mihomo-configurator без лицензии (all rights reserved)** → прямой
  перенос кода недопустим; план — **реимплементация по референсу**. Требует решения: писать своё vs запросить
  лицензию. Новые npm-deps `js-yaml`+`@codemirror/lang-yaml` (ставятся в Docker-билде).
- **API-токены (H)** — единая точка резолва `require_account` (`auth.py:54`) принимает `Bearer <api-token>`
  наравне с JWT; токены **отзываемые** (stored hash, HMAC-SHA256), формат `nai_<account_id>_<secret>`, показ
  секрета один раз; MCP переводится на управляемый токен вместо сессионного JWT.
- **ИИ-инструкции (I)** — библиотека пресетов системных промптов per-account; Cloudflare-промпт **вендорится из
  URL с атрибуцией** (не reproduce вручную — при реализации фактически зафетчить, иначе пресет-плейсхолдер).
- **OpenCLIProxy = CLIProxyAPI** (`router-for-me/CLIProxyAPI`, R3) — это **LLM-шлюз** (говорит OpenAI+Anthropic
  форматами, которые агент уже умеет), а НЕ MCP-транспорт и не новый «провайдер». **Реинтерпретация ключевой
  формулировки:** «использовать MCP через api opencliproxy» → перенаправляем НАШ `ai_agent` через шлюз (новый
  режим `gateway="cliproxy"` в `AiConfig`); `mcp_server.py` не трогаем. Self-host прокси — опц. DooD-контейнер.
- **Мультипанель (K)** — `AppSettings.remnawave` остаётся **вычисляемым представлением активной панели** через
  `@model_validator` → 13 сайтов-читателей `.remnawave` кодово не трогаются; правим только запись/CRUD/миграцию
  (реестр панелей + указатель активной). Ручной ввод = «кастомная» запись реестра. `panel_jobs` не хранит токен
  → «из развёрнутых» даёт только URL-кандидат.
- **Импорт/экспорт (L)** — 2 среза: (1) наши per-account данные (11 JSON `storage.py` + 3 обходящих его +
  5 SQLite) — **полный обход `accounts.data_dir(id)`**, не только `storage.*`; политика волтов —
  опц. шифрование архива паролем (PBKDF2→Fernet) для переноса между инстансами с разным `ENCRYPTION_KEY`;
  (2) данные Remnawave-панели через API (нужен новый `remnawave_client.create_user` для импорта пользователей).
- **Микросервисы (M)** — **опционально за compose-профилем** (`--profile split`), strangler, с фолбэком
  (выключенный сервис не роняет продукт). Минимум готовности = вынести `monitoring` + `deploy-worker`. Требует
  Ф1-подготовки: `infra_billing_store` на explicit `account_id` (единственный ContextVar-only стор) +
  разделяемый `SharedTaskStore` (SQLite) вместо in-memory. auth/accounts НЕ дробим.

## Бэклог — открытые вопросы (свести перед реализацией)
- **E (mihomo):** лицензия апстрима — своя реализация или запрос лицензии у автора? (блокер переноса кода)
- **I (cloudflare):** WebFetch домена иногда блокируется — при реализации фактически зафетчить промпт (текст не
  выдумывать); держать плейсхолдер `unavailable` до успешного фетча.
- **C (библиотека):** собран ли прод-Python с FTS5 (иначе degrade на LIKE); достаточно ли `unicode61`+префикс для
  русского или нужен стемминг; богатое превью legacy `.doc` (LibreOffice-ветка) — отложено; OCR — вне области.
- **B (неон):** точные хекс-значения неон-палитры и имена акцентов — подтвердить визуально; light-вариант неона
  («day-glow») отложен (dark-committed в первой итерации).
- **K (панели):** plaintext-токен vs Fernet-волт для записей панелей (дефолт — статус-кво plaintext); отдельный
  таб `rw-panels` vs селектор внутри `RemnavaveTab`; топбар-дропдаун vs сайдбар-переключатель.
- **L (экспорт):** нет `remnawave_client.create_user` (нужен для импорта пользователей среза 2); экспорт
  client-side данных (`deploy_jobs`/`panel_jobs`/`xray_profile` в localStorage) — отдельным клиентским экспортом?
- **H (токены):** формат встраивает `account_id` (O(1)-резолв без индекса) — приемлемо ли раскрытие uuid (не
  чувствительнее JWT-`sub`); HMAC-SHA256 vs адаптивный KDF; гранулярные скоупы — только `readonly` в v1.
- **G (виджеты):** отдельный `stat_widgets.json` vs поле в settings; конфликт сервер↔localStorage «last-write-wins»
  для мультиустройства; своя CSS-grid + HTML5 DnD vs `react-grid-layout`.
- **J (CLIProxyAPI):** точный тег официального Docker-образа для pin в compose (проверить при реализации).
- **M (микросервисы):** осознанный оверхед для single-operator — распил опционален; кросс-хостовый распил (разные
  `ENCRYPTION_KEY`) — вне волны; брокер сообщений не вводим (SQLite-стор задач достаточно).

## Разведка — источники (закрыты)
- **mihomo-configurator** (R1): `github.com/123jjck/mihomo-configurator` — браузерный YAML-генератор для mihomo
  (Clash.Meta), импорт share-ссылок vless/vmess/ss/trojan/hysteria/tuic/…; **без лицензии**.
- **Cloudflare** (R2): `developers.cloudflare.com/agent-setup/prompt.md` — официальный bootstrap-промпт для
  ИИ-агентов-кодеров; вендорить как ассет.
- **CLIProxyAPI** (R3): `github.com/router-for-me/CLIProxyAPI` (Go) — LLM-шлюз, OpenAI+Anthropic-совместимые
  эндпоинты, `GET /v1/models`; MCP в ядре не поддерживает.
- **Remnawave** (R4): `api-1.json` (config-profiles = сырой Xray-конфиг) + `remnawave/templates` (sub-templates
  по типам клиента, mihomo как base64(YAML)); наш `remnawave_client` сейчас умеет только config-profiles.
- **Doc-парсеры** (R5): pypdf/pdfminer.six, python-docx, openpyxl, xlrd, odfpy, markdown; SQLite FTS5; вьюеры
  pdf.js/mammoth.
- Плюс кодовая разведка: nav/routing, тема/motion, stats-виджеты + Xray-редактор, auth/API/инвентарь сторов,
  управление панелями.
