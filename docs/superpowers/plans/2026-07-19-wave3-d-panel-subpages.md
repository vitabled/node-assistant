# Волна 3 · План D — Панель ↔ страницы подписок: новый бандл, подключение к панели, несколько sub-page

> Пункты: 8a (подключить sub-page к существующей/установленной панели по токену; несколько sub-page на панель),
> 9 (новый бандл-формат страницы подписок вместо одного HTML + загрузка/выбор архивов).
> Затрагивает: `services/subpage_store.py`, `api/subpages.py`, `services/panel_pipeline.py`,
> `models/panel_deploy.py`, `api/panel_deploy.py`, `remnawave_client.py`, `frontend/rw/SubPages.tsx`,
> `rw/PanelDeployForm.tsx`, `rw/PanelDashboard.tsx`. **Самый крупный и требующий разведки план.**

## Контекст (как есть)

- Текущая «страница подписок» = ОДИН статический `index.html` (Orion): `subpage_store.py` хранит
  `accounts/<id>/subpages/<page_id>.html` (лимит 512KiB, `MAX_PAGES=100`); `PanelDeployRequest.subpage_html`
  (одна строка); `panel_pipeline._install_subpage` пишет этот HTML в `/opt/remnawave-subpage/index.html` и
  волюм-маунтит в контейнер `remnawave/subscription-page:latest`.
- `_subpage_env` ставит `APP_PORT=3010`, `REMNAWAVE_PANEL_URL` (пусто для subpage-only/внешней панели —
  «set via Variables»), предупреждает про `REMNAWAVE_API_TOKEN` (Dashboard → API Tokens). `_subpage_compose`
  задаёт сервис. Reverse-proxy caddy/nginx. Подключение к панели сейчас **ручное** (через
  `/api/panel/env/write` — Ф8 Variables).
- **Новый формат (архив от пользователя):** официальный фронт-бандл `remnawave/subscription-page` — многофайловый
  статический SPA (`index.html` + `assets/` c Monaco/Go-WASM/`xray.schema.json`), ходит на относительный
  `/subscription`, конфиг панели инъектит серверный контейнер. То есть sub-page — уже НЕ один HTML, а бандл.
- Официальная механика (docs.rw/install/subscription-page/separate-server): панель `.env` +=
  `SUB_PUBLIC_DOMAIN`; sub-server `.env`: `REMNAWAVE_PANEL_URL` + `REMNAWAVE_API_TOKEN` (из «Dashboard →
  API Tokens») + `APP_PORT=3010` + `CUSTOM_SUB_PREFIX` + `TRUST_PROXY`; несколько sub-page = разные домены,
  тот же/разные токены.

## Развилки (закреплены)

- Sub-page разворачивается контейнером `remnawave/subscription-page:latest`; кастомизация = **загрузка своего
  фронт-бандла (zip)**, а не редактирование одного HTML.
- Подключение к панели: выбрать панель из `panel_jobs` ИЛИ внешнюю по URL; node-assistant создаёт/принимает
  API-токен и прописывает в `.env` sub-page; на панели ставит `SUB_PUBLIC_DOMAIN`.
- Несколько sub-page на одну панель (список, каждая — свой домен/токен).

## Разведка ПЕРЕД реализацией (обязательно)

- **R1.** Как `remnawave/subscription-page:latest` потребляет КАСТОМНЫЙ фронт-бандл: волюм-маунт статических
  файлов в директорию сервировки (какую?), переменная окружения, или отдельный образ? Определить путь маунта
  (сейчас маунтится один `index.html` — уточнить, куда именно, и поддерживает ли контейнер целую папку `assets/`).
- **R2.** Умеет ли Remnawave API **создавать API-токен** программно (`POST /api/tokens`?). Если да —
  автосоздание; если нет — оператор создаёт токен в панели и вставляет его (поле в форме). Проверить `api-1.json`
  + `remnawave_client.py`.
- **R3.** Установка `SUB_PUBLIC_DOMAIN` в панельный `.env` + рестарт панели — через существующий
  `/api/panel/env/write` (Ф8) или отдельный шаг.

## Стратегия

Ф1 (bundle-стор: zip вместо HTML) → Ф2 (подключение к панели: токен + env + SUB_PUBLIC_DOMAIN) → Ф3 (несколько
sub-page на панель) → Ф4 (frontend: загрузка/выбор архивов + мастер подключения).

