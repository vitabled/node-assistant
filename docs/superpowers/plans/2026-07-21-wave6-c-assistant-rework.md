# Волна 6 · План C — Переделка раздела «Ассистент» + self-host CLIProxyAPI

> Страница «Ассистент» (`frontend/src/components/settings/AiChat.tsx`) сегодня — это ~60% формы настроек
> провайдера + пресеты промптов + чат в коробке `max-h-80`, и она **физически не прокручивается** (единственный
> экран в приложении, не заводящий свой скролл-контейнер). Делаем три вещи: (1) на странице остаётся ТОЛЬКО чат,
> он занимает высоту экрана и прокручивается, композер прибит к низу; (2) настройки провайдера/модели/ключа +
> пресеты промптов уезжают в новую вкладку «Настройки → Ассистент»; (3) список моделей подгружается сам (сейчас
> он намертво загейчен на `gateway == "cliproxy"` и на бэкенде, и на фронте). Отдельно доделываем отложенную
> опц. Ф2 Плана J — **поднимаем CLIProxyAPI своим DooD-контейнером** на `node-assistant-net` (решение
> пользователя), что заодно оживляет мёртвый сегодня флаг `gateway_internal`.
> Затрагивает: `AiChat.tsx` (распил надвое), `Settings.tsx` (новая под-вкладка), `PromptPresets.tsx`,
> `App.tsx` (импорт), `api/ai.py`, `services/ai_agent.py`, `models/settings.py`, новые
> `services/cliproxy_server.py` + `api/cliproxy.py`, `main.py`. Переиспользует: DooD-паттерн `mcp_server.py`,
> Fernet-волт `SHA-256(encryption_key)`, скролл-паттерн `RuleBuilder.tsx`.
> **Ф1–Ф2 (UI + автоподгрузка моделей) отгружаются БЕЗ Ф3–Ф4 (self-host).**

## Контекст (как есть)

- **Страница смонтирована ровно в одном месте:** `App.tsx:240` — `{tab === "assistant" && <AiChat />}`, пункт
  меню — `Sidebar.tsx:44` (`AUTOMATION_TABS`, «Ассистент», иконка `Bot`). В `Settings.tsx` компонент **не
  рендерится вообще**: под-вкладка `mcp` (`Settings.tsx:830`) показывает только `<McpTab/>`.
  **CLAUDE.md:358 («Frontend `settings/AiChat.tsx` (под MCP-вкладкой)») — УСТАРЕЛА, побеждает код**; §9c
  («ИИ-чат вынесен… `assistant` таб в группе Автоматизация») — верна. Правится при реализации.
- **Анатомия файла** (`AiChat.tsx`, 247 строк): заголовок 131–134; блок конфига 137–182 (Шлюз 140–144, Формат
  протокола 148–156, Модель select-или-input 160–169, Base URL 173–174, API-ключ 178–180); тумблер + лимит
  шагов + «Сохранить» 184–202; предупреждение «агент выключен» 204–208; **`<PromptPresets/>` 210**; чат-лог
  213–234; композер 236–244. Стейт разделяется чисто: `cfg/keyInput/saving/models` — конфиг,
  `msgs/input/busy/scrollRef/abortRef` — чат.
- **Почему не скроллится (цепочка прослежена до конца):** `index.css:44` `html,body,#root{height:100%}` +
  `index.css:47` `body{… overflow:hidden}` → документ не прокручивается никогда, каждый экран обязан завести
  свой скроллер. `App.tsx:228` `<main className="ni-main" style={{flex:1,display:flex,flexDirection:"column",
  minHeight:0}}>` и обёртка `Screen` (`App.tsx:58-67`, тот же стиль) — **`overflow` не задают ни там, ни там**
  (это by design). Корень `AiChat` — `<div className="card card-p flex flex-col gap-4">` (`AiChat.tsx:130`):
  ни `flex-1`, ни `overflow-y-auto`, ни `ni-pagebody`. Всё, что не влезло, обрезается `body{overflow:hidden}`
  без единого скроллбара. Все прочие экраны скроллер заводят: `infra/ui.tsx:22-28`, `Settings.tsx:809`,
  `RuleBuilder.tsx:99`+`:113`, `UsersStats.tsx`, `Library.tsx`, `Profiles.tsx`.
- **Чат-лог дополнительно зажат хардкодом** `max-h-80 overflow-y-auto` (`AiChat.tsx:213`, = 320px) с
  автоскроллом в `useEffect` (`AiChat.tsx:47`) — переписка листается в коробке 320px независимо от высоты окна.
- **Мобильный добивает:** `index.css:317` `@media(max-width:820px){.ni-main{padding-bottom:calc(58px + var(--safe-b))!important}}`
  — на телефоне ещё 58px контента уезжает в недостижимую зону. Класс `ni-pagebody` (`index.css:320`) даёт
  мобильные паддинги бесплатно — сейчас его на странице нет.
- **Автоподгрузка моделей загейчена ДВАЖДЫ, обе — по `gateway`:**
  - бэкенд `api/ai.py:107` — `if cfg.gateway != "cliproxy": return {"models": []}`;
  - фронт `AiChat.tsx:49-53` — `useEffect`, стреляющий только при `cfg?.gateway === "cliproxy"`; селектор
    (`AiChat.tsx:160`) рендерится только при `gateway==="cliproxy" && models.length>0`, иначе — ручной инпут.
  При дефолтном `gateway:"none"` список не запрашивается никогда.
