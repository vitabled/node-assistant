# Волна 8 — теги хостингов · просмотр токена · Обновления · автобэкап · балансер (selector) · ASN · анализ подписки

> Продвинутый промпт пользователя (7 пунктов), уточнён через 3 раунда вопросов. Ниже — по пункту:
> запрос, разведка по коду, ЗАФИКСИРОВАННЫЕ решения (из Q&A) и реализация. Всё per-account, кроме
> §3 «Обновления» (глобально, host-level) и DooD-синглтонов. Секреты — в открытом виде не хранить,
> кроме module-scoped Fernet-волтов (как MCP/HAProxy/rules).

## Глобальная разведка (проверено по коду)
- Хостинги: `models/hostings.py` (`HostingBody{name,website,notes,features,tariffs[],locations[],provider_ref}`,
  `Tariff{name,specs,bandwidth,price,currency,period}`, `Location{city,country_code,lat,lng,note}`),
  `services/hostings_store.py` (per-account `hostings.json`, атомарно+lock, MAX 500), `api/hostings.py` (CRUD под
  `_auth`), фронт `components/hostings/{HostingsCatalog,HostingsMap,search,geo,api}.tsx`. Тегов/ASN сейчас нет.
- Settings/Remnawave: `Settings.tsx::RemnavaveTab` грузит `api_token` из `GET /api/settings` и кладёт в
  `type="password"` поле → **токен уже приходит в браузер** (plaintext в settings.json, статус-кво). «Просмотр» =
  reveal-тумблер, бэкенд не трогаем.
- Версия/обновления: `main.py` — `FastAPI(version="1.0.0")` (статичная строка), **механизма самообновления нет**.
  Стек — DooD, собирается из исходников (`docker-compose.yml`, профильные билды как MCP/nodeflow). Паттерн
  «оркестратор находит СВОЙ контейнер инспектом» уже есть — `nodeflow_server._node_data_volume` резолвит том по
  `docker inspect` собственного контейнера; так же берётся `com.docker.compose.project.working_dir` (путь проекта
  на хосте) и `com.docker.compose.project`.
- Бэкап/экспорт: `services/export_service.py` (`GET /api/export/stores`, `POST /api/export` → tar.gz 15 сторов,
  `include_secrets=true`→400 пока), `services/telegram.py::send_message` (**только текст**, `sendDocument` нет),
  фронт `settings/DataTransfer.tsx`. Правиловый telegram-волт — `rules_store` (Fernet).
- Балансер (selector): Remnawave `remnawave.injectHosts[].selector={type:"uuids",values:[uuid…]}` + `tagPrefix`;
  `routing.balancers[].selector:["proxy"]` (prefix-matcher) балансирует по инжектнутым аутбаундам. Клиент уже умеет
  `list_config_profiles`/`get_config_profile(uuid)`/`update_config_profile(uuid,config)`/`create_host`;
  хосты создаются на деплое в `pipeline.step_create_hosts` (после `create_node`). Локальный хост-шаблон —
  `models/hosts.py::HostTemplateBody`, фронт `Hosts.tsx`.
- Подписки/анализ: `services/test_tools.py::parse_xray_link` (vless/vmess/trojan/ss → адрес/порт, ошибки БЕЗ
  фрагментов ссылки), `services/subscription_import.py::{decode_subscription,link_to_candidate,country_of}`,
  `net_guard.is_safe_url` (SSRF-гард), `server_monitor._fetch_subscription` (UA-fetch с ручными редиректами,
  per-hop гард, лимит байт). **IP→ASN/гео сейчас НЕТ** — нужен новый источник.

---

## 1. Теги хостингов
**Запрос:** система тегов; добавить новый тег + выбрать несколько существующих; теги видны на плашках хостингов.

**Решения:** account-level пул тегов; чипы на карточках; тег как фильтр каталога (подтверждено допущением).

**Реализация:**
- Backend: `HostingBody.tags: list[str] = []` (нормализация: trim, dedup, ≤10 тегов, ≤24 симв., без CR/LF).
  Пул тегов НЕ отдельная сущность — выводится как `sorted(set(all hostings' tags))` в `api/hostings.py`
  новым `GET /api/hostings/tags` (для автодополнения). Стор без изменений (тег — поле записи).
