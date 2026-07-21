# Волна 5 · План D — Пользовательские конфиги: шаблоны + привязка Xray-редактора

> Раздел **«Пользовательские конфиги»** — per-account хранилище пользовательских ШАБЛОНОВ конфигов по типам
> клиента (XRAY_JSON, MIHOMO, CLASH, SINGBOX, STASH, XRAY_BASE64), смоделированное по образцу
> Remnawave **subscription-templates** (см. РАЗВЕДКУ R4). Ключевая фича — **привязка нашего существующего
> Xray-редактора** (`frontend/src/components/profiles/*`, форк bropines/xray-config-ui-editor) к шаблонам
> типа `XRAY_JSON`: открыть шаблон в редакторе → сохранить обратно в шаблон. Закладываем общий каркас
> «тип шаблона → редактор» (mihomo-редактор придёт в **Плане E** `2026-07-21-wave5-e-mihomo-editor.md`).
> Затрагивает: `backend/app/services/config_templates_store.py` (новый), `backend/app/api/config_templates.py`
> (новый), `backend/app/models/config_templates.py` (новый), доп. методы в
> `backend/app/services/remnawave_client.py` (subscription-templates), `frontend/src/components/configs/*`
> (новый раздел), реюз `frontend/src/components/profiles/*` (редактор). НЕ трогает 14-шаговый деплой-пайплайн.

## Контекст (как есть)

- **Remnawave client** (`services/remnawave_client.py:116-160`) умеет ТОЛЬКО config-profiles:
  `create_config_profile`/`list_config_profiles`/`get_config_profile`/`update_config_profile`. Методов
  `subscription-templates` (list/get/create/update/delete/reorder) **нет вообще** — их надо добавить.
  Клиент инстанцируется из настроек аккаунта: `RemnavaveClient(cfg.panel_url, cfg.api_token)` через хелпер
  `_client()` (образец — `api/traffic_rules.py:13-18`, кидает 400 «Remnawave не настроен» если не сконфигурен).
- **Наши «Шаблоны»** (`components/Templates.tsx` + `api/templates` + `storage.load/save_templates` →
  `accounts/<id>/templates.json`) — это `Template{id,name,config:str,is_default,host_template_ids?}`, где
  `config` — сырая строка Xray-JSON с плейсхолдерами `$domain`/`$xhttp_path`/`$name` (подставляются на
  бэкенде в `step_create_node`/`_subst_host_vars`). Редактируется через `JsonEditor` (умная textarea) +
  `JSON.parse`-гейт. Это ОТДЕЛЬНАЯ сущность от нового раздела — их НЕ сливаем (привязку профили↔шаблоны уже
  пробовали и откатили — CLAUDE.md §9c, коммит 9bf2f20).
- **Xray-редактор** (`components/profiles/*`): богатый визуальный редактор. `store/configStore.ts` — Zustand+Immer,
  персист ручной per-account в `localStorage['xray_profile_<accountId>']` (`storageKey`/`persist`/`hydrate`),
  ключ через `getActiveId()` из `auth/store`. `JsonEditor.tsx` — переиспользуемый CodeMirror6+ajv (props
  `{value,onChange,readOnly?,schemaMode?}`, **уже** импортирован в `Templates.tsx:4`). Модалки
  `GeneratorsModal`/`ItemModal`/`SectionJsonModal`/`DiagnosticsPanel` (`collectDiagnostics(config)→{rows,blockers}`).
  **Синк браузер→панель — заглушка** (TODO, backend-роут не построен; `Profiles.tsx` шапка-коммент).
- **Per-account сторы** — эталон `services/hostings_store.py` (атомарная запись temp+`os.replace`, `threading.Lock`
  на read-modify-write, `MAX_*` лимит, `account_id: Optional` + `current_account` ContextVar-фолбэк) и
  `services/storage.py` (простые load/save по `accounts/<id>/<name>.json`). CRUD-роутер под `require_account`
  регистрируется в `main.py` списком `_auth` (сейчас 28 роутеров, строки 99-126).