---

### Ф1 — Bundle-стор → verify: pytest test_subpages + docker

1. **`services/subpage_store.py`** — эволюция с single-HTML на **бандл (zip)**:
   - Хранить `accounts/<id>/subpages/<page_id>.zip` (или распакованную папку) + `index.json`
     `[{id,name,size,files_count,created_at}]`. Лимит увеличить (бандл ~60МБ+ из-за WASM — задать разумный
     `MAX_BUNDLE_BYTES`, напр. 128МБ; `MAX_PAGES` оставить). Валидация zip: только внутри account-dir
     (**защита от zip-slip** — при распаковке проверять, что каждый путь остаётся под целевой папкой), запретить
     абсолютные/`..`-пути.
   - API `add_bundle(name, zip_bytes)`, `get_bundle_path(page_id)`, `list_pages`, `delete_page`. **Обратная
     совместимость:** старые single-HTML записи (если есть) — либо мигрировать (обернуть в бандл с одним
     index.html), либо помечать `kind:'html'` и поддерживать оба.
2. **`panel_pipeline._install_subpage`**: вместо записи одного `index.html` — **загрузить выбранный бандл** на
   сервер (SFTP `SSHSession.upload_file` архива → распаковка в `/opt/remnawave-subpage/frontend/`) и
   смонтировать папку в контейнер по пути из R1. `_subpage_compose` — добавить волюм кастомного фронта.
   `PanelDeployRequest.subpage_html` → заменить на `subpage_bundle_id` (ссылка на бандл из стора) или передавать
   архив отдельным полем/загрузкой. Санитизация путей при распаковке на ноде.
3. verify: `backend/tests/test_subpages.py` — zip upload/list/delete + zip-slip отвергается; `docker compose
   config`; smoke распаковки.

---

### Ф2 — Подключение к панели → verify: pytest + ручной сценарий

1. **Модель** (`models/panel_deploy.py`): добавить связь sub-page↔панель:
   - `panel_ref: Optional[str]` — id панели из `panel_jobs` (если привязка к своей), ИЛИ
   - `panel_url: str` + `panel_api_token: str` — внешняя панель (токен write-only, не логировать).
   - `sub_public_domain` (= `sub_domain`), `custom_sub_prefix`, `trust_proxy`.
2. **Токен (R2):** если Remnawave API умеет создавать токен — `remnawave_client.create_api_token(name)` →
   вернуть токен, прописать в sub-page `.env`. Иначе — оператор вставляет токен из панели (поле формы).
   Токен НЕ хранить в нашей БД (как остальные секреты — на целевом сервере в `.env` 0600).
3. **`.env` sub-page:** `_subpage_env` — заполнять `REMNAWAVE_PANEL_URL` (из выбранной панели/URL) +
   `REMNAWAVE_API_TOKEN` реально (сейчас пусто/ручное). Переиспользовать тихий канал записи (секрет не в логах).
4. **Панель:** прописать `SUB_PUBLIC_DOMAIN` в панельный `.env` + рестарт панели (R3). Только если панель
   управляется нами (`panel_ref`); для внешней панели — вывести инструкцию оператору.
5. **«Подключить после установки»** — эндпоинт `POST /api/panel/subpage/connect` (`api/panel_deploy.py`):
   привязать уже установленную sub-page к панели (создать токен + записать env + рестарт), стрим-Task.
6. verify: `backend/tests/test_panel_deploy.py`/`test_subpages.py` — сборка env с токеном (маскировка в логах),
   connect-эндпоинт; ручной сценарий подключения.

---

### Ф3 — Несколько sub-page на одну панель → verify: store isolation + preview

- Трекать развёрнутые sub-page per-account (новый `panel_subpages.json` или расширить `panel_jobs`):
  `{id, name, sub_domain, server_ip, panel_ref/panel_url, bundle_id, created_at}`. Список в UI, каждая —
  свой домен/токен/бандл. Одна панель → много записей.
- CRUD + связь с панелью; удаление sub-page (down контейнера, чистка).
- verify: несколько sub-page на одну панель в списке; изоляция per-account.

---

### Ф4 — Frontend → verify: tsc + preview