- Frontend: в редакторе хостинга `HostingsCatalog.tsx` — компонент тег-инпута (чипы + «+ тег»,
  подсказки из `GET /tags`; переиспользовать паттерн `common/HeadersEditor` идеи «контролируемый список»).
  На карточке — ряд чипов (var-токены, как существующие бейджи). В `search.ts::matchHosting` — матч по тегам;
  в каталоге — быстрый фильтр-по-тегу (клик по чипу).
- **Проверка:** `test_hostings.py` (+теги: нормализация/лимит/`/tags` дедуп), `HostingsCatalog` рендер чипов
  (vitest), `tsc`.

## 2. Просмотр API-токена панели (Settings/Remnawave)
**Запрос:** возможность просматривать указанный api-токен панели.

**Решение:** reveal-тумблер (токен уже в браузере). Frontend-only.

**Реализация:** в `RemnavaveTab` у поля `api_token` — кнопка-глаз (Eye/EyeOff), переключающая `type`
password↔text (как в других password-полях проекта). То же добавить в `common/PanelRegistry` при
редактировании токена панели, если поле там password. Бэкенд/модель не трогаем. **Проверка:** `tsc` + ручной
reveal в `Settings.test.tsx` (toggle меняет `type`).

## 3. Раздел «Обновления» (Settings → вкладка «Обновления»)
**Запрос:** настройки обновления сервиса, в т.ч. автообновление при новой версии на GitHub.

**Решения (Q&A):** git pull + `docker compose build` + `up -d` через DooD; настройка **глобальная (host-level,
не per-account)**; «версия» = **коммит отслеживаемой ветки** (local HEAD vs remote HEAD); автоприменение —
**тумблер (по умолчанию ВЫКЛ)** + кнопка «Обновить сейчас».

**Реализация:**
- **`services/updater.py` (глобальный синглтон):**
  - `project_dir()`/`project_name()` — `docker inspect` СВОЕГО контейнера → лейблы
    `com.docker.compose.project.working_dir` / `com.docker.compose.project` (как `nodeflow_server`). Это путь
    репозитория на ХОСТЕ (там лежит `docker-compose.yml` и `.git`).
  - `local_rev()` = `git -C <dir> rev-parse HEAD`; `remote_rev(branch)` = `git -C <dir> ls-remote origin <branch>`
    (или `git fetch` + `rev-parse origin/<branch>`). `check()` → `{branch, local, remote, behind: local!=remote,
    subject}`. Приватный репо → полагаемся на git-креды хоста (репо уже склонирован с доступом); опц. поле
    `git_token` (Fernet) на будущее, но по умолчанию не требуется.
  - **`apply()` — ДЕТАЧНЫЙ self-update sidecar:** `docker run -d` вспомогательный `docker:cli`-контейнер с
    маунтом docker-сокета + host project dir, команда `git -C <dir> pull --ff-only && docker compose -f
    <dir>/docker-compose.yml build && docker compose ... up -d`. Детачед — переживает пересоздание backend'а
    (иначе `up -d` убьёт процесс, отдавший команду, на середине). Статус пишется в
    `DATA_DIR/updater_status.json` (шаги/rc/лог-хвост), новый backend его читает для UI. Single-flight-лок.
  - Состояние (глобальное): `DATA_DIR/updater.json` `{auto_update, branch, last_check, last_result}` (не секрет).
- **`api/updates.py` (под `_auth`):** `GET /api/updates/status` (check + текущее состояние; кеш 60с),
  `POST /api/updates/config` (`{auto_update, branch}`), `POST /api/updates/apply` (запуск sidecar; Docker/git
  отсутствуют → warning, не 500 — как MCP/nodeflow). ⚠️ **Любой аутентифицированный аккаунт может инициировать
  host-wide рестарт** — как и запуск прочих DooD-синглтонов (nodeflow/mcp). Задокументировать; при желании
  позже сузить до «первого аккаунта».
- **Автопроверка:** `updater.auto_loop` в lifespan, **гейт `worker_lease` monitoring** (как `poller_loop`),
  интервал ~6ч: `git fetch` → если `behind` и `auto_update` → `apply()`. Глобальный (один луп, не per-account).