- **`ai_agent.list_models`** (`ai_agent.py:218-235`): `GET {base_url}/models`, заголовок **только**
  `Authorization: Bearer {key}`, 20с, `>=400 → []`, парсит `data["data"][].id`, «никогда не бросает».
  **Нет раннего выхода `if not key`** — с пустым ключом уйдёт реальный внешний запрос. Anthropic-путь агента
  использует `x-api-key` + `anthropic-version` (`ai_agent.py:308-310`), т.е. прямой Anthropic на
  `list_models` ответит 401 → тихо `[]`.
- **Баг «список моделей пуст до ремаунта»:** `patchCfg` (`AiChat.tsx:55`) меняет только локальный стейт, а
  эффект (`:49`) завязан на `cfg?.gateway`. Пользователь выбирает CLIProxyAPI → эффект стреляет сразу → на
  сервере ещё `none` → `[]`; после «Сохранить» `setCfg(data)` (`:68`) возвращает то же значение `"cliproxy"` →
  зависимость не изменилась → эффект НЕ перезапускается. `base_url` в зависимостях нет вообще.
- **`POST /api/ai/config` — ПОЛНАЯ ЗАМЕНА секции, не патч** (`api/ai.py:79-99`): `AiConfigBody` имеет дефолт
  для каждого поля, `save_config` пишет их безусловно. Частичный POST молча сбрасывает `base_url`→
  `https://api.openai.com/v1`, `model`→`gpt-4o-mini`, `max_steps`→6, `active_preset_id`→"". Патч-безопасен
  только `api_key` (`api/ai.py:95-96`). Сегодня оба вызывающих шлют объект целиком (`AiChat.tsx:61`,
  `PromptPresets.tsx:41-47` — GET-modify-POST).
- **`PromptPresets`** (`PromptPresets.tsx:17-122`) самодостаточен: свой `card card-p` (`:75`), свой `load()`
  по `/api/ai/prompts` + `/api/ai/config` (`:25-28`), а `setActive` (`:38-49`) делает GET конфига → POST всего
  объекта с новым `active_preset_id`. Переносится одной строкой JSX, но на общей странице с формой конфига
  это гонка «кто записал последним».
- **`gateway_internal` — мёртвое поле.** Объявлено `models/settings.py:107`, читается
  `ai_agent._check_base_url` (`ai_agent.py:208`), но его **НЕТ** в `AiConfigBody` (`api/ai.py:29-52`), в
  `save_config` (`:83-93`), в `_public` (`:59-71`) и во фронтовом типе (`AiChat.tsx:6-15`). Включить его из
  продукта нельзя ничем — только руками в `settings.json` или из юнит-теста
  (`test_ai_gateway.py:23`). ⇒ SSRF-исключение для внутреннего шлюза сегодня недостижимо.
- **`readonly` — тоже мёртвое поле** (`settings.py:104`, `api/ai.py:36`, `_public` `:67`, фронт-тип
  `AiChat.tsx:12`): `ai_agent` его нигде не читает — все 4 инструмента read-only по построению. Крутится
  туда-обратно, UI-контрола нет.
- **`run_agent` жёстко требует ключ:** `ai_agent.py:425-428` — `if not key: yield {"type":"error",…}; return`.
- **`net_guard`** (`net_guard.py:21-46`): http(s) + КАЖДЫЙ резолвнутый IP должен быть публичным; резолв через
  блокирующий `socket.getaddrinfo` (`:26`). Контейнерное имя на `node-assistant-net` резолвится в 172.16/12 →
  приватный → блок. Escape-hatch `ALLOW_PRIVATE_HOSTS=1` (`:18`) — только для тестов.
- **`_INTERNAL_GATEWAY_HOSTS = {"node-installer-cliproxy", "cli-proxy"}`** (`ai_agent.py:200`) — апстримовое
  дефолтное имя контейнера `cli-proxy-api` в набор НЕ входит.
- **Никакого CLIProxyAPI в инфраструктуре нет:** `docker-compose.yml` содержит `backend`, `subs-aggregator`,
  `mcp` (profile `mcp-build`), `monitoring`/`deploy-worker` (profile `split`, План M), `frontend`, сеть
  `app-net` с `name: node-assistant-net` (`docker-compose.yml:157-162`). Сервисов `cli-proxy`/`cliproxy` нет,
  файлов `services/cliproxy_server.py`/`api/cliproxy.py` нет, строка `8317` вне планов не встречается.
- **DooD-эталон** — `services/mcp_server.py`: `_docker()`/`_NO_DOCKER` (`:134-159`), `container_state()`
  (`:162-178`), `start()` c `docker rm -f` → 0600 `--env-file` для секретов (`:211-224`, «не в argv, не в
  `/proc/cmdline`») → `--network` из `XRAY_CHECKER_NETWORK` (`:233-235`) → guard `image.startswith("-")`
  (`:194`), `reachable()` по имени контейнера (`:283-297`), `status()` (`:300-320`). API-эталон —
  `api/mcp.py`: `GET/POST /config` + `GET /status`, Docker-absent → **200 с `warning`**, не 500 (`:74-85`).
- **Тесты, которые упрутся в изменения:** `test_ai_gateway.py:48` утверждает `GET /api/ai/models ==
  {"models": []}` для свежего аккаунта (сегодня — из-за гейта по `gateway`); `AiChat.test.tsx:31` — мок
  `fetch` **бросает на любой неизвестный URL**, знает только `/api/ai/config` и `/api/ai/chat`
  (`/api/ai/prompts` от вложенного `PromptPresets` сегодня падает, но отлов в `PromptPresets.tsx:31`
  проглатывает — сюита проходит случайно); `CONFIG` в тесте (`:5-8`) не содержит `gateway`.

