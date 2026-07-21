# Волна 5 · План K — Селектор панелей Remnawave + смена главной

> **Идея 13.** Сейчас на аккаунт ровно ОДНА панель Remnawave — плоский `RemnavaveConfig{panel_url, api_token,
> default_internal_squad_ids, default_external_squad_ids}` на `AppSettings.remnawave` (`backend/app/models/settings.py:6`),
> персист в `accounts/<id>/settings.json`. Клиент `RemnavaveClient` stateless, конструируется per-call из пары
> `(base_url, token)` — сменить «главную» = передать другую пару. Задача: сделать **реестр нескольких панелей**
> per-account + указатель на **активную**, при этом НЕ убирая ручной ввод url/token (он становится «кастомной»
> записью реестра). Переключение главной доступно из Настроек (Settings→«Remnawave») и из бокового меню группы
> «Remnawave». Затрагивает: `models/settings.py` (модель), `api/settings.py` (CRUD панелей + активация + резолвер),
> `frontend/src/components/Settings.tsx` (`RemnavaveTab`), `frontend/src/components/Sidebar.tsx` (`Tab`/`RW_TABS`) и
> опц. топбар-дропдаун по образцу `frontend/src/auth/AccountMenu`. Переиспользует: `storage.load/save_settings`,
> `RemnavaveClient`, Fernet-волт (ключ = SHA-256 `settings.encryption_key`, как rules/mcp/netbird).

## Контекст (как есть)

- **Единый конфиг:** `AppSettings.remnawave: RemnavaveConfig = RemnavaveConfig()` (`models/settings.py:87`) —
  `panel_url`/`api_token`/`default_internal_squad_ids`/`default_external_squad_ids`. Списка/id/«active» нет.
- **Запись:** `POST /api/settings/remnawave` (`api/settings.py:28`) принимает `RemnavaveConfig` целиком → `settings.remnawave`.
  `POST /api/settings/remnawave/check` (`:83`) уже умеет принимать необязательное тело `{panel_url, api_token}` для
  теста несохранённых значений (фолбэк на сохранённый конфиг) — единственная ручка с url/token из запроса.
- **Клиент:** `RemnavaveClient(base_url, token)` (`services/remnawave_client.py:37`) полностью stateless, per-call,
  без синглтона/кэша. Единый паттерн во всём коде:
  `cfg = AppSettings(**storage.load_settings(<acct?>)).remnawave; RemnavaveClient(cfg.panel_url, cfg.api_token)`.
- **Blast-radius — 13 точек читают `.remnawave`** (сверено grep’ом):
  - *Группа A (ContextVar-scoped, request-time):* `api/settings.py:88,109` (`_client()` + check → squads/plugins-прокси),
    `api/traffic_rules.py:15`, `api/infra_billing.py:31`, `api/node_ops.py:313`, `services/pipeline.py:182`
    (env Node Accelerator), `:1920` (step_create_node — привязка ноды к панели), `:2080` (get_node_secret_key),
    `:2116` (step_create_hosts).
  - *Группа B (фоновые воркеры, явный `account_id`):* `api/user_stats.py:50` (коллектор метрик нод),
    `services/ai_agent.py:81` (инструменты ИИ), `services/rule_actions.py:61` (действия rules), `services/mcp_server.py:185`
    (env MCP-контейнера).
  - *Группа C (уже per-request explicit — образец):* `api/migrate.py:104` `_remnawave_client(url, token)` из тела запроса
    + SSRF-гард `net_guard.is_safe_url`.
  - *Группа D (readiness-флаги):* `api/mcp.py:36` `bool(s.remnawave.panel_url and s.remnawave.api_token)`,
    `Settings.tsx:140` (грузит squads если `panel_url && api_token`).
- **Развёрнутые панели трекаются только клиентски** — `panel_jobs_<accountId>` в localStorage
  (`auth/store.ts:104` `panelJobsKey`, модель `PanelJobSummary` в `rw/PanelDashboard.tsx:14`): `savedForm`
  (`PanelDeployRequest`) содержит `ip/ssh_*/panel_domain/sub_domain`, но **НЕ содержит API-токена** — токен создаётся
  в панели вручную ПОСЛЕ деплоя (`panel_pipeline.py` пишет `REMNAWAVE_API_TOKEN=""`). ⇒ из panel_jobs можно
  предложить только `panel_url` (кандидат из `panel_domain`), токен оператор вводит руками.