- **Frontend nav** (`Sidebar.tsx`): `Tab`-юнион + группы `NAV_MAIN`/`STATS_TABS`/`RW_TABS`/`HOSTINGS_TABS`/…
  «Профили» (`rw-profiles`) уже в `NAV_MAIN` после «Шаблонов». Роутинг вкладок — `App.tsx`.
- **auth/store.ts:101-109** — per-account key-хелперы (`deployJobsKey`/`panelJobsKey`/`tabKey`); сюда же логично
  добавить хелпер для локального ключа редактора шаблона (если пойдём путём отдельного localStorage-инстанса).

## Развилки (закреплены)

- **Хранилище — ЛОКАЛЬНОЕ, per-account** (`accounts/<id>/config_templates.json`, как `hostings.json`), НЕ прямой
  прокси в Remnawave. Причина: раздел работает и **без** сконфигуренной панели; секретов нет → Fernet-волт не нужен.
  Remnawave subscription-templates подключаем как **опциональный экспорт/импорт** (кнопки «Отправить в панель» /
  «Импортировать из панели»), гейтящийся на наличие `panel_url`+`api_token`. В фоне не переспрашивать: панель не
  настроена → кнопки панели задизейблены с тултипом, локальный CRUD работает всегда.
- **Типы шаблонов — ровно 6** (зеркалим Remnawave enum `templateType`): `xray-json`, `xray-base64`, `mihomo`,
  `stash`, `clash`, `singbox`. Роутинг контента по ядру: JSON-ядра (`xray-json`/`singbox`/`xray-base64`) хранят
  контент как **JSON-object** (`templateJson`); YAML-ядра (`mihomo`/`clash`/`stash`) — как **строку YAML**
  (в Remnawave это `encodedTemplateYaml` = base64(YAML); base64 кодируем/декодируем на бэкенде при экспорте/импорте,
  локально храним человекочитаемый YAML-текст).
- **Каркас «тип → редактор»** (frontend `EDITORS: Record<TemplateKind, EditorComponent>`): в этом плане реализуем
  ТОЛЬКО `xray-json` → встроенный Profiles-редактор; остальные 5 типов → generic raw-редактор (`JsonEditor` для
  JSON-ядер, plain-textarea/YAML-подсветка для YAML-ядер). Mihomo-редактор — заглушка «редактор появится в Плане E».
- **Xray-редактор для шаблона — STATELESS-инстанс** (НЕ трогать глобальный `configStore` с ключом
  `xray_profile_<acc>`, иначе перезапишется черновик «Профилей»). Передаём `config`/`onChange` как props;
  переиспользуем `JsonEditor`+`GeneratorsModal`+`ItemModal`+`DiagnosticsPanel` в контролируемом режиме. Round-trip
  строка→объект→строка НЕ должен убивать плейсхолдеры `$domain`/`$xhttp_path`/`$name` (ajv их пропускает как строки).
- **Диагностика-гейт**: сохранение xray-json шаблона гейтится на `collectDiagnostics(config).blockers === 0`
  (critical; enum-нарушения — warning, не блокируют), как в `Profiles.tsx`.
- **Имя шаблона** валидируем локально мягко; при экспорте в панель санитайзим под Remnawave `^[A-Za-z0-9_\s-]+$`
  (2–255 для sub-templates) — как `create_config_profile` уже делает для profile-name.

## Стратегия

Ф1 (backend: локальный стор + модель + CRUD-роутер) → Ф2 (backend: методы subscription-templates в клиенте +
экспорт/импорт-эндпоинты) → Ф3 (frontend: раздел «Пользовательские конфиги» + каркас редакторов + привязка
Xray-редактора к xray-json).

---

### Ф1 — Backend: локальное хранилище шаблонов + CRUD → verify: pytest + py_compile

