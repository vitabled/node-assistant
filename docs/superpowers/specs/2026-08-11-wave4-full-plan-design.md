# Волна 4: 8 задач — полный план (цепочка PR в main)

Дата: 2026-08-11. Статус: утверждён пользователем после Q&A (8 вопросов).

## Стратегия доставки

Цепочка маленьких PR, каждый со своей ветки от `main`, последовательный мердж
(rebase по мере надобности). В каждом PR: `tsc --noEmit` + `vitest run` +
`pytest` зелёные. Порядок: PR-0 → PR-1 … PR-7.

**PR-0**: текущие 4 коммита `claude/remnawave-wave3` (скин NodeFlow, тема-зависимый
акцент, vite NI_BACKEND, spec) — пуш и мердж первым.

## Решения из Q&A

| # | Тема | Решение |
|---|---|---|
| 1 | PR-стратегия | A — цепочка маленьких PR |
| 2 | Мосты: модель | A — маршруты внутри config-профилей Remnawave |
| 3 | Параметры моста | Параметры маршрутизации (RuleObject: domain[] и др.) |
| 4 | «Авто» | A — структурный редактор + raw JSON |
| 5 | SSL «авто» | D — форма SSH (юзер+пароль), сервис сам собирает домены |
| 6 | Источники доменов | A — все: nginx/apache, certbot, xray/remnanode, масксайт |
| 7 | IP-расшифровка | A — резолв + обогащение (без проксирования через ноду) |
| 8 | AI OAuth | A — все OAuth-провайдеры CLIProxyAPI |

## Documentation basis (сверено 2026-08-11)

- **xtls `llms-full.txt`**: RuleObject = `domain, ip, port, sourcePort,
  localPort, network, sourceIP(alias source), localIP, user, vlessRoute,
  inboundTag, protocol, attrs, process, outboundTag, balancerTag, ruleTag,
  webhook`. Поля `type` (легаси `"type":"field"`) в актуальной документации
  НЕТ → не используем (Unconfirmed per Role.txt). Множество matcher-полей
  работают по AND; внутри массива — OR. domain-синтаксисы:
  `keyword:/regexp:/domain:/full:/dotless:/geosite:/ext:`.
- **docs.rw/learn/xray-json-advanced**: директива `remnawave` в XRAY_JSON
  шаблонах подписки: `injectHosts[]` (selector:
  uuids/remarkRegex/tagRegex/sameTagAsRecipient; `selectFrom`:
  HIDDEN/NOT_HIDDEN/ALL; ровно одно из tagPrefix/useHostRemarkAsTag/
  useHostTagAsTag), `addVirtualHostAsOutbound`. Требуется Remnawave ≥ 2.6.3.
- **api-1.json (Remnawave API)**:
  - `POST /api/users` — обязательные `username`, `expireAt`;
    `trafficLimitBytes: 0` = безлимит; `trafficLimitStrategy` (NO_RESET);
    `activeInternalSquads`.
  - `PATCH /api/config-profiles {uuid, name?, config: object}`.
  - `POST /api/subscription-templates {name, templateType}` (enum: XRAY_JSON,
    XRAY_BASE64, MIHOMO, STASH, CLASH, SINGBOX) — создаёт пустой, контент
    пишется PATCH'ем (клиент уже умеет: `update_subscription_template`).
  - `GET /api/sub/{shortUuid}` (+ `/{clientType}`) — контент подписки
    (XRAY_JSON → парсим outbounds).
  - Пользователь имеет `subscriptionUrl` (48 упоминаний в схемах).

---

## PR-1. Фикс вечного логаута (P0)

**Корень**: панель Remnawave отвечает 401/403 → backend пробрасывает статус
наружу (`raise HTTPException(exc.status or 502, ...)`) → `apiClient`
считает любой 401 смертью сессии → `forget(id)` → логин. После входа фоновый
запрос к той же панели снова даёт 401 → цикл.

**Фикс, две линии:**

1. Backend: helper `_raise_downstream(exc)` (в `remnawave_client.py` или общем
   месте): `exc.status in (401, 403)` → HTTPException(502, detail с пометкой
   «Панель ответила <status>: …»), прочие статусы — как раньше. Применить во
   ВСЕХ точках `HTTPException(exc.status or 502, …)`: settings.py (check,
   proxy-эндпоинты), migrate.py, config_templates.py, subpage_configs.py,
   traffic_rules.py, infra_billing.py, node_ops.py, user_stats.py и др.
   (grep `exc.status`).
2. Frontend `apiClient`: `forget()` только при заголовке
   `x-session-invalid: 1` в 401-ответе; backend `require_identity` добавляет
   этот заголовок к своим 401. Защита от любых будущих пробросов 401.

**Тесты**: pytest — клэмп 401→502 на фейковом клиенте; vitest — apiClient:
401 с заголовком → forget, 401 без заголовка → сессия жива.

## PR-2. Установка: виджет панелей

- `PanelRegistry` — собственная карточка (`.card` + padding), отделённая от
  родительского виджета; проверка обеих тем (на скрине светлая — сливается).
