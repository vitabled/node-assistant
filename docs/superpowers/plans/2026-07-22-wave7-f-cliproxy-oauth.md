# Волна 7, План F — CLIProxyAPI со входом через OAuth (self-host)

> Пункт пользователя 10: *«cliproxyapi должен получать доступ к нейронкам через auth… Не хочу в
> cliproxyapi использовать api, хочу auth»*. К запросу приложен архив рабочего проекта `ai-router`,
> где это уже сделано.

## Что означает «auth вместо api»

CLIProxyAPI умеет два способа получить доступ к провайдеру:

- **API-ключ** — обычный `sk-…` от Anthropic/OpenAI, оплата по токенам.
- **OAuth-аккаунт** — вход тем же аккаунтом, которым человек пользуется в Claude/Codex/Grok/Kimi;
  прокси хранит refresh-токен, сам его обновляет и раздаёт запросы по пулу аккаунтов с round-robin.

Просьба — про второй. Сегодня у нас нет **ни того, ни другого**: контейнера CLIProxyAPI не существует,
а `AiConfig.gateway_internal` — мёртвое поле (найдено разведкой Волны 6, План C).

## Разведка — наша сторона (проверено по коду)

- `services/ai_agent.py:199-215` — `_INTERNAL_GATEWAY_HOSTS = {"node-installer-cliproxy", "cli-proxy"}`
  и SSRF-исключение для внутреннего шлюза уже написаны: `_check_base_url` пропускает эти хосты, когда
  `gateway == "cliproxy"` и `gateway_internal`. То есть посадочная площадка готова, самолёта нет.
- `services/ai_agent.py:218-224` — `list_models` работает для любого провайдера (Волна 6, План C Ф2
  сняла гейт `gateway != cliproxy → []`).
- Образцы оркестрации: `services/xray_checker.py` (DooD-контейнер, `_docker()`, `container_state`,
  деградация при отсутствии docker-бинаря) и `services/mcp_server.py` (Fernet-волт для
  `auth_token_enc`, `ensure_auth_token`, отдача токена владельцу для копирования).

## Разведка — CLIProxyAPI (источник: приложенный `ai-router`)

Архив несёт `docs/cliproxyapi-integration.md` — дистиллят чтения исходников CLIProxyAPI
(HEAD `5afc0f1d`, 2026-07-04) с ссылками `file:line`, плюс рабочие `docker-compose.yml`,
`cliproxy/config.template.yaml`, `render_config.py` и async-клиент Management API. Всё ниже — оттуда,
и это **проверенные** факты, а не память о проекте.

**Образ и запуск.** `eceasy/cli-proxy-api:v7.2.50` — **Docker Hub, не ghcr**; версию пинить.
Единственный нужный порт — **8317** (chat + management + health). Callback-порты (54545/1455/…)
нужны только для `is_webui`-флоу, который нам не нужен.

**Раскладка томов — важная ловушка.** Стоково монтируют файл `./config.yaml` и каталог
`./auths → /root/.cli-proxy-api`. Bind-mount **файла** до его создания заставляет Docker создать
директорию-паразит. `ai-router` обходит это, монтируя каталог целиком (`./cliproxy → /conf`),
запуская `./CLIProxyAPI --config /conf/config.yaml` и переопределяя в конфиге `auth-dir: /conf/auths`.

**Healthcheck.** В образе **нет curl и wget** (debian:bookworm + tzdata/ca-certificates) — стоковый
`CMD curl …/healthz` упадёт. Рабочий вариант из `ai-router`: `bash -c "exec 3<>/dev/tcp/127.0.0.1/8317"`.
Сам эндпоинт `GET /healthz` не требует авторизации.

**Аутентификация — три отдельные вещи, которые легко перепутать:**

1. `api-keys:` в конфиге — клиентский мастер-ключ, которым **наш бэкенд** ходит на `/v1/*`.
   ⚠️ **Пустой список = полностью открытый прокси**: provider доступа не регистрируется и запросы
   проходят без проверки. Ключ обязан быть задан до первого старта.
2. `MANAGEMENT_PASSWORD` (env) — ключ Management API. Плюс он **принудительно включает
   `allow-remote`**, без которого запрос из соседнего контейнера получает 403 ещё до проверки ключа.
   Альтернатива (`remote-management.secret-key` в конфиге) хуже: значение хешируется и **переписывается
   в файл** при старте.