## Развилки (закреплены)

- **UI-переделка полностью независима от self-host.** Ф1 (чат-only + скролл + вкладка настроек) и Ф2
  (автоподгрузка моделей) не трогают ни compose, ни Docker и отгружаются сами по себе. Ф3–Ф4 (self-host
  CLIProxyAPI) можно не делать вовсе — тогда обязателен «минимум честности» из Ф3 (см. фазу).
- **Страница чата НЕ пишет конфиг вообще.** Она делает только `GET /api/ai/config` и использует его read-only
  (`enabled`/`has_key` гейтят композер). Это единственный надёжный способ обойти full-replace-мину
  `api/ai.py:79-99`: writer у секции `ai` остаётся ровно один — форма в Настройках.
- **Пресет промпта становится полем формы, а не отдельным писателем.** `active_preset_id` уже есть в
  `AiConfigBody` (`api/ai.py:37`), поэтому `PromptPresets` переводится в **контролируемый** режим
  (`value`/`onChange` от родителя), а его собственный GET-modify-POST (`PromptPresets.tsx:38-49`) удаляется.
  CRUD пресетов (`/api/ai/prompts`: create/fork/delete/edit) остаётся как есть. Плюс: один POST-путь, гонки
  нет, кода меньше. Минус (принят): выбор пресета применяется по «Сохранить», а не мгновенно.
- **Скролл-паттерн = `RuleBuilder`, не `infra/Page`.** `<div className="flex flex-col h-full min-h-0 ni-pagebody">`
  + фиксированная шапка 44px (`RuleBuilder.tsx:100`) + `flex-1 min-h-0 overflow-y-auto` под лог
  (`RuleBuilder.tsx:113`) + прибитый композер. Хардкод `max-h-80` (`AiChat.tsx:213`) удаляется. `ni-pagebody`
  бесплатно даёт мобильные паддинги (`index.css:320`).
- **`GET /api/ai/models` разгейчивается для ВСЕХ провайдеров**, но с двумя защитами в `list_models`:
  (а) ранний выход `if not key: return []` **до любой сети** — иначе разгейченный эндпоинт начнёт ходить в
  `api.openai.com` из тестовой сюиты и с неконфигуренного аккаунта; (б) заголовок по `provider`
  (`x-api-key` + `anthropic-version` для anthropic, `Bearer` иначе). Контракт «никогда не бросает, `[]` при
  любой ошибке» сохраняется → неверная догадка про чужой `/models` деградирует в ручной ввод, ничего не ломая.
- **Селектор моделей рендерится всегда, когда `models.length > 0`** (не только при `gateway==="cliproxy"`),
  ручной инпут — fallback при пустом списке. Плюс кнопка «Обновить список» рядом (одна строка) — иначе
  единственный способ перезапросить каталог после смены `base_url` неочевиден.
- **Файлы разъезжаются по назначению:** чат → `components/automation/AiChat.tsx` (совпадает с nav-группой,
  `Sidebar.tsx:44`), тест `AiChat.test.tsx` переезжает рядом, импорт в `App.tsx` правится; форма конфига →
  **новый** `components/settings/AiSettingsTab.tsx`, `PromptPresets.tsx` остаётся в `settings/`. Если хочется
  минимума диффа — оставить пути как есть безвредно, но имя `settings/AiChat.tsx` для не-настроек врёт.
- **Self-host CLIProxyAPI — БЕЗ compose-сервиса.** Образ апстрима готовый (`eceasy/cli-proxy-api:latest`), мы
  его не собираем → compose-запись не нужна вовсе: `docker run` из оркестратора, ровно как `xray_checker`
  (у которого тоже нет compose-записи). Compose-запись у `mcp` существует только потому, что его образ
  **собирается** из `./mcp`. Это проще, чем «новый сервис `cli-proxy` по образцу `mcp`» из текста Плана J
  (`wave5-j-opencliproxy.md:95-99`) — фиксируем упрощение.
- **⚠️ КРИТИЧНО (безопасность): `api-keys` у CLIProxyAPI НИКОГДА не пустой.** В апстриме при пустом списке
  ключей менеджер доступа возвращает `nil, nil` и запрос пропускается **без аутентификации вообще**
  (`sdk/access/manager.go`, подтверждено чтением исходника; docs/sdk-access.md дублирует это прозой). Наш
  контейнер стоит на общей `node-assistant-net` рядом с образами, которые мы тянем по `:latest`
  (xray-checker) — открытый шлюз там означает, что любой сосед может жечь OAuth-квоту оператора. Поэтому:
  оркестратор **всегда** генерит случайный клиентский ключ и кладёт его в `api-keys`, порт 8317 **наружу не
  пробрасывается** (`expose`-режим, без `-p`), и это ровно та же логика defence-in-depth, что `AGG_TOKEN` у
  `subs-aggregator` (CLAUDE.md §4b Ф8).
- **`run_agent` НЕ ослабляем** — требование ключа остаётся строгим (`ai_agent.py:425-428`). Разведка
  предлагала разрешить пустой ключ при `gateway=="cliproxy"` (ради апстримового открытого режима
  `api-keys: []`), но наш self-host ключ всегда минтит, а внешний открытый шлюз мы явно не поощряем. Для
  оператора, который всё же указал на открытый внешний шлюз, — подсказка в плейсхолдере поля ключа
  («если у шлюза пустой `api-keys` — введите любую строку, она игнорируется»). Меньше кода, безопаснее.