- **Frontend:** `Settings.tsx` новая `SubTab "updates"` → `settings/UpdatesTab.tsx`: текущий коммит/ветка,
  «доступно обновление N» (subject), тумблер «Автообновление», выбор ветки, кнопки «Проверить»/«Обновить сейчас»
  + прогресс из `updater_status.json` (поллинг). Плашка-предупреждение о рестарте.
- **Проверка:** `test_updater.py` (pure: `local/remote_rev` парсинг `git`-вывода mock'ом, `behind`-логика,
  argv-билдер sidecar, status-json roundtrip; Docker/git-absent → warning). `tsc`.

## 4. Автоматическое резервное копирование → Telegram (Settings → Экспорт/Импорт)
**Запрос:** настройки автобэкапа + автоотправка копии в телеграм.

**Решения (Q&A):** контент — полный per-account экспорт с **тумблером «включать секреты»**; расписание —
**интервал** (напр. каждые N часов/дней); **свои bot_token/chat_id** (не переиспользуем правиловый бот).

**Реализация:**
- **`export_service.py`:** снять запрет `include_secrets` для ВНУТРЕННЕГО вызова автобэкапа (роут `/api/export`
  остаётся с 400 на `include_secrets=true` из браузера — секреты наружу через HTTP не отдаём; автобэкап зовёт
  сервисную функцию напрямую с `include_secrets` по конфигу). Функция `build_archive(account_id, include_secrets)
  -> bytes`.
- **`telegram.py`:** `send_document(bot_token, chat_id, filename, data: bytes, caption)` — multipart
  `POST /bot<token>/sendDocument`, best-effort, `redact` в логах.
- **`services/auto_backup.py`:** конфиг per-account в `settings.json` секции `auto_backup`
  (`AutoBackupConfig{enabled, interval_hours, include_secrets, chat_id, bot_token_enc(Fernet), last_run,
  last_error}`); `run_once(account_id)` = build_archive → send_document. Волт-ключ = SHA-256 `encryption_key`.
- **`api/backup_auto.py`** (или расширить `api/settings.py`): `GET/POST /api/settings/auto-backup`
  (bot_token write-only, blank=keep; наружу только `has_token`), `POST /api/settings/auto-backup/run` (тест —
  «Отправить сейчас»). Под `_auth`.
- **Фоновый луп:** `auto_backup.loop` в lifespan, **гейт `worker_lease` monitoring**, per-account explicit
  `account_id` (как `collector_loop`): каждые ~15 мин проверяет `enabled && now-last_run≥interval` → `run_once`.
- **Frontend:** `settings/DataTransfer.tsx` — блок «Автобэкап»: тумблер, интервал, чекбокс «Включать секреты»
  (+amber-предупреждение про приватность чата), bot_token (password) + chat_id, «Отправить сейчас».
- **Проверка:** `test_auto_backup.py` (config CRUD/шифрование-at-rest/blank-keeps; `send_document` мок;
  include_secrets прокидывается; интервал-триггер). `tsc`.

## 5. «Балансер» (selector) в хостах вместо `$hostid`
**Запрос (переопределён):** убрать `$hostid`; host UUID дописывается в **selector** реального xray-json
config-профиля Remnawave; в настройках хоста выбирать из **реальных** xray-json профилей.

**Решения (Q&A):** список = **отдельная injectHosts-группа** «config-профиль · tagPrefix»; UUID идёт ровно в
выбранную группу; **жизненный цикл: добавлять при создании хоста, убирать при удалении/переустановке**.
Selector: `remnawave.injectHosts[].selector={type:"uuids",values:[…]}` (порядок = порядок аутбаундов).
**Переменную `$hostid` НЕ вводим.**

**Реализация:**
- **Pure-хелперы `services/xray_selector.py`:** `list_uuid_groups(config)->[{tag_prefix, count}]` (найти все
  `remnawave.injectHosts` с `selector.type=="uuids"`); `add_uuid(config, tag_prefix, uuid)` /
  `remove_uuid(config, tag_prefix, uuid)` (deepcopy, dedup, порядок сохраняется; группа не найдена → отчёт, не
  бросать). Тесты на них.