- **`rw/SubPages.tsx`** (был Orion single-HTML iframe): переделать в **каталог бандлов** — загрузка zip-архива
  (drag&drop/файл), список загруженных архивов, выбор архива при установке/редактировании sub-page. Превью —
  опционально (бандл тяжёлый; можно только метаданные). Старый srcDoc-iframe убрать/адаптировать.
- **`rw/PanelDeployForm.tsx`**: при `target` subpage/both — блок «Подключение к панели»: выбрать панель из
  `panel_jobs` ИЛИ ввести URL+токен внешней; поле домена sub-page; выбор бандла (Ф1).
- **`rw/PanelDashboard.tsx`**: список sub-page (Ф3) + кнопка «Привязать к панели» (для отвязанных/после
  установки) → `POST /api/panel/subpage/connect`, стрим в модалке.
- verify: `tsc`, preview: загрузить архив → выбрать при установке sub-page → привязать к панели.

## РАЗВЕДКА ВЫПОЛНЕНА (2026-07-19) — факты для реализации

- **R1 (кастомный фронт-бандл):** образ `remnawave/subscription-page` = NestJS-бэкенд (`pm2-runtime`), фронт
  **вшит в образ** по пути **`/opt/app/frontend/`** (index.html + assets/). Официальная точка кастомизации ТОЛЬКО
  одна — `/opt/app/frontend/assets/app-config.json` (брендинг/список приложений, «Subpage Builder»). Полной
  замены фронта env-переменной НЕТ. **Наш путь:** volume-mount своего бандла поверх статики —
  `volumes: - /opt/remnawave-subpage/frontend:/opt/app/frontend` (неофициально, но работает — чистая статика;
  бандл должен содержать `index.html` + `assets/` + опц. `assets/app-config.json`). Дефолтный compose
  volume-mount'ов НЕ имеет — добавляем свой.
- **env (полный список):** `APP_PORT=3010`, `REMNAWAVE_PANEL_URL`, `REMNAWAVE_API_TOKEN` (обязателен),
  `CUSTOM_SUB_PREFIX`, `TRUST_PROXY=1`, `CADDY_AUTH_API_TOKEN`, `CLOUDFLARE_ZERO_TRUST_CLIENT_ID/SECRET`,
  `MARZBAN_LEGACY_*`. Брендинг — через `app-config.json`, НЕ через env.
- **R2 (создание токена) — ВАЖНЫЙ БЛОКЕР:** `POST /api/tokens` СУЩЕСТВУЕТ (`{name(2-30), expiresInDays(≥1),
  scopes=["*"]}` → ответ содержит `token` в открытом виде ОДИН раз). НО контроллер **запрещён для API-ключа —
  только admin-JWT** («can only be used with an admin JWT-token»). Значит авто-создать токен existing-API-ключом
  НЕЛЬЗЯ. **Следствие для дизайна:** для авто-создания токена node-assistant должен держать логин/пароль
  админа панели (`POST /api/auth/login` → JWT) — ИЛИ (проще/безопаснее) оператор создаёт токен в панели вручную
  и вставляет его. **Рекомендация:** дефолт — ручная вставка токена; авто-создание — опционально, за admin-логином.
- **R3 (SUB_PUBLIC_DOMAIN):** подтверждено — `SUB_PUBLIC_DOMAIN` в `/opt/remnawave/.env` панели (required),
  формат `yoursubdomain.com/api/sub`. Публичный роут подписки — `/api/sub/{shortUuid}`.
- Источники: github.com/remnawave/subscription-page (Dockerfile/.env.sample), docs.rw/install/subscription-page/*,
  локальный api-1.json (OpenAPI v2.8.0, `/api/tokens`).

## Критерии готовности плана D

- Sub-page = бандл (zip), безопасная распаковка, монтирование кастомного фронта в контейнер.
- Подключение sub-page к панели из `panel_jobs` или внешней (токен + `REMNAWAVE_PANEL_URL` + `SUB_PUBLIC_DOMAIN`),
  в т.ч. «привязать после установки».
- Несколько sub-page на одну панель.
- Загрузка/выбор архивов в UI. `pytest` (test_subpages/test_panel_deploy), `tsc`, `docker compose config`, preview.
- **Разведка R1–R3 выполнена и зафиксирована в CLAUDE.md §7d.**