- **`gateway_internal` не получает пользовательского чекбокса.** Он выставляется ТОЛЬКО серверной стороной
  при включении self-host (вместе с `gateway="cliproxy"`, `base_url=http://node-installer-cliproxy:8317/v1`).
  Чекбокс, ослабляющий SSRF-гард руками, — регрессия безопасности.
- **`_INTERNAL_GATEWAY_HOSTS` дополняется `cli-proxy-api`** (дефолтное имя контейнера апстрима) — страховка
  для оператора, поднявшего апстримовый compose дословно. Своё имя оставляем `node-installer-cliproxy`
  (конвенция `node-installer-mcp`).
- **Форматов протокола по-прежнему два** (`_PROVIDERS = ("openai","anthropic")`, `api/ai.py:25`). Gemini
  (`/v1beta`) и Codex (`/v1/responses`) шлюз тоже отдаёт, но Gemini-апстримы достижимы через OpenAI-формат
  алиасами — третий адаптер не покупает ничего.
- **`readonly` в форму не выносим** — поле мёртвое; оно сохраняется как есть за счёт полного POST. Удаление
  поля — в бэклог «later» (это отдельная чистка, не наша задача).
- **Что осознанно НЕ делаем (вне объёма):** персист истории чата (сейчас `Screen` кеится по табу
  `App.tsx:229` → уход с вкладки чистит `msgs`), кнопка «Стоп» для активного стрима (AbortController уже
  есть, UI-кнопки нет), потоковая отдача по токенам (`_provider_turn` не стримит — весь ответ приходит одним
  `text`-событием), Gemini/Codex-адаптеры, удаление мёртвого `readonly`.

## Стратегия

Ф1 (UI: чат-only + скролл + вкладка «Настройки → Ассистент») → Ф2 (автоподгрузка моделей: разгейтить бэкенд +
починить эффект) → Ф3 (self-host CLIProxyAPI DooD-контейнером + оживление `gateway_internal`) → Ф4 (UI
self-host + headless-провижининг upstream-аккаунтов через Management API).

---

### Ф1 — UI: на странице только чат, он прокручивается; настройки — в Настройках → verify: `npm test` + `tsc`

- **`components/automation/AiChat.tsx`** (переезд из `components/settings/`) — оставить ТОЛЬКО чат:
  - удалить блок конфига (`AiChat.tsx:137-202`), баннер 204–208 заменить компактной плашкой со **ссылкой на
    «Настройки → Ассистент»**, снять `<PromptPresets/>` (`:210`), выкинуть стейт `keyInput`/`saving`/`models`
    и функцию `save()` (`:57-73`);
  - `GET /api/ai/config` на маунте **оставить** (`:39-46`) — из него нужны `enabled`/`has_key` для гейта
    композера (`:237`,`:240`); **никаких POST со страницы чата**;
  - **обязательно сохранить cleanup `return () => abortRef.current?.abort()`** — он живёт в том же
    `useEffect`, что и загрузка конфига (`:44-45`); при перекройке эффектов его легко потерять;
  - вёрстка: корень `<div className="flex flex-col h-full min-h-0 ni-pagebody">`; шапка
    `shrink-0 h-11 … ni-pagehead` («Ассистент» + подпись + иконка `Bot`); лог — `flex-1 min-h-0 overflow-y-auto p-4`
    (**вместо `max-h-80`**, `:213`), `scrollRef` и автоскролл-эффект (`:47`) навести на новый скроллер;
    композер — `shrink-0` внизу. `data-testid="ai-chat-log"` сохранить (на него смотрят тесты).
- **`components/settings/AiSettingsTab.tsx`** (новый) — перенесённая форма 1:1: Шлюз / Формат протокола /
  Модель / Base URL / API-ключ / «Включить агента» / «Лимит шагов» / «Сохранить», плюс `<PromptPresets/>`
  ниже. POST — **полный объект** (`{...cfg}` + `api_key` только когда непустой), как сейчас (`:61-62`).
- **`components/settings/PromptPresets.tsx`** — сделать контролируемым: пропсы `activeId` + `onPickActive(id)`;
  удалить `setActive` (`:38-49`) и чтение `/api/ai/config` из `load()` (`:27`,`:30`). CRUD пресетов не трогаем.
- **`components/Settings.tsx`** — добавить `SubTab "assistant"` (union `:790`), запись `{ id:"assistant",
  label:"Ассистент" }` в `tabs` (`:795-806`, поставить рядом с `mcp`) и ветку в switch (`:825-834`) по образцу
  строки 830: `{sub === "assistant" && <div className="flex flex-col gap-4 max-w-2xl"><AiSettingsTab/></div>}`.
  Скроллер там уже есть (`:809`) → перенесённая форма прокручивается бесплатно.
- **`App.tsx`** — поправить импорт `AiChat` на новый путь (строка ~24); монтирование (`:240`) не меняется.
- **Тесты:** `AiChat.test.tsx` переезжает в `components/automation/`; из него уходят проверки конфига
  (`«сохранён»`-бейдж, `:44`) — вместо них новый `AiSettingsTab.test.tsx` (рендер формы, `has_key`-бейдж,
  POST полного объекта, выбор пресета попадает в тело POST). В чат-тесте добавить проверку, что у лога есть
  прокручиваемый контейнер (класс `overflow-y-auto`) и что `max-h-80` больше нет.