- **UI:** Settings→«Remnawave» (`Settings.tsx:116` `RemnavaveTab`) = поля `panel_url`/`api_token` + squads-MultiSelect +
  «Проверить соединение» + Сохранить. Сайдбар-группа «Remnawave» (`Sidebar.tsx:43` `RW_TABS`, 5 табов) + `Tab`-union
  (`Sidebar.tsx:10`). Топбар-дропдаун-прецедент — `frontend/src/auth/AccountMenu`.

## Развилки (закреплены)

- **Модель — реестр + указатель, `.remnawave` остаётся вычисляемым представлением активной панели.** Самый дешёвый
  путь без переписывания 13 сайтов: новая модель хранит `panels: list[PanelEntry]` + `active_panel_id`, а
  `AppSettings.remnawave` продолжает отдавать активную запись (через валидатор/резолвер). Группы A/B/D кодово НЕ
  трогаются — меняется только слой хранения. Осознанно правим: (1) запись (`POST /settings/remnawave` → в список),
  (2) новые CRUD-ручки панелей, (3) фоновые воркеры группы B (решить activ vs. все — см. ниже), (4) миграцию legacy.
- **Токен панели хранить в открытую в settings.json — как сейчас.** Секретность НЕ ужесточаем в этом плане (текущий
  `api_token` в settings.json plaintext — статус-кво проекта для Remnawave-кредов). Fernet-волт НЕ вводим для панелей,
  чтобы не расширять скоуп; при желании — отдельный follow-up. (⚠️ упомянуть в открытых вопросах.)
- **Активная панель — глобальный per-account указатель, влияет на ВСЕ ручки сразу** (squads/plugins/pipeline/
  infra-billing/traffic-rules/node_ops). Пер-деплойного выбора панели в этом плане НЕ вводим (деплой-нода привязывается
  к **активной** панели) — иначе трогаем `DeployRequest` + 14-шаговый инвариант. Это отдельный follow-up.
- **Фоновые воркеры группы B (коллектор/rules/ai/mcp) работают по АКТИВНОЙ панели.** Обход всех панелей — избыточен и
  ломает семантику метрик; активная = единый смысл «главная». Резолвер отдаёт активную → воркеры кодово не меняются.
- **Ручной ввод = «кастомная» запись реестра.** Сохранение нового url/token создаёт/обновляет запись `kind:"custom"`;
  запись из panel_jobs → `kind:"deployed"` (префилл url, токен вводится вручную). Ручной ввод НИКОГДА не убираем.
- **Переключатель в двух местах:** селектор в `RemnavaveTab` (полный CRUD + «Сделать главной») и быстрый переключатель
  в сайдбаре группы «Remnawave» (компактный дропдаун — только смена активной).
- **В фоне не переспрашивать:** дефолт активной — первая/единственная запись; пустой реестр → поведение как сейчас
  (Remnawave «не настроен»).

## Стратегия

Ф1 (backend: модель реестра + резолвер `.remnawave` + миграция legacy) → Ф2 (backend: CRUD-ручки панелей + активация)
→ Ф3 (frontend: селектор в Settings + быстрый переключатель в RW-сайдбаре).

---

### Ф1 — Модель реестра + резолвер активной панели + миграция → verify: pytest + py_compile

`backend/app/models/settings.py`:
- Новый `PanelEntry(BaseModel)`: `id: str` (uuid), `name: str`, `kind: "custom"|"deployed" = "custom"`,
  `panel_url: str = ""`, `api_token: str = ""`, `default_internal_squad_ids: list[str] = []`,
  `default_external_squad_ids: list[str] = []` (те же поля, что `RemnavaveConfig`, + id/name/kind).
- Новый контейнер `RemnawaveRegistry(BaseModel)`: `panels: list[PanelEntry] = []`, `active_panel_id: str = ""`.
  Хранится на `AppSettings` как **новое** поле `remnawave_registry: RemnawaveRegistry = RemnawaveRegistry()`.
- **`AppSettings.remnawave` остаётся `RemnavaveConfig`** — но становится **вычисляемым представлением активной панели**.
  Реализовать через `@model_validator(mode="after")` на `AppSettings`: если есть `remnawave_registry.panels` → выбрать
  активную (`active_panel_id`, иначе первая) и спроецировать её поля в `self.remnawave` (panel_url/api_token/squads);
  если реестр пуст, но legacy `remnawave` заполнен → **мигрировать**: создать `PanelEntry` из legacy `remnawave`
  (`kind:"custom"`, `name:"Основная"`, новый uuid), положить в `panels`, выставить `active_panel_id`. Пустой legacy +
  пустой реестр → оба пустые (как сейчас). ⇒ группы A/B/D читают `.remnawave` без изменений, всегда получая активную.