3. OAuth-токены провайдеров — файлы в `auth-dir`, обновляются прокси автоматически.

**⚠️ 5 неудачных авторизаций Management API с одного IP → бан IP на 30 минут.** За общей сетью все
наши запросы приходят с одного адреса, поэтому опечатка в ключе плюс ретрай-цикл заблокируют доступ
целиком. Клиент обязан делать **ровно один** запрос и на 401 бросать типизированную ошибку без ретрая
(`common/ai_router_common/cliproxy/management.py:78-91`).

**Ещё ловушки Management API:** пустой ключ И пустой `MANAGEMENT_PASSWORD` → все `/v0/management/*`
отдают **404** (API выключен, а не «не найдено»); `GET /config` возвращает весь конфиг с plaintext-
ключами — в браузер не отдавать никогда.

**OAuth headless-флоу (то, ради чего всё):**

1. `GET /v0/management/{anthropic|codex|antigravity|kimi|xai}-auth-url` → `{status:"ok", url, state}`.
   Спавнит фоновую горутину, ждущую колбэк **до 5 минут**.
2. Человек открывает `url`, входит в аккаунт провайдера, его редиректит на несуществующий
   loopback-адрес — и он копирует **полный URL из адресной строки**.
3. `POST /v0/management/oauth-callback` c `{provider, state, code}` **или** `{redirect_url: "<полный URL>"}`.
4. `GET /v0/management/get-auth-status?state=…` → `wait | ok | error`, поллить до терминального.

⚠️ **Не передавать `is_webui=1`** — иначе поднимется loopback-forwarder на портах, которых у нас нет.
⚠️ **У Gemini OAuth-логина нет** — только API-ключ / Vertex / Antigravity (вход Google-аккаунтом даёт
gemini-модели). Пользователю это надо показать, а не молча не давать кнопку.
Нормализация провайдеров при колбэке: `claude/anthropic→anthropic`, `openai/codex→codex`,
`grok/xai/x-ai/x.ai→xai`. Kimi — device-flow (url = verification URI, код ловить не надо).

**Аккаунты:** `GET /auth-files` → список со здоровьем (`status`, `disabled`, `unavailable`, `email`,
`success/failed`, `last_refresh`, …); `DELETE /auth-files?name=`; `PATCH /auth-files/status`
(включить/выключить); `POST /reset-quota {"auth_index"}`. Несколько аккаунтов одного провайдера =
несколько файлов → round-robin автоматически.

**Модели:** `GET /v1/models` — динамический список **реально загруженных** моделей (исчезает вместе с
последним живым кредом). Недоступная модель даёт **502** («unknown provider») или **503** («no auth
available»), а не чистый 404 — обрабатывать как «временно недоступна».

## Развилки

**Р1. Где хранить `config.yaml` и OAuth-токены.** Бэкенд управляет контейнером через docker.sock, то
есть все пути в `docker run` — **хостовые**, а не внутренние для нашего контейнера. Значит bind-mount
каталога из нашего `DATA_DIR` работать не будет (мы не знаем его хостовый путь).
- **(а, по умолчанию)** именованный том `node-cliproxy-conf`, монтируемый в `/conf`. Docker резолвит
  его на стороне демона — трансляция путей не нужна. Конфиг засеваем одноразовым
  `docker run --rm -v node-cliproxy-conf:/conf …` с записью файла из stdin **до** старта основного
  контейнера, поэтому окна с пустым `api-keys` (= открытый прокси) не существует ни секунды.
- (б) выставить `api-keys` после старта через `PUT /v0/management/api-keys` — между стартом и вызовом
  прокси открыт. Отвергаю.

**Р2. Кому принадлежит контейнер.** Ровно та же проблема, что у MCP (см. План E, Р2): один контейнер
на инсталляцию. Здесь она **мягче** — CLIProxyAPI не несёт данных аккаунта, только OAuth-аккаунты
провайдеров LLM. Но пул этих аккаунтов общий, то есть аккаунт Б расходует лимиты аккаунта А.
- **(а, по умолчанию)** явно назвать это общей инфраструктурой: `owner_account_id`, кто включил —
  тот и управляет аккаунтами провайдеров; остальные аккаунты могут только пользоваться шлюзом, и в
  UI это написано. Так же, как общий xray-checker.