- → verify: `cd frontend && npm test` и `npx --no-install tsc --noEmit`; вручную (или `docker compose build
  frontend` + preview) — на вкладке «Ассистент» длинная переписка прокручивается, композер виден всегда,
  на ширине ≤820px нижний таб-бар не перекрывает поле ввода; форма настроек живёт и сохраняется в
  «Настройки → Ассистент».

---

### Ф2 — Автоподгрузка списка моделей → verify: `cd backend && python -m pytest` + `npm test`

- **`services/ai_agent.py::list_models`** (`:218-235`):
  - в самое начало (до `_check_base_url` и до любой сети) — `if not key: return []`;
  - заголовки по провайдеру: `provider == "anthropic"` → `{"x-api-key": key, "anthropic-version": "2023-06-01"}`
    (как `_anthropic_turn`, `:308-310`), иначе — текущий `Authorization: Bearer`;
  - всё остальное (URL `{base_url}/models`, парс `data["data"][].id`, «никогда не бросает») не трогаем — оно
    выверено против апстрима CLIProxyAPI.
- **`api/ai.py::list_models`** (`:102-110`) — снять гейт `if cfg.gateway != "cliproxy"`; оставить
  дешифровку ключа и вызов. Эндпоинт по-прежнему под `require_account`, по-прежнему graceful.
- **Фронт (`AiSettingsTab.tsx`)** — починить эффект-каталог (перенесённый из `AiChat.tsx:49-53`):
  - зависимости `[cfg?.base_url, cfg?.provider, cfg?.has_key]` (гейт по `gateway` уходит вместе с серверным);
  - **явный рефетч в конце `save()`** — иначе после «Сохранить» с тем же `gateway` эффект не перезапустится
    (описанный в Контексте баг);
  - селектор рендерится при `models.length > 0`, иначе инпут; рядом кнопка «Обновить список».
- **Тесты бэкенда** (`backend/tests/test_ai_gateway.py`): строка 48 (`{"models": []}` для свежего аккаунта)
  **остаётся зелёной** за счёт раннего выхода по пустому ключу — но добавить явный тест «нет ключа → сети НЕ
  было» (подменить `httpx.AsyncClient` на объект, бросающий при вызове) + тест «anthropic → заголовок
  `x-api-key`» (мок транспорта, проверка заголовков).
- **Тест фронта:** мок `fetch` в `AiChat.test.tsx`/`AiSettingsTab.test.tsx` **бросает на неизвестных URL**
  (`AiChat.test.tsx:31`) → добавить `/api/ai/models` (и `/api/ai/prompts` в тест формы). В `CONFIG` (`:5-8`)
  дописать `gateway: "none"`.
- **Замечание к производительности (не блокер):** `net_guard.host_is_public` резолвит через блокирующий
  `socket.getaddrinfo` (`net_guard.py:26`) прямо в event-loop; автозапрос каталога на открытии вкладки
  добавляет один такой резолв. Сейчас это уже происходит на каждом тёрне агента, поэтому специально ничего не
  меняем; если станет заметно — обернуть в `asyncio.to_thread` (одна строка).
- **НЕ ПРОВЕРЕНО:** что прямой `https://api.anthropic.com/v1/models` отдаёт ту же форму `{"data":[{"id"}]}`
  (разведка это утверждает, но по первоисточнику не проверялось). Риск нулевой: контракт «`[]` при любой
  ошибке» вернёт ручной ввод.
- → verify: `cd backend && python -m pytest`; `cd frontend && npm test`; вручную — на аккаунте с ключом
  OpenAI-совместимого провайдера список моделей появляется без переключения шлюза и обновляется после смены
  `base_url` + «Сохранить».

---

### Ф3 — Self-host CLIProxyAPI как DooD-контейнер → verify: `pytest` + `docker compose config` + ручной старт

> Доделывает отложенную опц. Ф2 Плана J (`wave5-j-opencliproxy.md:93-120`) и оживляет `gateway_internal`.
> **Если фазу решено НЕ делать — обязателен минимум честности:** либо вынести `gateway_internal` в
> `AiConfigBody`/`_public`, либо удалить и поле, и ветку исключения в `_check_base_url` (`ai_agent.py:208`)
> вместе с тестами `test_ai_gateway.py:22-36`. Оставлять недостижимое исключение в коде нельзя.

- **`models/settings.py`** — новый `CliProxyConfig` на `AppSettings` (по образцу `McpConfig`, `:85-91`):
  `enabled: bool = False`, `image: str = "eceasy/cli-proxy-api:latest"`, `mgmt_secret_enc: str = ""`
  (Fernet, наружу маска). Порт **не конфигурируем** — 8317 внутри контейнера, наружу не публикуется.