- `models/config_templates.py` (новый): `TemplateKind = Literal["xray-json","xray-base64","mihomo","stash",
  "clash","singbox"]`; `ConfigTemplateBody{name:str, kind:TemplateKind, content_json:Optional[dict]=None,
  content_yaml:Optional[str]=None, note:Optional[str]=None}`. Валидатор: JSON-ядра требуют `content_json`
  (или пусто), YAML-ядра — `content_yaml`; взаимоисключающе (не оба). Имя 1–255, непустое после `strip`.
- `services/config_templates_store.py` (новый, калька `hostings_store.py`): `accounts/<id>/config_templates.json`,
  атомарная запись (temp+`os.replace`), `_LOCK`, `MAX_TEMPLATES` (напр. 200), `list/add/update/delete`
  (`account_id: Optional` + ContextVar-фолбэк, `id=uuid4().hex[:12]`, `created_at`). Хранит `view_position`
  (int) для будущего reorder — присваивать при add (макс+1).
- `api/config_templates.py` (новый): `router = APIRouter(prefix="/api/config-templates")`, под `require_account`:
  `GET ""` (список, отсортирован по `view_position`), `POST ""` (201), `PUT /{id}`, `DELETE /{id}` (204),
  опц. `POST /reorder` (список id → пересчёт `view_position`). Зарегистрировать в `main.py` в списке `_auth`.
- verify: `backend/tests/test_config_templates.py` — CRUD + per-account изоляция + валидатор (JSON-vs-YAML по kind,
  лимит, пустое имя → 422). `python -m py_compile` изменённых файлов; `pytest`.

---

### Ф2 — Backend: subscription-templates в клиенте + экспорт/импорт → verify: pytest

- `remnawave_client.py` (доп. методы, ветка `# ── Subscription templates ──`):
  - `list_subscription_templates()` → `GET /api/subscription-templates` → `response.templates`.
  - `get_subscription_template(uuid)` → `GET /api/subscription-templates/{uuid}` (с контентом).
  - `create_subscription_template(name, template_type)` → `POST /api/subscription-templates {name, templateType}`
    (создаёт **пустой** — контент не задаёт; вернуть `uuid`). Санитайз `name` под `^[A-Za-z0-9_\s-]+$`, 2–255.
  - `update_subscription_template(uuid, *, template_json=None, encoded_template_yaml=None)` →
    `PATCH /api/subscription-templates {uuid, templateJson?|encodedTemplateYaml?}` (пишет контент).
  - `delete_subscription_template(uuid)` → `DELETE /api/subscription-templates/{uuid}`.
  - опц. `reorder_subscription_templates(uuids)`.
  - Все ответы — `_unwrap` из конверта `{response: …}`. `templateType` — UPPERCASE enum
    (`xray-json`→`XRAY_JSON`, `xray-base64`→`XRAY_BASE64`, `mihomo`→`MIHOMO`, `stash`→`STASH`, `clash`→`CLASH`,
    `singbox`→`SINGBOX`) — маппер local-kind→panel-enum.
- `api/config_templates.py` (доп. эндпоинты, `_client()`-хелпер как в `traffic_rules.py`, 400 если панель не
  настроена):
  - `POST /{id}/export` — **двухшаговый create** (POST пустой шаблон нужного `templateType` → PATCH контент):
    JSON-ядра → `templateJson`; YAML-ядра → `encodedTemplateYaml = base64(content_yaml)`. Вернуть panel-`uuid`.
  - `GET /import/panel` — список sub-templates из панели (для выбора); `POST /import/panel/{uuid}` — забрать
    один шаблон панели → создать локальный (декод: `templateJson`→`content_json`; `encodedTemplateYaml`→base64-decode
    →`content_yaml`; `templateType`→local-kind).
- verify: `test_config_templates.py` доп. кейсы — маппер kind↔enum, base64 round-trip YAML, двухшаговый export
  (мок клиента), «Remnawave не настроен»→400. `pytest`.

---

