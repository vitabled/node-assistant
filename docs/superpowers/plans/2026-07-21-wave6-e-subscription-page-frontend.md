# Волна 6 · План E — Страницы подписок: от «одного HTML» к редактированию фронтенда контейнера

> Раздел «Страницы подписок» сегодня — каталог одиночных `index.html`, который мы монтируем в контейнер
> `remnawave/subscription-page`. Разведка показала, что премисса задачи даже сильнее заявленной: этот контейнер
> **никогда не был «одним HTML»** — внутри собранная Vite/React SPA, которую NestJS отдаёт через **EJS-шаблон**,
> и наш монтаж произвольного `index.html` **тихо ломает единственный канал данных SPA** (`<%- panelData %>`).
> Плюс в 7.x `REMNAWAVE_API_TOKEN` стал обязательным, а мы пишем его пустым → контейнер падает на старте →
> деплой страницы подписок, вероятно, **не работает вообще**. План чинит оба дефекта и переводит раздел на
> модель «вариант = overlay изменённых файлов поверх базового дерева, стянутого с живого образа».
> Затрагивает: `services/subpage_store.py`, `api/subpages.py`, `services/panel_pipeline.py`,
> `models/panel_deploy.py`, `api/panel_deploy.py`, `services/remnawave_client.py`, `rw/SubPages.tsx`,
> `rw/PanelDeployForm.tsx`. **Поглощает неотгруженный План D Волны 3** (`2026-07-19-wave3-d-panel-subpages.md`).

## Контекст (как есть)

- **Стор — одиночный HTML на вариант.** `services/subpage_store.py:30-33` — `MAX_HTML_BYTES = 512*1024`,
  `MAX_PAGES = 100`; раскладка плоская: `accounts/<id>/subpages/<page_id>.html` + сиблинг `index.json`
  `[{id,name,size,created_at}]` (`:40-50,104-118`). Запись атомарная (`_write_atomic`, temp+`replace`),
  RMW индекса под процесс-локом `_INDEX_LOCK` (`:37`).
- **Traversal-гард — membership, а не путь.** `get_page_html` (`:81-90`) сначала проверяет наличие id в
  `index.json`, поэтому `_dir / f"{page_id}.html"` физически не может выйти за каталог (id = 12-hex от `uuid4`).
  **Этот гард не обобщается на дерево файлов** — там нужна пер-путь валидация (`resolve()` + проверка `parents`,
  как в `library_store.py:120-125`) и отдельный анти-zip/tar-slip на распаковке.
- **API-поверхность** `api/subpages.py:30-76`: `GET` список / `POST {name,html}` (413 сверх лимита `:40-43`,
  иначе 422) / `GET /{id}/raw` / `DELETE`. `/raw` отдаёт `Content-Security-Policy: sandbox` +
  `X-Content-Type-Options: nosniff` (`:54-57`). Роутер под `require_account` — `main.py:139`.
- **Единственная точка кастомизации в пайплайне** — `panel_pipeline._subpage_compose:302-325`: образ
  `remnawave/subscription-page:latest` (**плавающий тег**), `ports: 127.0.0.1:3010:3010`, и вольюм
  `- ./index.html:/opt/app/frontend/index.html`, эмитится ТОЛЬКО когда `req.subpage_html.strip()` непустой.
- **Транспорт контента** — `_write_html_script:376-386`: base64 внутрь heredoc + `base64 -d` на ноде.
  Приемлемо для 512 КиБ, безнадёжно для многомегабайтного дерева. SFTP уже есть и уже используется
  (`ssh_manager.py:151-164` `download_file`/`upload_file`, паттерн релея — `panel_sync.py:123-133`).
- **ЖИВОЙ ДЕФЕКТ (а) — обязательный токен.** `_subpage_env:281-299` пишет `REMNAWAVE_API_TOKEN=` **пустым**
  «по дизайну», предлагая оператору заполнить его позже. В 7.x схема env валидирует его zod-ом `.min(1)` →
  контейнер выходит на старте → проверка «контейнер запущен» в `_install_subpage:585-593` бросает
  `RuntimeError` → весь деплой панели помечается FAILED. **НЕ ПРОВЕРЕНО живьём** (код-путь однозначен, но
  запуска образа разведка не выполняла) — подтверждение вынесено первым шагом Ф1.
- **ЖИВОЙ ДЕФЕКТ (а2) — совет оператору ведёт в никуда.** `_install_subpage:566-570` пишет в лог «Задайте
  `REMNAWAVE_API_TOKEN` через раздел «Переменные»», но раздел «Переменные» жёстко прибит к **панельному**
  `.env`: `api/panel_deploy.py:308` `_ENV_PATH = "/opt/remnawave/.env"` (и `:359-363` запись туда же,
  фронт `rw/PanelVariables.tsx:198`). Файла `/opt/remnawave-subpage/.env` в UI нет вообще.
- **Секретный канал не используется для subpage.** Панельный `.env` пишется тихо
  (`_install_panel:475` → `ssh.get_script_output(_write_env_script(...))`, с idempotency-guard
  `__ENV_EXISTS__`), а subpage `.env` — через `_write_file_script` + `run_script` (`:546-551`) и **без**
  guard-а «файл уже есть». Сейчас утечки нет (секретов в нём нет), но как только появится реальный токен —
  надо переносить на тихий канал и добавлять merge/сохранение существующего значения.
- **Модель.** `models/panel_deploy.py:41,100-101,170-175`: `subpage_html: str` едет ВНУТРИ JSON-тела деплоя,
  капнут валидатором `_SUBPAGE_HTML_MAX = 512*1024`.
- **Ops-поверхность, которую нельзя сломать.** `api/panel_deploy.py:158-166` — `Component` включает `subpage`;
  `_panel_reinstall:263-281` заново вызывает `panel_pipeline._install_subpage` с полным ре-валидированным
  `PanelOpRequest` (то есть savedForm из `panel_jobs_<id>` в localStorage прогоняется через те же валидаторы);
  `_op_target:222-231` роутит subpage-операции на `sub_server`, когда он задан.
- **Frontend односоставный по конструкции.** `rw/SubPages.tsx:111-123` — загрузка через `await file.text()`
  (zip/шрифт непредставимы), `:236-237` — предпросмотр `<iframe sandbox="" srcDoc={previewHtml}>`.
  `rw/PanelDeployForm.tsx:345-374` — `pickCatalog()` тянет `/api/subpages/{id}/raw` и кладёт текст в
  `<textarea>`; `:569-613` — секция буквально называется «Страница подписок (Orion)», `SUBPAGE_MAX` (`:105`).