- **`services/cliproxy_server.py`** (новый) — калька `mcp_server.py`:
  - `CONTAINER_NAME = "node-installer-cliproxy"`, `_CONTAINER_PORT = 8317`, `_fernet()` =
    `Fernet(b64(sha256(settings.encryption_key)))` (тот же инвариант, `mcp_server.py:54-56`),
    `_docker`/`_NO_DOCKER`/`_require_docker`/`container_state`/`stop`/`logs`/`reachable`/`status` — 1:1;
  - `start(account_id)`: guard `image.startswith("-")` (`mcp_server.py:194`) → `docker volume create
    node-cliproxy-auth` (идемпотентно) → `docker rm -f` → **`docker create`** с
    `--network $XRAY_CHECKER_NETWORK`, `-v node-cliproxy-auth:/root/.cli-proxy-api`, **БЕЗ `-p`** →
    **`docker cp <0600-tmpfile> <container>:/CLIProxyAPI/config.yaml`** → `docker start`;
  - **почему `docker cp`, а не bind-mount:** пути в `-v` резолвит ХОСТОВЫЙ демон, а конфиг мы пишем внутри
    своего контейнера — bind-mount указывал бы в пустоту. `docker cp` читает файл клиентом и стримит по
    сокету → работает через DooD. Auth-dir OAuth-токенов — именованный том (переживает пересоздание;
    потерять его = потерять все OAuth-логины);
  - `config.yaml` (генерируется, 0600-временный файл, секреты **не в argv**): `host: "0.0.0.0"`, `port: 8317`,
    **`api-keys: ["<secrets.token_urlsafe(32)>"]` — НИКОГДА пустой** (см. развилку про открытый доступ),
    `auth-dir: "/root/.cli-proxy-api"`, `remote-management: {secret-key: "<token_urlsafe(32)>",
    allow-remote: true}` (без секрета весь `/v0/management` отдаёт 404 — проверено по
    `config.example.yaml`; `allow-remote` нужен, т.к. наш backend — не localhost для контейнера);
  - клиентский ключ пишется в существующий волт `AiConfig.api_key_enc` (`ai_agent.encrypt_key`),
    management-секрет — в `CliProxyConfig.mgmt_secret_enc`; наружу отдаётся только `has_*`-флаг.
- **`ai_agent.py`** — `_INTERNAL_GATEWAY_HOSTS` (`:200`) дополнить `"cli-proxy-api"`.
- **`api/cliproxy.py`** (новый, `/api/cliproxy`, регистрируется в `main.py` под `_auth`): `GET/POST /config`
  (enable/disable), `GET /status`, `POST /start`, `POST /stop`. При `enabled=true` — после успешного старта
  **атомарно пропатчить секцию `ai`**: `gateway="cliproxy"`, `gateway_internal=True`,
  `base_url="http://node-installer-cliproxy:8317/v1"`, `api_key_enc=<клиентский ключ>` (через
  `storage.load_settings`→merge→`save_settings`, как `api/mcp.py:60-71`). Docker отсутствует →
  **200 + `warning`**, не 500 (`api/mcp.py:74-83`).
- **⚠️ Один общий контейнер** несёт креды последнего включившего аккаунта — та же оговорка, что у MCP
  (`mcp_server.py:15-17`). Для single-operator это ок; при желании — owner-marker как `mcp_owner.json`.
  Задокументировать, не замалчивать.
- **Тесты** `backend/tests/test_cliproxy.py`: генерация `config.yaml` (обязательно **непустой `api-keys`** —
  регрессия на главный риск; секреты не попадают в argv команды `docker`), Fernet at-rest
  (`mgmt_secret_enc` — не plaintext, наружу не отдаётся), `status` при отсутствии Docker → graceful,
  CRUD-конфиг + изоляция per-account, включение self-host выставляет `gateway_internal=True` и внутренний
  `base_url`. В `test_ai_gateway.py` — кейс «`cli-proxy-api` тоже exempt».
- **НЕ ПРОВЕРЕНО (проверить на живом контейнере при реализации):** (а) подхватывает ли CLIProxyAPI конфиг,
  доставленный `docker cp` до `docker start` (по докам конфиг читается из рабочего каталога — должен; если
  нет, добавить `docker restart` после `cp`); (б) считает ли `allow-remote` вызов из соседнего контейнера
  удалённым (ставим `true` на всякий случай); (в) точные имена ключей провижининга в `/v0/management`.
- → verify: `cd backend && python -m pytest`; `docker compose config` (валиден — мы compose не меняем,
  проверка на регресс); на машине с docker.sock: включить self-host → `docker ps` показывает
  `node-installer-cliproxy` **без опубликованных портов** → `GET /api/cliproxy/status`
  `{container:"running", reachable:true}` → чат отвечает через шлюз.

---

### Ф4 — UI self-host + провижининг upstream-аккаунтов (Management API) → verify: `npm test` + ручной smoke

- **`components/settings/AiSettingsTab.tsx`** — блок «Локальный шлюз CLIProxyAPI»: тумблер, статус контейнера
  (`GET /api/cliproxy/status`, чипы как в `McpTab`), кнопки Старт/Стоп, read-only endpoint
  (`http://node-installer-cliproxy:8317/v1`), плашка «Docker недоступен» при `warning`. Тема — только
  var-токены, без CDN (CSP-self-contained).
- **Провижининг upstream-провайдеров** (иначе шлюз пустой и бесполезен) — backend-прокси поверх
  `/v0/management` (management-секрет **никогда** не уходит в браузер):
  - **ключевые провайдеры:** `PUT /v0/management/{claude,codex,gemini,xai,vertex}-api-key` +
    `/openai-compatibility` — форма «добавить ключ провайдера», ключи в наш Fernet-волт;
  - **OAuth-подписки (headless):** `GET /v0/management/<provider>-auth-url` → показать оператору ссылку →
    он открывает её в своём браузере → поллинг `GET /v0/management/get-auth-status?state=<state>` до успеха.
    Это **единственный** доступный нам путь: браузерный CLI-flow апстрима требует пяти опубликованных
    callback-портов (8085/1455/54545/51121/11451), которые мы принципиально не публикуем.