- **Инвариант резолвинга** держать в ОДНОМ месте (валидатор), чтобы `AppSettings(**raw)` в любом из 13 сайтов давал
  консистентную активную панель. Дубли `active_panel_id`/несуществующий id → фолбэк на первую запись (не падать).
- verify: `backend/tests/test_settings_panels.py` (новый) — (a) legacy single-config → после `AppSettings(**raw)`
  появляется 1 запись в реестре + `remnawave` = она же; (b) реестр с 2 панелями + `active_panel_id=второй` → `.remnawave`
  = вторая; (c) битый `active_panel_id` → фолбэк на первую; (d) пустой → пустой `.remnawave`. `python -m py_compile`.

---

### Ф2 — CRUD-ручки панелей + активация → verify: pytest

`backend/app/api/settings.py` (под `require_account`, как весь роутер):
- `GET /api/settings/remnawave/panels` → `{panels: [...], active_panel_id}` из `AppSettings(**load_settings()).remnawave_registry`.
  (⚠️ `api_token` возвращать как есть — статус-кво plaintext; либо, если решим маскировать в списке, отдавать `has_token`
  + хвост — зафиксировать в развилке. Дефолт этого плана: как есть, симметрично текущему `GET /api/settings`.)
- `POST /api/settings/remnawave/panels` (`PanelEntryBody` без id) → создать запись (uuid), если реестр был пуст —
  сделать её активной; вернуть созданную.
- `PUT /api/settings/remnawave/panels/{id}` (`PanelEntryBody`) → обновить поля записи (url/token/name/squads); 404 если нет.
- `DELETE /api/settings/remnawave/panels/{id}` → удалить; если удаляли активную → активной становится первая из
  оставшихся (или пусто); 404 если нет.
- `POST /api/settings/remnawave/panels/{id}/activate` → выставить `active_panel_id=id`; 404 если нет. Возврат
  `{ok, active_panel_id}`.
- **Совместимость `POST /api/settings/remnawave`** (существующая ручка, `RemnavaveConfig`): сохранить как «редактор
  активной панели» — записывать пришедший `RemnavaveConfig` в **активную** запись реестра (create-if-empty). Так старый
  фронт/тесты не ломаются, а `Settings.tsx` может продолжать слать `RemnavaveConfig`.
- **`POST /api/settings/remnawave/check`** — оставить как есть (тест произвольных url/token из тела); опц. добавить
  `panel_id?` в тело, чтобы «Проверить» конкретную запись реестра без ручного ввода (фолбэк-цепочка: тело > запись по id
  > активная).
- Все записи settings атомарны через `storage.save_settings(settings.model_dump())` (валидатор Ф1 нормализует реестр
  при каждом load).
- verify: `test_settings_panels.py` — CRUD-цикл (create→list→activate→update→delete), удаление активной → переезд
  указателя, `POST /settings/remnawave` пишет в активную, изоляция per-account (два аккаунта не видят панели друг друга,
  через `current_account` ContextVar). `python -m py_compile`.

---

### Ф3 — Frontend: селектор в Settings + быстрый переключатель в RW-сайдбаре → verify: tsc + preview

`frontend/src/components/Settings.tsx` (`RemnavaveTab`):
- Над полями `panel_url`/`api_token` — **селектор панелей** (список записей реестра из `GET /settings/remnawave/panels`):
  каждая строка = имя + kind-бейдж (custom/deployed) + отметка активной; действия «Сделать главной»
  (`POST .../{id}/activate`), «Редактировать» (загружает поля в форму), «Удалить» (`DELETE`).
- **Ручной ввод сохраняется** — форма url/token/squads/name = редактор выбранной/новой записи; «Сохранить»
  создаёт (`POST .../panels`) или обновляет (`PUT .../panels/{id}`). «Проверить соединение» — как сейчас.
- Кнопка **«Из развёрнутых»**: читает `panel_jobs_<accountId>` (клиентски, `panelJobsKey()`), предлагает `panel_domain`
  успешных панелей как кандидаты `panel_url` (`kind:"deployed"`), токен оператор вводит вручную (подсказка «токен
  создаётся в самой панели»).