- Кнопка «Изменить» у каждой панели → модалка: название, panel_url,
  api_token (password-input, пустое = не менять), default_internal/external
  squads (мультиселект из `/api/remnawave/squads/*` как в Settings).
  Submit → `PUT /api/settings/remnawave/panels/{id}` (endpoint существует).
- Кнопки «Главная»/«Удалить» сохраняются.

**Тесты**: рендер кнопки «Изменить», заполнение и submit PUT; пустой токен не
затирает сохранённый (как в AiSettingsTab).

## PR-3. Дашборд + SSL

**Дашборд**: в заголовок «История доступности за последние 7 дней» —
iconbtn-глаз (Eye/EyeOff). Скрытие: список не рендерится (данные продолжают
грузиться — мгновенное раскрытие). Состояние: localStorage `ni_hide_incidents`
(device-global), по умолчанию показано.

**SSL «Авто»** (CertsForm, рядом с полем домена — кнопка «Авто»):
- Модалка: хост (селектор известных серверов из деплоя + ручной ввод),
  SSH-порт (22), юзер, пароль. Кнопка «Сканировать».
- Backend `POST /api/certs/scan-domains {ip, ssh_port, username, password}`:
  по SSH (asyncssh, как stats/node) выполняет read-only команды:
  - `grep -r server_name /etc/nginx /etc/apache2 2>/dev/null`;
  - `ls /etc/letsencrypt/live`;
  - домены из конфигов xray/remnanode (`dest`, `serverName`, SNI в
    /usr/local/etc/xray, /opt/remnanode);
  - маскировочный сайт из docker-окружения (`docker inspect` имён известных
    контейнеров / env `FAKE_SITE`/`MASK`).
  - Ответ: `{domains: [{domain, sources: ["nginx","certbot"]}]}` —
    дедупликация, пометка источников.
- Фронт: список с чекбоксами → «Добавить выбранные» → в «Домены»
  (DomainsPanel store, как ручное добавление).
- Пароль не логируется и не сохраняется (per-request, как SSH-креды деплоя).

**Тесты**: pytest — парсеры вывода (nginx server_name, certbot ls, xray
serverName) на эталонных строках; vitest — модалка, чекбоксы, добавление.

## PR-4. Анализ подписки

1. **Селектор User-Agent** в SubscriptionAnalyze: пресеты — «Авто (цепочка)»
   (текущее поведение), v2rayNG, Streisand, sing-box, Mihomo/Clash,
   Shadowrocket, Happ. Значение уходит параметром в запрос анализа; backend:
   фиксированный UA вместо цепочки (цепочка остаётся для «Авто»).
2. **IP-расшифровка** каждой ноды: резолв `address` → A/AAAA (dnspython) →
   обогащение через ip-api.com/batch (до 100 адресов/запрос, без ключа;
   поля country/city/as/org/reverse/isp/hosting-flag). 2ip.io — запасной
   провайдер (если появится ключ). Кэш per IP (TTL 24ч). UI: колонка/блок
   «IP» с развёрнутой расшифровкой.
3. **Починка website**: расследовать `_website_from_rdap_autnum` (вероятно
   links парсятся неверно/пусто) и PeeringDB fallback; добавить лог-путь
   отладки в ответ (`websiteSource: rdap|peeringdb|none`). Фикс + тест на
   реальном ASN с известным сайтом (мок RDAP/PeeringDB ответов).

**Тесты**: pytest — резолвер (мок DNS), обогащение (мок ip-api), website
(мок RDAP); vitest — селектор UA, рендер IP-блока.

## PR-5. Настройки/AI: метод входа

- В «Встроенный ИИ-агент» (AiSettingsTab) — селектор «Метод входа»:
  `API-ключ` / `OAuth`.