- **Пробел в клиенте панели.** `services/remnawave_client.py:164-201` оборачивает только
  `/api/subscription-templates`. Методов `/api/subscription-page-configs` **нет**, хотя эндпоинты есть в нашем
  же `api-1.json` (v2.8.0) и уже обёрнуты в нашем MCP-форке (`mcp/src/tools/subscription-page-configs.ts:7-48`
  — list/get/create/update/delete/reorder/clone).
- **План D Волны 3 не отгружен.** `docs/superpowers/plans/2026-07-19-wave3-d-panel-subpages.md:53-71,115-136`
  проектирует ровно эту миграцию (zip-бандл, zip-slip-гард, SFTP, монтирование каталога), но
  `git log -- backend/app/services/subpage_store.py backend/app/api/subpages.py frontend/src/components/rw/SubPages.tsx`
  показывает только Волну-1 `9ee8b3e` и косметический `05dd326`. В CLAUDE.md §9 План D отсутствует.
- **CLAUDE.md против кода.** §7d («Каталог Orion, Ф5») описывает стор **верно** — дрейфа доки-vs-код нет.
  Неверны **докстринги** `subpage_store.py:3-5` и `SubPages.tsx:4-7` («Orion отдаёт страницу подписок ОДНИМ
  build-less `index.html`, монтируемым в `remnawave/subscription-page`») — они смешивают legacy-Orion
  (standalone Marzban-подобный шаблон) с контейнеризованной SPA, которую мы реально запускаем. Это и есть
  корень проблемы. §7d придётся переписать по итогам этого плана.

## Развилки (закреплены)

- **Baseline стягиваем С ЖИВОГО КОНТЕЙНЕРА по SSH и кэшируем по digest образа** (решение пользователя).
  Не вкладываем дерево в репозиторий, не тянем с Docker Hub с бэкенда: у ноды уже есть образ, у нас уже есть
  SSH-сессия и SFTP. Кэш ключуется **resolved digest**, а не тегом — тег плавает.
- **Делаем ОБА уровня, в этом порядке: сначала каталог через штатные `subscription-page-configs` (API панели),
  потом file-overlay** (решение пользователя). Первый — официально поддерживаемая ось (брендинг/приложения/
  локали/featured), стоит почти ноль и закрывает ~90% реальных запросов; второй — для настоящей переверстки.
- **Вариант = overlay (только изменённые файлы) поверх baseline**, а не полный бандл. Vite пишет
  `assets/[name]-[hash].js` → полный бандл протухает на каждом апстрим-релизе; overlay протухает только в
  изменённых файлах, а неизменённые подтягиваются из baseline нужной версии.
- **Материализация дерева НА НОДЕ, а не загрузка апстрим-ассетов по сети.** На ноде: `docker create` +
  `docker cp <cid>:/opt/app/frontend/. /opt/remnawave-subpage/frontend/` → поверх копируем файлы overlay →
  монтируем **каталог** (`- ./frontend:/opt/app/frontend`). Это даёт настоящую семантику «храним только
  изменённое», не требует трафика на апстрим-ассеты и не превращает compose в список из сотни вольюмов.
- **Пиннинг образа обязателен.** `:latest` в `_subpage_compose:319` заменяется константой
  `_SUBPAGE_IMAGE = "remnawave/subscription-page:7.2.6"` + опциональное поле `subpage_image` (валидируется
  regex-ом image-ref). Overlay хранит `base_image` (тег) И `base_digest`; расхождение digest на целевой ноде —
  **громкое предупреждение в логе и в UI, но НЕ отказ** (оператор вправе рискнуть).
- **Чиним оба найденных бага этой волны, относящихся к разделу** (решение пользователя): (а) обязательный
  `REMNAWAVE_API_TOKEN` в форме деплоя + запись через тихий канал; (а2) раздел «Переменные» получает
  переключатель `scope: panel | subpage` (файл `/opt/remnawave-subpage/.env`), чтобы токен можно было
  починить/сменить без переустановки. **Мёртвый тоггл `cookie_gate` — НЕ в этом плане** (это нода, а не
  страница подписок; см. план по eGames). Здесь только фиксируем связь: у subscription-page есть env
  `EGAMES_COOKIE` — вторая половина той же фичи.
- **Обратная совместимость: не мигрируем legacy-каталог.** Существующие записи получают `kind:"html"` —
  читаются, скачиваются и деплоятся ровно как сейчас (одиночный file-mount). Новые создаются как
  `kind:"overlay"`. Авто-миграция бессмысленна: legacy-Orion HTML не является валидным фронтендом 7.x.
- **Предпросмотр для overlay — НЕ `srcDoc`-iframe.** Относительные `/assets/...` и EJS-теги делают его
  бессмысленным. Вместо него — дерево файлов + редактор + пометка «изменён/новый» относительно baseline и
  кнопка «вернуть к базовому». **DEFAULT: построчный diff-виджет не тащим** (новой либы нет, и она не нужна).
  Живой предпросмотр требует настоящего served-роута со stub-ом `panelData` — отдельная фича, не в этой волне.
- **Лимиты стора (DEFAULT):** `MAX_FILE_BYTES = 25 MiB` (число переиспользуем из `library_store.py:22`),
  `MAX_VARIANT_BYTES = 64 MiB` (суммарно на overlay), `MAX_FILES_PER_VARIANT = 2000`, `MAX_PAGES = 100`
  (без изменений). Baseline-кэш: `MAX_BASELINE_BYTES = 256 MiB` на дерево, tar-slip отвергается жёстко.
- **Baseline-кэш — ГЛОБАЛЬНЫЙ**, `DATA_DIR/subpage_baselines/<digest>/` (DEFAULT). Это содержимое апстрим-образа,
  не данные аккаунта; прецедент глобального стора уже есть (`xray_checker_metrics.db`, CLAUDE.md §1b).
  Per-account хранение дублировало бы десятки МБ на каждый аккаунт без выигрыша в изоляции.
- **Отдача файлов клиенту — только `application/octet-stream` + `Content-Disposition: attachment` + `nosniff`
  + `CSP: sandbox`.** Наш origin = origin SPA; отдать чужой JS/HTML как `text/html` с нашего домена — это XSS
  на себе. Legacy `/raw` сохраняет текущее поведение и текущие заголовки (`api/subpages.py:54-57`).