- **Инвалидация кэша squads/plugins при смене активной** — после activate refetch `/api/remnawave/squads/*` +
  `node-plugins` (в `Settings.tsx` и, при монтировании, в `DeployForm`/`Templates`, т.к. backend-резолвинг уже сменит
  панель в одном месте `_client()`).

`frontend/src/components/Sidebar.tsx`:
- Быстрый переключатель активной панели в шапке группы «Remnawave» — компактный `<select>`/дропдаун (список реестра,
  текущая = активная; выбор → `POST .../{id}/activate` → обновить кэш). CSP-self-contained, тема через var-токены
  (без хардкода цветов). Если панелей <2 — не показывать.
- Опц. альтернатива (зафиксировать в UI-решении): глобальный дропдаун в топбаре по образцу
  `frontend/src/auth/AccountMenu` (влияет на все ручки). Дефолт плана — переключатель в RW-группе сайдбара; топбар — опц.
- **`Tab`-union и роутинг НЕ обязательно расширять** (селектор живёт в существующем `RemnavaveTab` + сайдбар-дропдаун).
  Если решим сделать отдельный таб «Панели» — добавить `"rw-panels"` в `Tab` (`Sidebar.tsx:10`) + `RW_TABS` + роут в
  `App.tsx` (упомянуть как опцию, не обязательно).
- verify: `tsc` (в docker-билде); preview — добавить 2 панели, переключить активную в Settings и в сайдбаре, убедиться,
  что squads/plugins и деплой едут на выбранную панель; ручной ввод сохраняется; удаление активной переезжает указатель.

## Критерии готовности плана K

- Модель `RemnawaveRegistry{panels[], active_panel_id}` + `PanelEntry`; `AppSettings.remnawave` = вычисляемая активная
  панель (валидатор), legacy single-config автоматически мигрирует в реестр из 1 записи (`kind:"custom"`) при первом
  `AppSettings(**raw)`. 13 сайтов, читающих `.remnawave`, кодово НЕ тронуты — работают на активной панели.
- CRUD-ручки `GET/POST/PUT/DELETE /api/settings/remnawave/panels` + `POST .../{id}/activate` под `require_account`,
  per-account изоляция; `POST /api/settings/remnawave` пишет в активную (обратная совместимость).
- Frontend: селектор в `RemnavaveTab` (полный CRUD + «Сделать главной» + «Из развёрнутых» из panel_jobs, ручной ввод
  сохранён) + быстрый переключатель в RW-группе сайдбара; кэш squads/plugins инвалидируется при смене активной.
- `pytest` (`test_settings_panels.py`: миграция/резолвинг/фолбэк/CRUD/активация/изоляция) + `tsc` + preview +
  ручной smoke (2 панели, переключение из обоих мест, деплой на активную). Обновить CLAUDE.md §1b/§2/§5 при реализации.

## Зависимости и кросс-ссылки

- Сквозная идея 5 (расширять backend API) — реализована: новые CRUD-ручки панелей под `require_account`, тесты в
  `backend/tests/`.
- Соседние планы Волны 5: **H** (`2026-07-21-wave5-h-api-tokens.md`) — API-токены доступа к нашему backend; если появится
  токен-based доступ, GET-панелей может отдавать маскированные токены (согласовать формат). **B**
  (`2026-07-21-wave5-b-neon-motion.md`) — оформление селектора/дропдауна в неон-скине + motion. **L**
  (`2026-07-21-wave5-l-panel-import-export.md`) — импорт/экспорт данных панели (работает поверх активной/выбранной
  записи реестра).

## Открытые вопросы (в бэклог)

- **Секретность токена панели:** оставляем plaintext в settings.json (статус-кво) или переносим `api_token` записей в
  Fernet-волт (как netbird/mcp/rules) + маскирование в GET? Дефолт плана — plaintext; ужесточение = отдельный follow-up.
- **Пер-деплойный выбор панели:** сейчас нода привязывается к активной панели. Отдельное поле в `DeployRequest`
  («в какую панель регистрировать») намеренно вне скоупа (трогает 14-шаговый инвариант) — отдельный план при спросе.
- **Отдельный таб «Панели» (`rw-panels`) vs. селектор внутри `RemnavaveTab`** — дефолт: без отдельного таба; решить на
  ревью UI.
- **Топбар-дропдаун (AccountMenu-стиль) vs. сайдбар-переключатель** — дефолт сайдбар; топбар опционально.