### Ф3 — Frontend: раздел + каркас редакторов + привязка Xray → verify: tsc + preview

- **Nav** (`Sidebar.tsx`): новый `Tab` `"configs"` (или подгруппа), пункт «Пользовательские конфиги» в `NAV_MAIN`
  рядом с «Шаблонами»/«Профилями» (Icon напр. `FileJson`/`Files`). Роут в `App.tsx`; активная вкладка персистится
  штатно (`tabKey`).
- `components/configs/api.ts` — типизированный клиент `/api/config-templates` (list/create/update/delete/reorder +
  export/import). Ошибки — как в других разделах (без эха secret-полей; тут секретов нет, но паттерн общий).
- `components/configs/ConfigTemplates.tsx` — раздел: список шаблонов **сгруппирован по `kind`** (6 категорий, как
  каталог Remnawave; несколько шаблонов на тип), кнопка «Создать» с выбором типа, per-row edit/delete, кнопки
  панели «Отправить в панель»/«Импортировать из панели» (задизейблены без Remnawave, тултип). Тема — CSS-var токены
  (skin×mode), без хардкод-цветов; иконки `lucide-react`.
- `components/configs/EditorRegistry.tsx` — каркас `EDITORS: Record<TemplateKind, EditorComp>`:
  - `xray-json` → `XrayTemplateEditor` (см. ниже).
  - `singbox`/`xray-base64` → generic `JsonTemplateEditor` (реюз `JsonEditor` в контролируемом режиме +
    `JSON.parse`-гейт).
  - `mihomo`/`clash`/`stash` → generic `YamlTemplateEditor` (plain textarea/CodeMirror-YAML, без структурного
    редактора). Для `mihomo` показать плашку «Визуальный редактор — в Плане E» (задел под
    `2026-07-21-wave5-e-mihomo-editor.md`).
- `components/configs/XrayTemplateEditor.tsx` — **привязка Xray-редактора БЕЗ глобального `configStore`**:
  локальный `useState<XrayConfig>` инициализируется из `template.content_json`; переиспользуем `JsonEditor`
  (schema-режим xray), `GeneratorsModal` (ключи/UUID/импорт ссылок — но подсветить, что для шаблона панель
  перезапишет прокси-outbounds: полезны блоки dns/routing/inbounds), `ItemModal`, `DiagnosticsPanel`
  (`collectDiagnostics`; `blockers>0` блокирует «Сохранить»). Сохранение → `content_json` шаблона. Плейсхолдеры
  `$domain`/`$xhttp_path`/`$name` сохраняются как строковые значения (round-trip не нормализует их).
  ⚠️ НЕ импортировать `useConfigStore` — иначе перезапишется черновик «Профилей».
- verify: `tsc` (в docker-билде фронта); `preview` — создать xray-json шаблон, открыть в редакторе,
  сгенерировать ключ/добавить inbound, сохранить, перечитать; создать mihomo (YAML) шаблон; проверить
  экспорт-в-панель кнопку (задизейблена без Remnawave). Юнит: `ConfigTemplates.test.tsx` (список/группировка/
  валидация/каркас редакторов).

## РАЗВЕДКА (факты) — Remnawave config-profiles & subscription-templates (v2.8.0)

Источники: `api-1.json` (OpenAPI 3.0 «Remnawave API v2.8.0», корень репо); `remnawave/templates`; офиц. доки;
`services/remnawave_client.py:116-160` (наша текущая интеграция).

- **Две РАЗНЫЕ сущности.** *Config-profile* = сырой Xray-конфиг, раздаваемый ядром на ноде (у нас уже есть 4
  метода). *Subscription-template* = скелет рендера подписки под клиент — именно его моделирует новый раздел.