- (б) контейнер на аккаунт — отдельная волна.

**Р3. Мастер-ключ и Management-пароль.** Генерируются нами (`secrets.token_urlsafe`), хранятся
**Fernet-зашифрованными** в `AiConfig` (`cliproxy_master_key_enc`, `cliproxy_mgmt_key_enc`) — точная
копия подхода `McpConfig.auth_token_enc`. Management-пароль **не показывается никогда и никому**;
мастер-ключ — только владельцу и только для копирования (прецедент MCP-вкладки).

**Р4. Порт наружу.** По умолчанию **не публикуем** (`--network node-assistant-net`, без `-p`), как
`ai-router`. Панель управления CLIProxyAPI (`GET /management.html`) при этом недоступна из браузера —
и это правильно: она требует management-ключ, который мы в браузер не отдаём. Свой UI строим сами (Ф4).

## Фазы

### Ф1 — контейнер и конфиг → verify: `cd backend && python -m pytest` + ручной старт

- `services/cliproxy_server.py` (NEW), по образцу `xray_checker.py`:
  - `_render_config(master_key)` — **чистая** функция, отдаёт YAML: `port: 8317`, `host: ""`,
    `remote-management: {allow-remote: true, secret-key: ""}`, `auth-dir: "/conf/auths"`,
    `api-keys: [<master>]`, `logging-to-file: false`, `request-retry: 3`.
    Ключ подставлять **YAML-безопасно** (кавычки/экранирование), а не конкатенацией.
  - `seed_config()` — одноразовый `docker run --rm -v node-cliproxy-conf:/conf` с записью конфига из
    **stdin** (не из argv — ключ не должен попасть в `/proc/cmdline` и в лог docker).
  - `start(account_id)` / `stop()` / `container_state()` / `reachable()` / `status()` — с `_NO_DOCKER`-
    деградацией: отсутствие docker-бинаря → `{ok, warning}`, а не 500 (правило `xray_checker`).
  - `ensure_keys()` — сгенерировать мастер-ключ и management-пароль при первом включении.
- `AiConfig`: `cliproxy_enabled`, `cliproxy_image` (дефолт `eceasy/cli-proxy-api:v7.2.50`),
  `cliproxy_master_key_enc`, `cliproxy_mgmt_key_enc`, `cliproxy_owner_account_id`.
- Тесты `backend/tests/test_cliproxy_server.py`: рендер конфига содержит непустой `api-keys`
  (регрессия на «открытый прокси» — assert, что список **не** пуст); ключ не попадает в argv
  сгенерированной команды; отсутствие docker не роняет ручку.

### Ф2 — клиент Management API → verify: pytest

- `services/cliproxy_management.py` (NEW) — порт `management.py` из `ai-router` на наш стиль:
  `ManagementError` / `ManagementDisabled` (403/404) / `ManagementAuthError` (401).
  **Ровно один запрос на метод, никаких ретраев** — комментарий про 30-минутный IP-бан обязателен
  в коде, иначе кто-нибудь добавит `retry` при следующем рефакторинге.
- Методы: `list_auth_files`, `delete_auth_file`, `patch_auth_status`, `start_oauth(provider)`,
  `post_oauth_callback(payload)`, `get_auth_status(state)`, `reset_quota`.
- `GET /config` **не** оборачиваем вовсе — чтобы не появилось соблазна отдать его наружу.
- Тесты: 401 не ретраится (замерить число вызовов транспорта); 404 и 403 дают `ManagementDisabled`;
  неизвестный провайдер OAuth отвергается до сети.

### Ф3 — наши роуты → verify: pytest

- `api/cliproxy.py` (NEW, под `require_account`):
  `GET/POST /api/cliproxy/config` · `GET /status` · `POST /start|/stop` ·
  `GET /accounts` (auth-files, отфильтрованные от секретов) · `DELETE /accounts/{name}` ·
  `PATCH /accounts/{name}` (enable/disable) ·
  `POST /oauth/start {provider}` · `POST /oauth/callback {state, redirect_url|code}` ·
  `GET /oauth/status?state=`.