- **Фаза опциональна по объёму:** без неё self-host работает только с ключевыми провайдерами, прописанными
  в `config.yaml`. Если времени нет — отгрузить UI-блок статуса/старт-стоп (первый буллет) и отложить
  провижининг; в этом случае явно написать в UI, что аккаунты провайдеров пока настраиваются вручную.
- **НЕ ПРОВЕРЕНО:** headless-OAuth end-to-end (разведка читала доки `help.router-for.me/management/api`,
  живого прогона не было). Флаг `state`, формат ответа `get-auth-status` и таймаут сессии уточнить на живом
  контейнере; `DELETE /v0/management/oauth-session` — отмена.
- **Ошибки атрибутировать честно:** `_provider_error` (`ai_agent.py:354`) маппит любой 401/403 в «Провайдер
  отклонил ключ». В режиме шлюза 401 гораздо вероятнее от **самого CLIProxyAPI** (ключа нет в его
  `api-keys`) — при `gateway=="cliproxy"` дописывать в текст «…или шлюз не принял клиентский ключ».
- → verify: `cd frontend && npm test` + `npx --no-install tsc --noEmit`; ручной smoke на машине с Docker:
  включить self-host → добавить один ключевой провайдер → список моделей в форме заполняется из шлюза →
  чат отвечает; (если сделан провижининг) залогинить один OAuth-аккаунт headless-потоком и увидеть его
  модели в каталоге.

## РАЗВЕДКА (факты)

**Наш код** (проверено открытием файлов):
- Единственный монтаж страницы — `App.tsx:240`; пункт nav — `Sidebar.tsx:44`; в `Settings.tsx:830` под
  вкладкой `mcp` рендерится только `<McpTab/>`. **CLAUDE.md:358 устарела** («AiChat под MCP-вкладкой»),
  §9c верна — привести CLAUDE.md в порядок при реализации.
- Скролл: `index.css:44` + `index.css:47` (`body{overflow:hidden}`) → документ не скроллится;
  `App.tsx:228` и `Screen` (`App.tsx:58-67`) `overflow` не задают; корень `AiChat.tsx:130` скроллер не
  заводит; лог зажат `max-h-80` (`AiChat.tsx:213`); мобильный `.ni-main` +58px (`index.css:317`).
  Эталоны скроллеров: `infra/ui.tsx:22-28`, `Settings.tsx:809`, `RuleBuilder.tsx:99`+`:113`.
- Двойной гейт каталога моделей: `api/ai.py:107` и `AiChat.tsx:49-53`; отсутствие `if not key` в
  `ai_agent.py:218-235`; Anthropic-заголовки — `ai_agent.py:306-311`.
- `POST /api/ai/config` — full replace (`api/ai.py:79-99`), патч-безопасен только `api_key` (`:95-96`).
- Мёртвые поля: `gateway_internal` (объявлено `settings.py:107`, читается `ai_agent.py:208`, нет ни в
  `AiConfigBody` `api/ai.py:29-52`, ни в `_public` `:59-71`, ни во фронт-типе `AiChat.tsx:6-15`);
  `readonly` (`settings.py:104`, нигде не читается агентом).
- Жёсткое требование ключа — `ai_agent.py:425-428`. SSRF-гард — `net_guard.py:21-46`, блокирующий резолв
  `:26`, escape-hatch `:18`. Allowlist контейнеров — `ai_agent.py:200`.
- DooD-эталоны: `mcp_server.py:134-159` (`_docker`), `:162-178` (`container_state`), `:194` (argv-guard),
  `:211-224` (0600 `--env-file`), `:283-320` (`reachable`/`status`); API — `api/mcp.py:74-85`
  (Docker-absent → 200 + `warning`). Compose: `docker-compose.yml:63-74` (`mcp`, profile `mcp-build`, образ
  **собирается**), `:157-162` (сеть `name: node-assistant-net`); у `xray-checker` compose-записи НЕТ вообще —
  он целиком поднимается `docker run`-ом, это и есть наш случай.
- Тестовые «мины»: `test_ai_gateway.py:48`; мок-`fetch`, бросающий на неизвестных URL — `AiChat.test.tsx:31`;
  `CONFIG` без `gateway` — `AiChat.test.tsx:5-8`.

**Апстрим CLIProxyAPI** (`github.com/router-for-me/CLIProxyAPI`, MIT; выверено по исходникам, не по прозе):
- Порт по умолчанию **8317** (`config.example.yaml`); `host: ""` = все интерфейсы; `auth-dir:
  "~/.cli-proxy-api"`; `api-keys: []`; hot-reload конфига.
- Маршруты (`internal/api/server.go`): OpenAI-группа `/v1` — `GET /v1/models`, `POST /v1/chat/completions`,
  `/v1/completions`, `/v1/responses`; **Anthropic — под тем же `/v1`**: `POST /v1/messages`,
  `/v1/messages/count_tokens`; Gemini — `/v1beta/models/*action`; Codex — `/backend-api/codex/*`.
  Ungated: `GET|HEAD /healthz`. ⇒ один `base_url = http://host:8317/v1` обслуживает и оба наших формата, и
  каталог моделей — **наши URL в `ai_agent.py:225/254/293` совпадают с апстримом 1:1** (текст Плана J
  `wave5-j-opencliproxy.md:78-80` с «`{base_url}/v1/models`» — ошибочен, код прав).