- **API «список балансеров»:** `GET /api/remnawave/balancers` — `list_config_profiles` → per profile
  `get_config_profile` → `list_uuid_groups` → `[{config_profile_uuid, config_profile_name, tag_prefix,
  count}]`. Гейт «панель не настроена» → 400/пусто.
- **Модель хоста:** `HostTemplateBody.balancers: list[BalancerRef] = []`,
  `BalancerRef{config_profile_uuid: str, tag_prefix: str}` (валидировать tag_prefix charset). Убрать любые
  следы `$hostid` из шаблонной подстановки (если добавлялись).
- **Деплой (`pipeline.step_create_hosts`):** после успешного `create_host` (есть host `uuid`) — для каждого
  `tpl.balancers`: `get_config_profile` → `add_uuid(config, tag_prefix, uuid)` → `update_config_profile`
  (dedup идемпотентен; per-balancer failure → warn, не валит деплой — хосты аддитивны).
- **Удаление/переустановка:** в пути удаления ноды/хоста (`node_ops` uninstall / удаление карточки) —
  `remove_uuid` из selector'ов балансеров этого хоста. Источник «какие балансеры у хоста» — `tpl.balancers`;
  плюс страховка: при удалении можно просканировать все config-профили и убрать `uuid` (selector.values —
  и есть накопленный список). Best-effort, не валит удаление.
- **Frontend `Hosts.tsx`:** мультиселектор «Балансер» (переиспользовать `MultiSelect`) с опциями из
  `GET /api/remnawave/balancers`, label = «<config-profile> · <tagPrefix> (N)», value =
  `<uuid>::<tagPrefix>`. Сохраняется в `balancers`. Гейт «панель не настроена» → подсказка.
- **Проверка:** `test_xray_selector.py` (add/remove/dedup/порядок/несколько групп/группа-не-найдена),
  `test_hosts.py` (+balancers валидация), `test_host_autocreate.py` (+ append UUID в selector после create_host,
  update_config_profile вызван), `Hosts.test.tsx`/`tsc`.

## 6. Поле ASN в хостингах
**Запрос:** поле со списком причастных к хостингу ASN.

**Реализация:** `HostingBody.asns: list[AsnRef] = []`, `AsnRef{number: int (ge=0), name: str="",
website: str=""}` (или простой `list[str]`, если пользователь предпочтёт — уточнить в реализации; по умолчанию
структурный, т.к. §7 заполняет name/website). Карточка: блок «ASN» (номер + имя, ссылка на сайт). Редактор:
список-эдитор (номер/имя/сайт). Заполняется вручную И кнопкой §7. **Проверка:** `test_hostings.py` (+asns),
`tsc`.

## 7. Раздел «Анализ подписки»
**Запрос:** ввод url подписки / домена / ip. Для url: fetch (UA v2rayng, как xray-checker) → целевой хост →
ASN ip → реальное гео (по ip) + реестровое гео + название ASN (Selectel/Timeweb…) + сайт ASN. Кнопка
«Добавить в хостинги».

**Решения (Q&A):** источники — **фактическое = ip-api.com/ipwho.is, реестровое = RDAP/RIR-whois** (без ключей;
ASN-имя/сайт — RDAP/PeeringDB, best-effort). «Добавить в хостинги» — **одна запись на ASN/провайдера** (дедуп по
ASN; серверы → локации + поле ASN §6; website = сайт ASN).

**Реализация:**
- **`services/subscription_analyze.py`:**
  - `fetch_subscription(url)` — переиспользовать паттерн `server_monitor._fetch_subscription` (UA `v2rayNG/…`,
    ручные редиректы, per-hop `is_safe_url`, лимит байт, `follow_redirects=False`).
  - `decode_subscription` + `parse_xray_link` (из `subscription_import`/`test_tools`) → список целевых хостов;
    домены → `getaddrinfo` (в `asyncio.to_thread`, таймаут) → IPv4; дедуп по IP.
  - `resolve_ip(ip)` (SSRF-гард на PUBLIC-IP не нужен — это исходящий lookup к API, но валидировать, что ip
    публичный): **actual** = `ip-api.com/json/<ip>?fields=...` (ASN номер+org, country/city, isp) с fallback
    `ipwho.is/<ip>`; **registry** = RDAP `https://rdap.org/ip/<ip>` (страна аллокации, handle) + autnum RDAP
    (имя/сайт ASN, remarks/links) — best-effort, ошибки → поля пустые, не бросать. **Ссылки/секреты в логи не
    попадают** (как `subscription_import`).
  - Результат: `[{ip, host, asn:{number,name,website}, geo_actual:{cc,city}, geo_registry:{cc}}]`.