- OAuth: форма «Вход через CLIProxyAPI (OAuth)» на месте поля API-ключа:
  - селектор провайдера: Claude, Codex (OpenAI), xAI (Grok), Kimi,
    Antigravity (из `OAUTH_ENDPOINTS` backend'а);
  - «Получить ссылку входа» → `POST /api/cliproxy/oauth/start` → ссылка
    (открыть в новой вкладке) + поле «Вставьте redirect URL или code» →
    `oauth/callback`; поллинг `oauth/status?state=` до `ok|error`;
  - статус подключённых аккаунтов (`GET /api/cliproxy/accounts`).
- Backend готов полностью — только фронтенд + выбор провайдера сохраняется в
  AiConfig (`provider` расширяется до union: openai|anthropic|xai|kimi|
  antigravity|codex — совместимо с `provider_defaults`).

**Тесты**: vitest — переключение метода (api-ключ не отправляется при OAuth),
oauth-flow (мок start/callback/status).

## PR-6. Мосты (новый раздел «Управление → Мосты»)

**Привилегии**: новый домен `bridges` в `services/permissions.py::DOMAINS`
(роль-миграция: суперпользователь получает автоматически).

**Модель** (утверждено A): мост = outbound к ноде-выходу + routing-правило в
config-профилях Remnawave.

**Backend** (`services/bridges.py` + `api/bridges.py`, хранилище
`bridges.json` per account):

1. Служебный пользователь `nai-bridge` (один на панель; создаётся при первом
   мосте): `POST /api/users {username, expireAt: "2099-12-31", status:
   "ACTIVE", trafficLimitBytes: 0, trafficLimitStrategy: "NO_RESET",
   activeInternalSquads: [<все активные>]}`. uuid/shortUuid храним.
2. Outbound ноды-выхода: `GET /api/sub/{shortUuid}` с UA, отдающим XRAY_JSON
   (или `/{clientType}`), парсим JSON, находим outbound, чей адрес/тег
   соответствует выбранной ноде-выходу (матч по address+port, fallback —
   remark ноды).
3. Запись в профили: для каждого выбранного профиля
   `GET /api/config-profiles/{uuid}` → в `config`:
   - `outbounds`: добавить `{...bridgeOutbound, tag: "bridge-<id>"}`
     (идемпотентно: заменить существующий с тем же тегом);
   - `routing.rules`: в НАЧАЛО — правило:
     `{ inboundTag: [<выбранные инбаунды>], domain?, ip?, port?, network?,
     protocol?, outboundTag: "bridge-<id>", ruleTag: "nai-bridge-<id>" }`
     (только документированные поля RuleObject; `type` НЕ добавляем);
   - `PATCH /api/config-profiles {uuid, config}`.
4. Удаление моста: из всех профилей убрать `outbounds[tag=bridge-<id>]` и
   правила `ruleTag=nai-bridge-<id>`. Редактирование = удалить + создать по
   тому же id.
5. Селекторы: ноды — `GET /api/nodes`; инбаунды ноды — из её конфиг-профиля
   (`GET /api/config-profiles/{uuid}/inbounds`, также общий список
   `GET /api/config-profiles/inbounds`).

**Форма** (модалка): имя; ноды-входы (селектор) → их инбаунды (мультиселект);
нода-выхода (селектор); конфиг-профили (мультиселект); параметры
маршрутизации: `domain[]` (textarea, по строке на домен, поддержка
`domain:/full:/regexp:/keyword:/geosite:`), `ip[]`, `port`, `network`,
`protocol[]` (чекбоксы http/tls/quic/bittorrent). Подсказка: пустые матчеры =
весь трафик выбранных инбаундов.

**Тесты**: pytest — сборка outbound/правила, идемпотентность повторной
записи, удаление по тегам, NO_RESET/2099 у служебного юзера; vitest — форма,
валидация, список.

**Unconfirmed (Role.txt)**: поле `type` правила — не упомянуто в актуальной
документации xtls, не используем.

## PR-7. Авто — конфигуратор XRAY_JSON-шаблонов

Новый раздел «Управление → Авто» (домен привилегий `configs` — существующий).

**Структурный редактор** XRAY_JSON subscription template (секции-аккордеоны):
- `dns`: servers[], queryStrategy (из xtls-доков);
- `routing`: domainStrategy, domainMatcher, rules[] (тот же набор
  документированных полей RuleObject, что в Мостах — переиспользуем UI-
  компонент правила), balancers[] (tag, selector[], strategy.type,
  fallbackTag);
- `inbounds[]` / `outbounds[]`: protocol + settings/streamSettings как
  вложенный raw-JSON (предустановленные скелеты: vless/trojan/shadowsocks/
  freedom/blackhole/socks/http — только документированные протоколы);
- `burstObservatory`: pingConfig + subjectSelector (по docs.rw);
- **`remnawave`-директива**: injectHosts[] — selector (uuids / remarkRegex /
  tagRegex / sameTagAsRecipient), selectFrom (HIDDEN/NOT_HIDDEN/ALL), ровно
  одно из tagPrefix/useHostRemarkAsTag/useHostTagAsTag (радио);
  addVirtualHostAsOutbound (чекбокс). Валидация «ровно одно tag-поле».
- Переключатель «Форма ↔ JSON»: raw-режим — textarea с JSON-валидацией;
  двусторонняя синхронизация (форма → JSON мгновенно; JSON → форма при
  переключении, ошибки парсинга — inline).
- Сохранение: выбор существующего шаблона (GET list) или «Новый» →
  `create_subscription_template(name, "XRAY_JSON")` →
  `update_subscription_template(uuid, json)`. Подсказка: назначение
  виртуальному хосту — в «Страницах подписок».

**Тесты**: vitest — генерация JSON из формы (эталонный документ docs.rw),
валидация tag-правил, raw↔форма синхронизация.

## Риски

- **Мосты**: формат XRAY_JSON-подписки служебного юзера зависит от шаблона
  подписки панели — outbound может не совпасть с ожиданиями (матч по
  address+port + fallback remark; если не нашли — понятная ошибка с
  инструкцией включить XRAY_JSON-шаблон).
- **Мосты**: правка config-профиля затрагивает ВСЕХ пользователей профиля —
  в форме явное предупреждение + список затронутых профилей.
- **SSL-скан**: команды read-only, но grep по /etc может быть долгим на
  больших конфигах — таймауты и ограничение глубины.
- **2ip.io**: доступность/ключ не подтверждены → основной провайдер ip-api.com
  (без ключа), 2ip.io опционально.