- Клиентская аутентификация (`docs/sdk-access.md`): ключ принимается из `Authorization: Bearer`, `X-Api-Key`,
  `X-Goog-Api-Key`, `?key=`, `?auth_token=`; 401 при отсутствии/непринятии. ⇒ и наш Bearer, и наш
  `x-api-key` шлюз понимает.
- **⚠️ Пустой `api-keys` ⇒ доступ БЕЗ аутентификации.** `sdk/access/manager.go`: `providers := m.Providers();
  if len(providers) == 0 { return nil, nil }` — ноль провайдеров = доступ разрешён (docs/sdk-access.md
  дублирует прозой: «…allowing callers to treat access control as disabled»). Это главный риск self-host —
  см. закреплённую развилку.
- `GET /v1/models` → ровно `{"object":"list","data":[{"id","object","created","owned_by"}]}`
  (`sdk/api/handlers/openai/openai_handlers.go`) — совпадает с нашим парсером.
- Деплой (апстримовый `docker-compose.yml`): образ `eceasy/cli-proxy-api:latest`, `container_name:
  cli-proxy-api`, `WORKDIR /CLIProxyAPI`, `CMD ["./CLIProxyAPI"]`, `EXPOSE 8317`; тома
  `config.yaml → /CLIProxyAPI/config.yaml`, **персистентный** `/root/.cli-proxy-api` (auth-dir), опц. logs и
  plugins; порты `8317` + **пять OAuth-callback портов** `8085/1455/54545/51121/11451`.
- Management API (`help.router-for.me/management/api`): база `/v0/management`, заголовок
  `Authorization: Bearer <secret>` или `X-Management-Key`; при пустом `remote-management.secret-key` **весь
  тракт 404**; удалённым вызывающим нужен `allow-remote: true`. Ключевые провайдеры:
  `/claude-api-key`, `/codex-api-key`, `/gemini-api-key`, `/xai-api-key`, `/vertex-api-key`,
  `/openai-compatibility`. OAuth headless: `GET /{anthropic,codex,antigravity,kimi,xai}-auth-url` →
  `GET /get-auth-status?state=` → `DELETE /oauth-session`. Файлы OAuth-токенов: `GET/POST/DELETE
  /auth-files`, `PATCH /auth-files/{status,fields}`, `GET /auth-files/models`. **Маршрута
  `/v0/management/usage` НЕ существует** (в Плане J `wave5-j-opencliproxy.md:164` — ошибка); реальные —
  `GET /api-key-usage` и `GET /usage-queue`.
- `openai-compatibility`-блок конфига позволяет заводить произвольные ключевые апстримы (OpenRouter и т.п.)
  с алиасами моделей ⇒ строка `model`, которую мы шлём, — это alias на стороне шлюза, не обязательно
  реальный id модели провайдера.
- **MCP в ядре шлюза отсутствует** (ни в таблице маршрутов, ни в схеме конфига) — подтверждает рамку Плана J:
  перенаправляем LLM-хоста (`ai_agent`), `mcp_server.py` не трогаем.
- **Частично проверено:** список моделей провайдеров в README — маркетинговая копия, с живым инстансом не
  сверялась; флаги CLI-логина (`--login`/`--codex-login`/`--claude-login`) взяты из доков, косвенно
  подтверждаются списком callback-портов в апстримовом compose.

## Критерии готовности плана C

- Вкладка «Ассистент» содержит **только чат**: лог занимает высоту экрана и прокручивается, композер прибит
  к низу, `max-h-80` удалён, на ≤820px нижний таб-бар не перекрывает ввод. Страница чата **не делает ни
  одного POST** в `/api/ai/config`.
- Настройки провайдера/модели/ключа/лимитов + пресеты промптов живут в «Настройки → Ассистент»;
  `active_preset_id` сохраняется единственным полным POST формы (двойного писателя и гонки больше нет).
- Список моделей подгружается автоматически для любого провайдера с сохранённым ключом, обновляется после
  «Сохранить» и по кнопке «Обновить список»; без ключа — **ноль исходящих запросов** (`[]`);
  ошибка каталога деградирует в ручной ввод.
- `cd backend && python -m pytest` и `cd frontend && npm test` + `npx --no-install tsc --noEmit` — зелёные;
  `test_ai_gateway.py` обновлён (`{"models": []}` теперь держится ранним выходом по пустому ключу, а не
  гейтом по `gateway`).
- (Ф3) CLIProxyAPI поднимается DooD-контейнером `node-installer-cliproxy` на `node-assistant-net`
  **без опубликованных портов**, с **непустым `api-keys`** (регрессионный тест на это обязателен),
  management-секрет и клиентский ключ — в Fernet-волте, наружу маска; Docker отсутствует → 200 + `warning`.
  `gateway_internal` выставляется только серверной стороной и перестаёт быть мёртвым кодом
  (либо — при отказе от Ф3 — удаляется вместе с веткой исключения).
- (Ф4) В настройках виден статус контейнера и старт/стоп; если сделан провижининг — ключевой провайдер
  добавляется из UI, headless-OAuth логинит хотя бы один аккаунт, и его модели видны в каталоге.
- CLAUDE.md обновлён при реализации: §8d — убрать «AiChat.tsx (под MCP-вкладкой)» (устарело), описать новое
  расположение чата и вкладку настроек; §8d/План J — снять «self-host отложен», записать имя контейнера,
  тома, `docker cp`-доставку конфига, и **явно зафиксировать риск пустого `api-keys`** в §6 Troubleshooting.