- **`PanelDeployRequest.subpage_html` НЕ удаляем** — добавляем `subpage_variant_id: str = ""` рядом. Причина:
  `panel_jobs_<id>.savedForm` в localStorage хранит старые payload-ы, и `PanelOpRequest` их ре-валидирует
  (`api/panel_deploy.py:170-177`) — ломать переустановку уже развёрнутых панелей нельзя.
- **Токен страницы подписок в теле запроса — как SSH-пароли.** Он транзитный на нашей стороне (никогда не
  ложится в наш стор), но фронт кладёт `savedForm` в `panel_jobs_<id>` localStorage — ровно как уже делает с
  SSH-паролями панелей (CLAUDE.md §7d). Осознанно и симметрично существующему поведению; в UI — пометка.
- **Авто-создание токена через `POST /api/tokens` — НЕ делаем.** Контроллер панели запрещён для API-ключа
  («can only be used with an admin JWT-token», подтверждено в `api-1.json` v2.8.0), значит потребовался бы
  логин/пароль админа панели. Оператор вставляет токен вручную (как уже рекомендовал План D, R2).

## Стратегия

Ф1 (живая сверка образа + починка деплоя: токен, пиннинг, `scope` в «Переменных») → Ф2 (клиент + API панельных
`subscription-page-configs`) → Ф3 (frontend панельного каталога) → Ф4 (baseline: стягивание дерева по SSH +
кэш по digest) → Ф5 (overlay-стор + per-file API) → Ф6 (деплой overlay: материализация на ноде + монтирование
каталога) → Ф7 (frontend: файловый браузер/редактор + селектор варианта в форме деплоя).

---

### Ф1 — Живая сверка образа + починка сломанного деплоя → verify: живой запуск образа + `cd backend && python -m pytest`

**Шаг 0 (обязателен, до правок кода) — подтвердить на живом образе.** Всё ниже про внутренности образа
разведка вывела из исходников апстрима, но НЕ исполняла:
```
docker run --rm --entrypoint sh remnawave/subscription-page:7.2.6 -c 'ls -R /opt/app/frontend | head -80'
docker run --rm -e REMNAWAVE_PANEL_URL=http://127.0.0.1:3000 -e REMNAWAVE_API_TOKEN= \
    remnawave/subscription-page:7.2.6            # ожидаем падение на zod-валидации
```
Зафиксировать в этом файле фактическую раскладку (`assets/`, `locales/`, фавиконки, наличие/отсутствие
`assets/app-config.json` в 7.x) и фактический текст ошибки при пустом токене. **Если Шаг 0 опровергнет
премиссу — остановиться и пересогласовать Ф4-Ф7** (Ф1-Ф3 останутся полезными в любом случае).