- **Subscription-templates эндпоинты** (конверт `{response:…}`): `GET /api/subscription-templates`
  (`{total,templates[]}`), `GET /api/subscription-templates/{uuid}` (с контентом),
  `POST /api/subscription-templates {name*, templateType*}` (создаёт **ПУСТОЙ**),
  `PATCH /api/subscription-templates {uuid*, name?, templateJson?, encodedTemplateYaml?}` (**пишет контент**),
  `DELETE /api/subscription-templates/{uuid}`, `POST /api/subscription-templates/actions/reorder`.
- **Двухшаговый create.** POST задаёт только `{name, templateType}` → нужен второй PATCH с контентом. Один вызов
  контент НЕ сохраняет.
- **`templateType` enum — ровно 6:** `XRAY_JSON · XRAY_BASE64 · MIHOMO · STASH · CLASH · SINGBOX`.
- **Контент в ДВУХ полях по ядру:** `templateJson` (JSON-object) — для JSON-ядер `XRAY_JSON`/`SINGBOX`/
  `XRAY_BASE64`; `encodedTemplateYaml` (**base64-строка YAML**) — для YAML-ядер `MIHOMO`/`CLASH`/`STASH`.
- **Форма шаблона:** `uuid, viewPosition, name, templateType, templateJson(object|null),
  encodedTemplateYaml(string|null)`. `name` 2–255, `^[A-Za-z0-9_\s-]+$`.
- **Дефолт xray-json (репо `remnawave/templates`, `remnawave-default/subscription-templates/xray-json.json`):**
  топ-ключи `dns/routing/inbounds/outbounds` (только `direct`/freedom + `block`/blackhole). **Видимого
  плейсхолдера нет** — панель на рендере САМА инъектит прокси-outbounds пользователя + routing к его хостам.
  Т.е. шаблон = скелет dns/routing/inbounds, а не полный конфиг ноды. Вывод для UI: для режима «шаблон» полезны
  блоки dns/routing/inbounds редактора; импорт share-ссылок (прокси-outbounds) панель перезапишет.
- **v2.2.0+**: несколько шаблонов на ядро; какой отдать юзеру — решают External Squads / Routing Rules (панельная
  логика, не наша). `viewPosition`/reorder поддержать в UI.
- **Смежное (не путать):** `GET/PATCH /api/subscription-settings` (глобальные настройки подписки) и
  `/api/subscription-page-configs` (веб-страница подписки, ближе к нашему каталогу Orion §7d) — НЕ шаблоны клиента.
- **Наш пробел:** методов subscription-templates в `remnawave_client.py` нет — добавляем в Ф2.

Sources: `api-1.json` (OpenAPI v2.8.0, локально); `remnawave/templates` —
`subscription-templates-list.json` / `remnawave-default/subscription-templates/xray-json.json`
(github.com/remnawave/templates); Templates | Remnawave Docs (docs.rw/docs/learn-en/templates/);
`services/remnawave_client.py:116-160`.

## Критерии готовности плана D

- Локальный per-account стор шаблонов (`config_templates.json`) + CRUD-роутер под `require_account` в `main.py`;
  6 типов клиента, роутинг контента JSON-vs-YAML по ядру. `pytest test_config_templates` зелёный.
- Клиент Remnawave умеет list/get/create(2-шага)/update/delete subscription-templates; экспорт локального шаблона в
  панель (двухшаговый) и импорт из панели с base64-декодом YAML; гейт «Remnawave не настроен»→400.
- Раздел «Пользовательские конфиги» во фронте: группировка по типу, CRUD, каркас `тип→редактор`; **xray-json
  открывается в нашем Xray-редакторе (stateless-инстанс, НЕ трогает `xray_profile_<acc>`) и сохраняется обратно**;
  плейсхолдеры переживают round-trip; диагностика-гейт на сохранение. `mihomo` — плашка-задел под План E.
- `python -m py_compile` + `pytest` + `tsc` (docker-билд) + preview-smoke (создать/редактировать xray-json и yaml
  шаблон, экспорт-кнопка гейтится). CLAUDE.md обновлён при реализации (§ про раздел + новые роутеры/сторы).