- Ответы **скрабить**: ни один management-ответ не уходит в браузер как есть (в `auth-files` бывают
  поля с путями и почтой; в `oauth-callback` — служебные данные).
- Мутации доступны только `owner_account_id`; остальным — 403 с внятным текстом.
- Тесты `backend/tests/test_cliproxy_api.py`: не-владелец получает 403 на мутации и 200 на статус;
  `provider: "gemini"` отвергается с объяснением «у Gemini нет OAuth»; секреты не в ответе.

### Ф4 — вкладка «Ассистент → Шлюз» → verify: `tsc --noEmit` + `npm test`

- `settings/CliProxyTab.tsx` (NEW) или блок в `AiSettingsTab.tsx`: статус контейнера, тумблер,
  версия образа, список аккаунтов провайдеров (провайдер · почта · статус · последнее обновление ·
  вкл/выкл/удалить).
- **Мастер OAuth** — двухшаговый, ровно как в `ai-router` (`admin-frontend/src/pages/Providers.tsx:129-139`):
  «1. Открыть ссылку» (валидировать, что это `https://`, прежде чем рендерить `<a>`) →
  «2. Вставьте полный redirect-URL после авторизации» → поллинг статуса до `ok`/`error`.
  Для Kimi — device-flow: показать только ссылку и поллить.
  Для Gemini — вместо кнопки плашка «OAuth недоступен; используйте Antigravity или API-ключ».
- Переключение ассистента на шлюз: `gateway = "cliproxy"`, `gateway_internal = true`,
  `base_url = http://node-installer-cliproxy:8317/v1`, ключ = мастер-ключ. Каталог моделей приезжает
  существующим `GET /api/ai/models` (`list_models` уже провайдер-агностичен).
- Тесты: кнопка провайдера без OAuth задизейблена с объяснением; не-`https` ссылка не рендерится
  как кликабельная; поллинг останавливается на терминальном статусе.

### Ф5 — compose и документация → verify: `docker compose config` + `docker compose build`

- Сервис `cli-proxy` в `docker-compose.yml` под `profiles: ["cliproxy"]` (по умолчанию не стартует —
  бэкенд оркестрирует сам, как с MCP), `container_name: node-installer-cliproxy` (имя уже зашито в
  `_INTERNAL_GATEWAY_HOSTS`), сеть `node-assistant-net`, том `node-cliproxy-conf:/conf`,
  healthcheck через `/dev/tcp`, **без** `ports:`.
- CLAUDE.md: раздел про шлюз — образ с Docker Hub, ловушка bind-mount файла, отсутствие curl,
  «пустой `api-keys` = открытый прокси», запрет ретраев на 401.

## Риски

- **Открытый прокси.** Единственный по-настоящему опасный сценарий: контейнер поднялся с пустым
  `api-keys`. Закрывается порядком «сначала засеять конфиг, потом старт» (Ф1) и assert-ом в тестах.
- **Бан IP на 30 минут** — цена одной опечатки плюс цикла ретраев. Запрет ретраев на 401 обязан быть
  и в коде, и в тесте.
- **Токены провайдеров в томе.** `node-cliproxy-conf` содержит живые refresh-токены. Том не бэкапится
  экспортом аккаунта (`services/export_service.py`) и не должен туда попасть.
- **Версия прокси.** Проект меняется быстро; справка снята с HEAD `5afc0f1d`. Образ пиннить, при
  бампе — перечитать `docs/cliproxyapi-integration.md` из архива и сверить ручки заново.
- **Приложенный архив — не источник кода, а источник знания.** В нём лежат живые OAuth-креды и `.env`
  владельца. Ничего оттуда **не копировать в репозиторий**; переносим только контракт и уроки.

## Критерий готовности

1. Контейнер поднимается из UI, `GET /healthz` отвечает, наружу порт не опубликован.
2. Мастер OAuth заводит аккаунт Claude: ссылка → вставка redirect-URL → статус `ok` → аккаунт виден
   в списке с почтой и здоровьем.
3. Ассистент отвечает через шлюз, каталог моделей непустой, API-ключ провайдера нигде не вводился.
4. `api-keys` непуст с первой секунды жизни контейнера (проверено тестом и `docker exec`).
5. Тест на «401 не ретраится» зелёный.