- **`models/panel_deploy.py`** — добавить `subpage_api_token: str = ""` (валидатор: одна строка, без
  `"`/`` ` ``/`$`/CR/LF — значение уходит в `.env`; длина ≤ 4096) и `subpage_image: str = ""`
  (regex `^[a-z0-9]([a-z0-9._/-]*[a-z0-9])?(:[A-Za-z0-9._-]{1,128})?$`, пусто → константа пайплайна).
  В `validate_by_target` — требовать `subpage_api_token` при `target ∈ {subpage, both}`.
- **`services/panel_pipeline.py`** — `_SUBPAGE_IMAGE = "remnawave/subscription-page:7.2.6"`, использовать в
  `_subpage_compose` вместо `:latest` (`:319`). `_subpage_env` (`:281-299`) — писать реальный токен; добавить
  `SUBPAGE_CONFIG_UUID` только если Шаг 0 подтвердит, что дефолт-all-zeros неудобен (**НЕ ПРОВЕРЕНО**, как
  именно он выбирает конфиг из полученного от панели списка). `_install_subpage` (`:546-551`) — писать
  subpage `.env` через **тихий канал** (`get_script_output`) по образцу `_write_env_script:348-363`, с
  merge-логикой «непустой существующий токен не затираем пустым» (симметрично `api/panel_deploy.py` merge
  замаскированных секретов). Ввести/сохранить sentinel-и, чтобы отличать «записано»/«уже было».
- **`api/panel_deploy.py`** — `_ENV_PATH` → функция `_env_path(scope)` (`panel` → `/opt/remnawave/.env`,
  `subpage` → `/opt/remnawave-subpage/.env`); `EnvReadRequest`/write-модель получают
  `scope: Literal["panel","subpage"] = "panel"`; `_COMPOSE_UP_SCRIPT` параметризовать каталогом и именем
  контейнера, который проверяем поднявшимся. Заменить вводящий в заблуждение лог `:566-570`.
- **`rw/PanelDeployForm.tsx`** — поле «API-токен Remnawave» (`secret`) в секции страницы подписок + валидация
  на клиенте, зеркалящая серверную (обязателен при subpage/both) + подсказка «Dashboard → API Tokens».
  **`rw/PanelVariables.tsx`** — сегмент «Панель / Страница подписок» (scope), заголовок отражает файл.
- verify: Шаг 0 воспроизведён; `cd backend && python -m pytest` (расширить `backend/tests/test_panel_deploy.py`
  и `test_panel_env.py`: 422 без токена при subpage/both, токен не появляется в логах задачи, `scope=subpage`
  читает/пишет второй `.env`, пиннинг образа в сгенерированном compose); `npx --no-install tsc --noEmit`;
  **ручной деплой страницы подписок доходит до «Страница подписок запущена»**.

---

### Ф2 — Панельные `subscription-page-configs`: клиент + наши роуты → verify: `cd backend && python -m pytest`

- **`services/remnawave_client.py`** (рядом с блоком subscription-templates, `:164-201`) — добавить:
  `list_subscription_page_configs()` (`GET /api/subscription-page-configs` → `response.configs`, есть `total`),
  `get_subscription_page_config(uuid)`, `create_subscription_page_config(name)`
  (`POST`, тело **только** `{name}`, 2–30, `^[A-Za-z0-9_\s-]+$` — санитайзить как
  `create_subscription_template:175-186`), `update_subscription_page_config(uuid, name=None, config=None)`
  (`PATCH`, `config` — **свободный JSON**), `delete_subscription_page_config(uuid)`,
  `clone_subscription_page_config(uuid)` (`POST /actions/clone`, тело `{cloneFromUuid}`),
  `reorder_subscription_page_configs(items)` (`POST /actions/reorder`, `{items:[{uuid,viewPosition}]}`).
  Все ответы разворачивать `_unwrap` (конверт `{response:…}`).
- **`api/subpage_configs.py`** (новый роутер `/api/subpage-configs`, монтировать в `main.py` под `_auth` рядом
  с `subpages.router:139`) — тонкий прокси над клиентом с фабрикой `_client()` по образцу
  `api/config_templates.py:23-28` (панель не настроена → 400). Роуты: `GET ""`, `GET /{uuid}`, `POST ""`,
  `PUT /{uuid}` (тело `{name?, config?}`), `POST /{uuid}/clone`, `POST /reorder`, `DELETE /{uuid}`.
  Локального стора **не заводим** — источник истины панель (клон+reorder у неё уже есть, каталог получается
  бесплатно).
- verify: новый `backend/tests/test_subpage_configs.py` — 400 без настроенной панели, маппинг путей/тел на
  замоканном httpx-транспорте (как в существующих тестах клиента), санитизация имени, разворачивание конверта.

---

### Ф3 — Frontend: каталог оформления (панельные конфиги) → verify: `npx --no-install tsc --noEmit` + `npm test`

- **`rw/SubPages.tsx`** — ввести две вкладки (`.seg`, как в `Hosts.tsx`): **«Оформление (панель)»** и
  **«Файлы фронтенда»**. В этой фазе делаем только первую: список конфигов (имя, `viewPosition`), создать,
  переименовать, клонировать, удалить, перетащить порядок (splice-реордер → `POST /reorder`, без DnD-либы —
  кнопки ↑/↓, как в `stats/statWidgetsStore`), редактор `config` — **`profiles/JsonEditor`** (уже
  переиспользован в `Templates.tsx`, схема-валидации нет — `config` свободный).
- Вторая вкладка на этой фазе показывает нынешний legacy-каталог без изменений (чтобы Ф3 была отгружаема
  отдельно).
- Плашка: «Оформление применяется контейнером страницы подписок при старте; после изменений — перезапустить
  контейнер» (контейнер читает конфиги на `onApplicationBootstrap`, см. РАЗВЕДКА).
- verify: `npx --no-install tsc --noEmit`; `npm test` (новый `SubPages.test.tsx`: список/создание/клон/
  реордер/ошибка «панель не настроена»); `docker compose build frontend`.

---

### Ф4 — Baseline: стягивание дерева с живого контейнера + кэш по digest → verify: pytest + ручной pull

- **`services/subpage_baseline.py`** (новый):
  - Чистые генераторы скриптов: `resolve_digest_cmd(image)` →
    `docker image inspect --format '{{index .RepoDigests 0}}' <image>` (fallback на `{{.Id}}`, если образ
    собран локально/без RepoDigests); `extract_tree_script(image, tmpdir)` → `docker create` +
    `docker cp <cid>:/opt/app/frontend/. <tmpdir>/frontend/` + `docker rm` + `tar czf <tmpdir>/frontend.tgz -C
    <tmpdir>/frontend .`. Все интерполяции — через `shlex.quote` (образ приходит из модели, но валидируется
    regex-ом ещё и там).
  - `pull_baseline(ssh, image) -> dict` — выполнить скрипты, SFTP-скачать `frontend.tgz` во временный каталог
    бэкенда (`tempfile.TemporaryDirectory`, паттерн `panel_sync.py:123-133`), распаковать в
    `DATA_DIR/subpage_baselines/<digest>/` **с жёсткой пер-членной валидацией tar** (отклонять абсолютные
    пути, `..`, симлинки/хардлинки, устройства; суммарный размер ≤ `MAX_BASELINE_BYTES`), собрать
    `manifest.json` `[{path, size, sha256}]`. Идемпотентно: если каталог с этим digest уже есть — скачивание
    пропускается.
  - `list_baselines()`, `get_manifest(digest)`, `read_file(digest, relpath)` (пер-путь `resolve()`+`parents`
    гард, как `library_store.py:120-125`).
- **`api/subpages.py`** — новые роуты: `POST /api/subpages/baselines/pull` (creds-per-request:
  ip/ssh_user/ssh_password/ssh_port + `image`; стрим-Task, как остальные SSH-операции),
  `GET /api/subpages/baselines`, `GET /api/subpages/baselines/{digest}/files`,
  `GET /api/subpages/baselines/{digest}/files/{path:path}` (заголовки из развилки — attachment + nosniff +
  CSP sandbox).
- verify: `cd backend && python -m pytest` (новые тесты в `test_subpages.py`: генераторы скриптов содержат
  `docker cp`/`docker rm` и цитирование; **tar-slip отвергается** — синтетический архив с `../evil` и с
  симлинком; идемпотентность по digest; лимит размера); ручной `pull` с реальной ноды → в
  `DATA_DIR/subpage_baselines/<digest>/` лежит `index.html` + `assets/`.

---

### Ф5 — Overlay-стор + per-file API → verify: `cd backend && python -m pytest`

- **`services/subpage_store.py`** — эволюция без ломки legacy:
  - `index.json` получает `kind: "html" | "overlay"` (отсутствует → `"html"`), а для overlay ещё
    `base_image`, `base_digest`, `files_count`, `bytes`.
  - Дерево overlay: `accounts/<id>/subpages/<page_id>/files/<relpath>`; манифест — либо `…/manifest.json`,
    либо поле в `index.json` (**DEFAULT: отдельный `manifest.json` рядом с деревом** — `index.json` остаётся
    дешёвым для листинга, как задумано в докстринге `:1-15`).
  - Новые функции: `add_overlay(name, base_image, base_digest)`, `list_files(page_id)`,
    `put_file(page_id, relpath, data)`, `get_file(page_id, relpath)`, `delete_file(page_id, relpath)`,
    `overlay_zip(page_id)`. Все — под тем же `_INDEX_LOCK` для RMW индекса/манифеста.
  - **Валидация relpath (обязательна, membership-гард здесь не работает):** только `[A-Za-z0-9._/-]`,
    без ведущего `/`, без сегмента `..`, без пустых сегментов, глубина ≤ 10, длина ≤ 200; плюс
    defence-in-depth `resolve()` + проверка `parents` перед любым чтением/записью.
  - Лимиты из развилки (`MAX_FILE_BYTES`/`MAX_VARIANT_BYTES`/`MAX_FILES_PER_VARIANT`); `MAX_HTML_BYTES` и
    legacy-функции (`add_page`/`get_page_html`/`delete_page`) **оставить как есть**.
- **`api/subpages.py`** — `POST /api/subpages/overlay` (создать вариант), `GET /{id}/files`,
  `GET|PUT|DELETE /{id}/files/{path:path}`, `GET /{id}/download` (zip только overlay-файлов).
  `PUT` принимает сырое тело (`Request.body()`), лимит проверяется до записи → 413. Заголовки отдачи — из
  развилки. Legacy-роуты не трогаем.
- **Мягкое предупреждение по EJS.** При `PUT` файла `index.html` в overlay — если содержимое НЕ содержит
  `<%- panelData %>`, отдать в ответе флаг `warning: "index.html без <%- panelData %> — SPA не получит данные
  подписки"`. **Предупреждение, не отказ** (эвристика; апстрим вправе переименовать плейсхолдер).
- verify: `cd backend && python -m pytest` (расширенный `test_subpages.py`: legacy-CRUD не сломан; создание
  overlay; put/get/delete файла; **отклонение `../`, ведущего `/`, глубины, размера, количества**; изоляция
  между аккаунтами; zip содержит только overlay-файлы; EJS-warning).

---

### Ф6 — Деплой overlay: материализация на ноде + монтирование каталога → verify: pytest + ручной деплой

- **`models/panel_deploy.py`** — `subpage_variant_id: str = ""` (12-hex или пусто). `subpage_html` остаётся
  (legacy). Валидация: одновременно заданные `subpage_variant_id` и непустой `subpage_html` → приоритет у
  variant_id (и предупреждение в лог), чтобы старые savedForm-ы не ломались.
- **`services/panel_pipeline.py`**:
  - `_subpage_compose` — при overlay-варианте вольюм становится `- ./frontend:/opt/app/frontend`; при
    legacy-html остаётся текущая строка `- ./index.html:/opt/app/frontend/index.html` (`:305-309`).
  - `_install_subpage` — новый шаг перед `docker compose up`: (1) прочитать digest образа на ноде и **сравнить
    с `base_digest` варианта** → расхождение = жёлтое предупреждение в логе, не отказ; (2) `rm -rf
    /opt/remnawave-subpage/frontend` и заново материализовать дерево из образа (`docker create`/`docker cp`,
    те же генераторы, что в Ф4 — вынести в общий модуль), чтобы удалённый из overlay файл откатывался к
    базовому; (3) залить overlay: собрать zip стора → **SFTP `upload_file`** во временный путь на ноде →
    распаковать `unzip -o` в `/opt/remnawave-subpage/frontend/` (перед распаковкой отфильтровать пути тем же
    правилом, что и на нашей стороне — распаковываем СВОЙ архив, но гард дешёвый и обязателен) → удалить
    архив. `_write_html_script` для overlay НЕ используется.
  - Разрешение варианта — по `subpage_variant_id` из стора **активного аккаунта** (ContextVar уже копируется
    в `asyncio.create_task`, CLAUDE.md §1b); в фоне — явный `account_id`, если понадобится.
- **`api/panel_deploy.py`** — `PanelOpRequest` наследует новое поле автоматически; убедиться, что
  `_panel_reinstall` (`:263-281`) для `component="subpage"` тянет вариант из стора (payload больше не
  самодостаточен — это осознанное изменение, зафиксировать в докстринге модуля).
- verify: `cd backend && python -m pytest` (генераторы: вольюм каталога vs файла, наличие `rm -rf` +
  `docker cp` + `unzip`, предупреждение о digest, приоритет variant_id над legacy html); **ручной деплой**:
  вариант с изменённым `index.html` → страница отдаёт подписку (SPA получает данные) → переустановка
  компонента `subpage` через `/api/panel/step` даёт тот же результат.

---

### Ф7 — Frontend: файловый браузер/редактор + селектор варианта → verify: tsc + `npm test` + preview

- **`rw/SubPages.tsx`**, вкладка «Файлы фронтенда»:
  - Список вариантов (legacy `kind:"html"` — с бейджем «legacy», только просмотр/скачивание/удаление и старый
    `srcDoc`-предпросмотр; overlay — полноценный редактор).
  - Кнопка «Стянуть базу с сервера» → форма SSH-кредов (транзитные) → `POST /api/subpages/baselines/pull`
    (стрим в модалке через `useTaskStream`, как `PanelManageModal`).
  - Дерево файлов = объединение манифеста baseline и манифеста overlay, с бейджами «изменён»/«новый»;
    клик по файлу → редактор (JSON → `profiles/JsonEditor`, прочий текст → textarea с моно-шрифтом, бинарь →
    только скачать/заменить файлом). Кнопки «Сохранить», «Вернуть к базовому» (= `DELETE` файла из overlay),
    «Добавить файл» (загрузка). Плашка-warning про `<%- panelData %>` из Ф5.
  - Предпросмотр `srcDoc` для overlay **убрать** (заменить пояснением, почему он невозможен).
- **`rw/PanelDeployForm.tsx`** — секция «Страница подписок»: вместо `<textarea>` + `pickCatalog` (`:345-374`,
  `:569-613`) — `<select>` вариантов (`""` = стоковая страница; legacy-записи помечены), поле токена из Ф1,
  подсказка про digest baseline. `subpage_html` в форме больше не редактируется, но остаётся в payload для
  legacy-вариантов (заполняется автоматически при выборе `kind:"html"`). `SUBPAGE_MAX` (`:105`) удалить,
  если больше не используется.
- verify: `npx --no-install tsc --noEmit`; `npm test` (`SubPages.test.tsx`: рендер дерева, бейджи изменённых,
  «вернуть к базовому», отсутствие `srcDoc` для overlay; `PanelDeployForm.test.tsx`: валидация токена и выбор
  варианта); `docker compose build frontend`; preview: стянуть baseline → создать overlay → изменить
  `index.html` → задеплоить → проверить страницу; мобильная раскладка ≤820px не ломается.

## РАЗВЕДКА (факты)

- **Контейнер — не статика, а NestJS + вшитый Vite/React.** Dockerfile апстрима
  (https://github.com/remnawave/subscription-page/blob/main/Dockerfile): multi-stage, runtime делает
  `COPY --from=backend-build /opt/app/dist ./dist` и `COPY frontend/dist/ ./frontend/`, запуск —
  `pm2-runtime start ecosystem.config.js` через `docker-entrypoint.sh`; WORKDIR `/opt/app` ⇒ статика живёт в
  **`/opt/app/frontend/`** и в git не коммитится (собирается в CI). **НЕ ПРОВЕРЕНО живьём:** точная раскладка
  дерева (`assets/`, `locales/`, фавиконки, наличие `assets/app-config.json` в 7.x) — это Шаг 0 Ф1.
- **EJS — ключевая механика.** `backend/src/main.ts`: `app.useStaticAssets('/opt/app/frontend', {index:false,
  dotfiles:'ignore'})` + EJS зарегистрирован как view-engine для `.html` с тем же каталогом как views-root.
  `index:false` — причина, по которой `index.html` НИКОГДА не отдаётся статикой, он всегда рендерится.
  `root.service.ts`: `res.render('index', {metaTitle, metaDescription, panelData})`, где
  `panelData = base64(JSON.stringify(subscriptionData))`; `frontend/index.html` несёт `<%- metaTitle %>`,
  `<%- metaDescription %>` и `<%- panelData %>` на `<div id="sbpg">`. ⇒ **любой кастомный `index.html`
  ОБЯЗАН сохранить `<%- panelData %>`** и не содержать посторонних `<%`-последовательностей.
- **Зарезервированные префиксы.** `root.controller.ts` рвёт сокет для любого запроса, чей путь начинается с
  `/assets` или `/locales` (ожидается, что их уже отдал static-middleware). ⇒ кастомный бандл обязан держать
  свои файлы под `assets/` (и `locales/`, если использует i18n) — свой статический корень изобрести нельзя.
- **7.0.0 (2025-12-19) увёл кастомизацию с ФС в панель.** `subpage-config.service.ts`: на
  `onApplicationBootstrap` контейнер зовёт `getSubscriptionPageConfigList()` + `getSubscriptionPageConfigByUuid()`
  на каждый конфиг, zod-валидирует и делает `exit(1)` при пустом списке / ошибке фетча / невалидной схеме.
  Конфиги держатся в in-memory `Map` и отдаются браузеру по маршруту, ключ которого зашифрован
  `INTERNAL_JWT_SECRET`. META_TITLE/META_DESCRIPTION/SUBSCRIPTION_UI_DISPLAY_RAW_KEYS переехали в «Subpage
  Builder». Старый монтируемый `assets/app-config.json` мёртв. Требуется панель ≥ 2.4.0.
- **Env-контракт образа.** `backend/src/common/config/app-config/config.schema.ts`: `REMNAWAVE_API_TOKEN` =
  `z.string().min(1)` **обязателен**; `INTERNAL_JWT_SECRET` = `z.string()` обязателен, но генерируется
  **самим entrypoint-ом** на каждый старт (`randomBytes(64).toString('hex')`) — оператор его не задаёт (и,
  как следствие, зашифрованный uuid конфига для браузера ротируется при каждом рестарте);
  `SUBPAGE_CONFIG_UUID` = `.default('00000000-…-0000')`; плюс `APP_PORT`(3010), `REMNAWAVE_PANEL_URL`,
  `CUSTOM_SUB_PREFIX`, `TRUST_PROXY`(default `'1'`), `CADDY_AUTH_API_TOKEN`, `CLOUDFLARE_ZERO_TRUST_*`,
  `MARZBAN_LEGACY_*`, **`EGAMES_COOKIE`**. Наш `_subpage_env:292-298` пишет 5 ключей и оставляет обязательный
  токен пустым.
- **Документированная установка вольюмов не имеет вообще** (docs.rw/install/subscription-page/{separate-server,
  bundled}): образ `:latest`, `127.0.0.1:3010:3010`, `env_file: .env`, внешняя `remnawave-network`; на панели
  ставится `SUB_PUBLIC_DOMAIN` (голый домен, без схемы); токен берётся из Dashboard → API Tokens. ⇒ **замена
  фронтенда апстримом не поддерживается**, монтирование — наше собственное изобретение (структурно рабочее).
- **Версии.** `latest == 7.2.6` (запушен 2026-06-24, в день коммита `chore: release v7.2.6`) ⇒ main ≈ latest;
  7.0.0 — 2025-12-19; 6.4.2 — последний 6.x (2025-12-11). Наш compose (`panel_pipeline.py:319`) пинит
  плавающий `:latest` прямо через границу 6→7.
- **Overlay версионно-зависим.** `frontend/vite.config.ts` апстрима: кастомного `base` нет (по умолчанию `/`),
  `outDir: 'dist'`, manual chunks `icons/date/react/mantine/i18n`, именование по умолчанию ⇒
  `assets/[name]-[hash].js|css`. Хэши меняются на каждой сборке ⇒ overlay валиден только против конкретной
  базовой версии; хранить тег И digest, при расхождении — предупреждать.
- **Панельная ось кастомизации уже в нашем `api-1.json` (v2.8.0):** `/api/subscription-page-configs`
  GET+POST+PATCH, `/{uuid}` GET+DELETE, `/actions/clone`, `/actions/reorder`.
  `CreateSubscriptionPageConfigRequestDto` = только `{name}` (2–30, `^[A-Za-z0-9_\s-]+$`);
  `UpdateSubscriptionPageConfigRequestDto` = `{uuid, name?, config}`, где **`config` — свободный JSON**;
  ответ-листинг = `{response:{total, configs:[{uuid, viewPosition, name, config}]}}`;
  clone = `{cloneFromUuid}`; reorder = `{items:[{uuid, viewPosition}]}`. Клон+reorder ⇒ «каталог вариантов»
  на этой оси почти бесплатен и не требует файлового хранилища.
- **Авто-выдача токена заблокирована панелью.** `POST /api/tokens` в `api-1.json` v2.8.0 несёт описание
  «This endpoint is forbidden to use via "API-key". It can only be used with an admin JWT-token»;
  `CreateApiTokenRequestDto = {name(2-30), expiresInDays(≥1), scopes=["*"]}`, `token` возвращается открытым
  один раз. Совпадает с блокером R2 неотгруженного Плана D.
- **План D Волны 3 — поглощаем, но одну его строку исправляем.** Его R1 утверждает, что официальная точка
  кастомизации — «ТОЛЬКО `assets/app-config.json`». Это верно для ≤6.x и **неверно** для 7.x, который мы
  разворачиваем (конфиг переехал в панель). Остальная его конструкция (zip-slip-гард, SFTP, монтирование
  каталога, `kind:'html'` для обратной совместимости) — переиспользуется как есть.
- **Что чинить в CLAUDE.md по итогам:** §7d («Каталог Orion (Ф5)») описывает нынешний стор верно, но после
  этого плана станет неактуальным целиком (kind html|overlay, baseline-кэш, новые роуты, панельные конфиги,
  пиннинг образа, `scope` в «Переменных»). Плюс исправить докстринги `subpage_store.py:3-5` и
  `SubPages.tsx:4-7` — они и есть источник заблуждения «страница подписок = один HTML».
- **Вне области плана E:** мёртвый тоггл `cookie_gate` (`models/deploy.py:47`, `DeployForm.tsx:34,102,567` —
  поле есть, в пайплайне не используется ни разу) — это нода eGames, а не страница подписок; чинится планом
  по eGames. Связь зафиксировать: у subscription-page есть парный env `EGAMES_COOKIE`.

## Критерии готовности плана E

- Шаг 0 Ф1 выполнен на живом образе, фактическая раскладка `/opt/app/frontend` и поведение при пустом
  `REMNAWAVE_API_TOKEN` записаны в этот файл (или премисса опровергнута и Ф4-Ф7 пересогласованы).
- **Деплой страницы подписок работает end-to-end**: токен обязателен в форме, пишется тихим каналом в
  `/opt/remnawave-subpage/.env`, контейнер поднимается, «Переменные» умеют `scope=subpage`; образ запинен
  (никакого `:latest`).
- Панельный каталог оформления (`subscription-page-configs`) доступен из UI: список/создать/переименовать/
  клонировать/переупорядочить/удалить + редактор свободного `config`; панель не настроена → внятные 400.
- Baseline стягивается с ноды по SSH, кэшируется по **digest**, tar-slip и лимиты покрыты тестами.
- Overlay-вариант: per-file CRUD, лимиты и пер-путь гарды покрыты тестами; legacy `kind:"html"` записи
  читаются/деплоятся без регресса и не мигрируются автоматически.
- Деплой overlay: дерево материализуется из образа на ноде, overlay накладывается сверху, монтируется
  **каталог**; расхождение digest — предупреждение; переустановка компонента `subpage` даёт тот же результат.
- Frontend: файловый браузер + редактор + бейджи «изменён/новый» + «вернуть к базовому»; `srcDoc`-предпросмотр
  для overlay убран (для legacy сохранён); форма деплоя выбирает вариант, а не хранит HTML в textarea.
- Отдача любых файлов варианта/baseline — `attachment` + `nosniff` + `CSP: sandbox`, никогда `text/html`
  с нашего origin.
- `cd backend && python -m pytest` зелёный; `npx --no-install tsc --noEmit` и `npm test` зелёные;
  `docker compose build frontend` собирается.
- CLAUDE.md §7d переписана (kind html|overlay, baseline-кэш, новые роуты `/api/subpage-configs` и
  `/api/subpages/baselines/*`, пиннинг образа, `scope` в «Переменных»), докстринги `subpage_store.py` и
  `SubPages.tsx` исправлены, План D Волны 3 помечен как поглощённый этим планом.

---

# РАЗВЕДКА 2026-07-22 (Волна 7, План G) — проверено, план исправлен

> Пять параллельных читателей + критик, часть фактов снята прогоном docker и чтением api-1.json.
> Ниже — только то, что **меняет план**. Совпавшее с планом не повторяется.

## Подтверждено фактом

- **Раздел `subscription-page-configs` в API СУЩЕСТВУЕТ** (`api-1.json:1711, 2072, 2320, 2445`, Remnawave
  v2.8.0). Конверт `{response:…}` у всех семи DTO. Это была главная непроверенная предпосылка Ф2.
- **Раскладка образа 7.2.6** (снята `docker create` + `docker cp`): `/opt/app/frontend` = ровно `index.html`
  + `assets/`, **160 файлов, 7.0 МБ**. `locales/` НЕ существует. `index.html` несёт `<%- panelData %>`,
  `<%= metaTitle %>`, `<%= metaDescription %>`.

## Опровергнуто — правки обязательны

1. **Ф3 «редактор `config` — `profiles/JsonEditor`, схема-валидации нет» — НЕВЕРНО.**
   `profiles/JsonEditor.tsx:36` компилирует ajv-схему ВСЕГДА, дефолт `schemaMode='full'` = `xrayConfigSchema`;
   в `core/schema.ts:182-184` девять режимов, все Xray-шные, режима «свободный JSON» нет.
   ⇒ либо добавлять режим `free`, либо брать другой редактор.

2. **Ф4 `resolve_digest_cmd` ПЕРВЫМ — упадёт на чистой ноде.** Проверено: `docker create` авто-пуллит
   отсутствующий образ, а `docker image inspect` — нет (`No such image`). ⇒ порядок обязан быть
   `docker pull`/`docker create` → и только потом `inspect`.

3. **Ценность Ф7 не та, что предполагалась.** Из 160 файлов **149 бинарные** (140 woff2, 8 png, 1 svg);
   текстовых, которые вообще можно редактировать, — **11**, и все минифицированы (крупнейший 300 КБ).
   ⇒ Ф7 на 93% файловый МЕНЕДЖЕР (заменить/скачать), а не редактор. UI планировать исходя из этого.

4. **Ловушка `assets/app-config.json`.** Файл ЖИВ в образе (207 932 Б), но SPA просит
   `assets/app-config-v2.json` (виден в `i18n-Bf6j5AtI.js`), которого в дереве нет. То есть shipped-файл —
   мёртвый артефакт сборки, а живой конфиг приходит с панели. Оператор увидит 208-КБ «конфиг оформления» и
   будет править его вместо панельного `subscription-page-config`. ⇒ прятать или помечать плашкой.

5. **Ссылка плана на `api/config_templates.py:23-28` устарела** после Волны 7, Плана C: фабрика клиента
   теперь `panel_registry.client_for(panel_id)` с `PanelNotFound`→404 / `PanelNotConfigured`→400.

6. **Санитизацию имени нельзя копировать из `create_subscription_template`** (`remnawave_client.py:175-186`):
   там срез `[:255]`, а у page-config максимум **30** (`minLength 2`, `^[A-Za-z0-9_\s-]+$`). Копипаст → 400.

7. **Сигнатуру reorder нельзя копировать из нашего MCP-форка**: `mcp/src/tools/subscription-page-configs.ts:38-41`
   принимает `{uuids: string[]}`, а API ждёт `{items:[{uuid, viewPosition}]}` (`api-1.json:25063`).

8. **Create принимает ТОЛЬКО `{name}`** ⇒ создание с готовым `config` = два запроса (create → PATCH).
   Обновление — **PATCH на КОЛЛЕКЦИЮ с uuid в теле**; отдельного `PATCH /{uuid}` и `PUT` нет.

## Мины, найденные в существующем коде

- **Листинг отдаёт записи сырыми** (`subpage_store.py:78`) ⇒ overlay-записи немедленно приедут в
  `PanelDeployForm.tsx:338-341`, клик по ним даст `/raw` → 404. Фильтр по `kind` обязан появиться
  ОДНОВРЕМЕННО с overlay, иначе видимый регресс.
- **`SubPages.tsx:207` зовёт `fmtSize(p.size)`**, а `fmtSize(undefined)` даёт `'NaN КиБ'` ⇒ overlay-запись
  ОБЯЗАНА нести числовой `size`.
- **Порядок роутов**: существует `DELETE /{page_id}`; литеральный `DELETE /api/subpages/baselines` того же
  метода будет им проглочен — литералы регистрировать ВЫШЕ параметризованных.
- **Гард пути**: membership-гард закрывает только `page_id`. Для `relpath` нужен свой (запрет `..`,
  ведущего `/`, windows-путей, `:`, обратного слэша + финальный `resolve().is_relative_to(root)`).
  Идиому `library_store` копировать нельзя — она плоская и санитайзит ИМЯ, а не путь.
- **ContextVar в фоне**: замер сделан на fastapi **0.115.6**, а `requirements.txt` пинит **0.111.0**.
  ⇒ не полагаться; захватывать `account_id` в хендлере явно, как `api/testservers.py:91-93`.

## Блокирующие неизвестные — Ф3 и Ф6 вслепую НЕ писать

1. **Форма поля `config`**: в спеке `{}`, в контракте `z.nullable(z.unknown())`. Панель валидирует своей
   схемой, текст её 400 непредсказуем. Без живой панели ≥7.x снять неоткуда.
2. **Семантика PATCH по `config`**: merge или replace; трактуется ли отсутствующий ключ как `null`.
   Риск «послали `{uuid,name}` → обнулили оформление» реален и в спеке не различим.
3. **Как контейнер выбирает конфиг** из списка (нужен ли `SUBPAGE_CONFIG_UUID` в `.env`).
4. **`rm -rf` под живым bind-mount `./frontend:/opt/app/frontend`** (Ф6) — мина; ни `down→материализация→up`,
   ни `temp+swap` не проверены.

## Решение по порядку

Ф2 (клиент+роуты, контракт теперь точно известен) → **Ф5** (overlay-стор, полностью локальный) →
**Ф4** (baseline; docker-механика уже проверена) → пауза на снятие блокирующих неизвестных с живой панели →
Ф6 → Ф3/Ф7. Ф4 и Ф5 обе правят `api/subpages.py`, поэтому строго последовательно.

**До первой строки Ф4/Ф5 зафиксировать письменно набор заголовков per-file отдачи** — сейчас на этот вопрос
три разных ответа (план: attachment+nosniff+CSP sandbox; разведка стора: sandbox несовместим с отдачей
фронтенда; существующий прецедент `library.py:63-65`: пользовательский mime вообще без nosniff).

---

# Ф6 — эксперимент с bind-mount 2026-07-24 (одна из четырёх «блокирующих неизвестных» СНЯТА)

> Проверено на живом Docker (Linux-контейнеры). Топология воспроизведена точно: контейнер держит каталог
> примонтированным и перечитывает его раз в секунду (= subscription-page-сервер на каждый запрос), а внешний
> процесс (= наш SSH-шаг Ф6) чистит и заново материализует ТОТ ЖЕ каталог.

**Буквальный текст плана `rm -rf /opt/remnawave-subpage/frontend` НЕВЕРЕН — ломается дважды:**
1. Удалить сам каталог точки монтирования нельзя, пока он смонтирован в живой контейнер → команда возвращает
   **rc=1**. Под `set -euo pipefail` (а именно так пишутся наши скрипты) это оборвёт весь шаг.
2. При этом СОДЕРЖИМОЕ всё же стирается, и живой контейнер немедленно начинает отдавать пустоту
   (`cat: can't open '/opt/app/frontend/index.html'`) — окно, в котором страница подписок мертва.

**Правильный рецепт (проверено, rc=0, без падения контейнера):**
```sh
find /opt/remnawave-subpage/frontend -mindepth 1 -delete   # чистит СОДЕРЖИМОЕ, точку монтирования не трогает
# → материализовать baseline из образа (docker create/cp, см. Ф4)
# → распаковать overlay (unzip -o), с тем же per-member гардом, что и на нашей стороне
docker compose restart subscription-page   # рестарт ради ПРИЛОЖЕНИЯ, не ради ФС
```

**Два отдельных факта, которые важно не путать:**
- **На уровне ФС простоя нет.** Живой контейнер увидел новые файлы (включая новый `assets/vendor.js` и
  изменённый `index.html`) СРАЗУ, без рестарта — bind-mount общий, новые открытия файлов видят новое дерево.
- **На уровне ПРИЛОЖЕНИЯ рестарт всё равно нужен.** subscription-page — Node/EJS-приложение; по разведке оно
  читает конфиги на `onApplicationBootstrap`, а `index.html`-шаблон, вероятно, компилируется один раз при
  старте. Мой alpine-пробник доказывает только ФС-поведение (нет краха, живая видимость), НЕ отсутствие
  кэша в самом приложении. Поэтому финальный `restart` обязателен — и он в плане уже был.

**Вывод для Ф6:** контейнер во время подмены останавливать НЕ нужно (`down→up` не требуется), достаточно
`find -delete` + материализация + `restart`. Окно «пустого каталога» между delete и материализацией
сокращать материализацией во временный каталог рядом и атомарным свопом — НЕ обязательно (рестарт в конце
и так перечитывает), но если нужен ноль 404-ответов в это окно — только тогда temp+swap оправдан.

**Оговорка честности:** пробник гонялся на Docker Desktop поверх Windows (шэринг хостового каталога через
virtiofs). «rm точки монтирования → rc=1» — инвариант VFS Linux и на ноде идентичен. «Живая видимость новых
файлов» на нативном bind-mount ноды как минимум не слабее, чем показал шэринг-слой, так что вывод
консервативен. Осталось снять на живой ноде только тайминги `docker cp` 7 МБ + SFTP overlay (это Ф4/Ф6
перф, не корректность).

**Осталось три из четырёх неизвестных — ВСЕ снимаются одним прогоном `scripts/probe_subpage_config.py`
на живой панели ≥7.x:** форма поля `config`, семантика PATCH по нему (merge/replace), привязка config через
внешний сквад (нужен ли `SUBPAGE_CONFIG_UUID` контейнеру).