- **`api/sub_analysis.py` (под `_auth`):** `POST /api/subscription-analyze` (`{input: url|domain|ip}`; url →
  fetch+parse, domain/ip → одиночный анализ; dry-run, ничего не пишет). `POST /api/subscription-analyze/to-hostings`
  (`{results}` → группировка по ASN → `HostingBody` per ASN: name=asn.name/org, website=asn.website,
  asns=[asn], locations=[{country_code: actual cc, city}], tags=[]) → `hostings_store` upsert (дедуп по
  name/ASN). Rate-limit внешних API — последовательно/с небольшим лимитом конкуренции.
- **Frontend:** nav-таб «Анализ подписки» (группа — «Справка» рядом с хостингами, или «Статистика»; уточнить —
  по умолчанию «Справка»). `components/SubscriptionAnalyze.tsx`: инпут (url/домен/ip) → таблица результатов
  (хост, IP, ASN номер+имя+сайт, факт-гео vs реестр-гео с флагами `FlagChip`, подсветка расхождения) → кнопка
  «Добавить в хостинги» (дизейбл если нет результатов).
- **Проверка:** `test_subscription_analyze.py` (decode/parse без утечки ссылки; группировка по ASN → HostingBody;
  мок ip-api/RDAP; domain/ip-ветка; SSRF-reject приватного/loopback). `SubscriptionAnalyze.test.tsx`/`tsc`.

---

## Порядок реализации (фазы, независимые где можно)
1. **Ф1 — мелочи, низкий риск:** §2 (reveal-токен), §1 (теги), §6 (ASN-поле). Backend-модели + фронт-редакторы
   + карточка. Один заход.
2. **Ф2 — §5 балансер (selector):** `xray_selector.py` + API `/balancers` + модель `balancers` + деплой-хук +
   удаление-хук + `Hosts.tsx`. Самый связный с деплой-пайплайном.
3. **Ф3 — §7 анализ подписки:** `subscription_analyze.py` + API + фронт-раздел (зависит от §6 ASN-поля для
   «Добавить в хостинги»).
4. **Ф4 — §4 автобэкап:** `send_document` + `auto_backup.py` + loop + `DataTransfer.tsx`.
5. **Ф5 — §3 Обновления:** `updater.py` + API + auto_loop + `UpdatesTab.tsx` (самый инфраструктурный, DooD
   self-update sidecar — делать последним и осторожно).

## Глобальная верификация
- Backend: `python -m py_compile` изменённых + `pytest` новых (`test_hostings`,`test_updater`,`test_auto_backup`,
  `test_xray_selector`,`test_host_autocreate`,`test_subscription_analyze`) + не сломать существующие
  (`test_settings_*`,`test_haproxy`,`test_hosts`).
- Frontend: `tsc --noEmit` (Docker `ni-frontend-test`) + vitest новых (`--maxWorkers=2`, сверять число файлов).
- Секреты: bot_token/git_token/ASN-key (если появится) — Fernet, наружу только `has_*`; ссылки подписок в логи
  не попадают; export с секретами — только внутренний вызов автобэкапа, не через браузерный роут.
- **Continuous Memory:** после каждой фазы обновлять CLAUDE.md (новые §, роуты, сторы, квирки RDAP/ip-api,
  DooD self-update sidecar).

## Открытые мелочи (дефолты, поправить при реализации)
- §6 ASN — структурный `AsnRef` (не просто строки), т.к. §7 пишет name/website. Если пользователь хочет просто
  перечисление — упростить.
- §7 nav-группа — «Справка» (рядом с хостингами). Если логичнее «Статистика» — перенести.
- §3 приватный репо — по умолчанию на git-кредах хоста; поле `git_token` (Fernet) добавить только если репо
  приватный и пул падает.
- §3 кто может инициировать апдейт — любой аккаунт (как прочие DooD-синглтоны); при необходимости сузить.
