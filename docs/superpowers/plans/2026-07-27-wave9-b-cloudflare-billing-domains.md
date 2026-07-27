# Волна 9 · План B — группа разделов «Cloudflare»: биллинг подключённого аккаунта + покупка доменов

> Продвинутый промпт: «Добавь группу разделов "Cloudflare". В нём разделы инфра-биллинга подключённого аккаунта
> cloudflare. Добавь раздел "Домены", где можно будет купить новый домен».
>
> Уточнено (Q&A): **зеркало инфра-биллинга + Домены** (отдельная nav-группа, read-only данные из CF API);
> **покупка домена — настоящая**, через CF Registrar API, за двойным confirm с показом цены.

## Контекст — как есть (проверено по коду)

- **Вся CF-интеграция сейчас** — `services/cloudflare.py` (58 строк): `get_zone_id(token, domain)` +
  `upsert_a_record(token, domain, ip)`. Больше ничего.
- **Токен CF** живёт в `DeployDefaults.cloudflare_api_key` (`models/settings.py:37`, **plaintext** в
  `settings.json`) и приходит в форму деплоя. Это **DNS-edit** токен: для `billing/profile` и `registrar` нужны
  ДРУГИЕ права → отдельное подключение, старое поле не трогаем.
- **Конвенция секрета модуля:** `AppSettings.<module>.<name>_enc` = Fernet-строка, ключ
  `base64(sha256(encryption_key))`, наружу только `has_token` (mcp/ai/haproxy/auto_backup — 5 примеров).
- **Как выглядит «подключаемый внешний сервис» в этом проекте** — `HaproxyConfig` + `api/haproxy.py`
  (`GET/POST /config`, `POST /test`) + `haproxy/gate.tsx` (`useHaproxyReady` + `NotConnected`) + connect-UI как
  **вкладка Настроек** (`Settings.tsx` `SubTab "haproxy"` → `<HaproxyConnect/>`). Копируем этот скелет 1:1.
- **Nav:** `Sidebar.tsx` — группы объявляются массивом `NavItemDef[]` + заголовок в JSX (строки 137-166);
  `Tab`-юнион 11-20; крошки `App.tsx:85-115`; рендер-свитч `App.tsx:~290-320`.
- **Инфра-биллинг** (`api/infra_billing.py`, 7 вкладок) — ЛОКАЛЬНЫЙ учёт с ручным вводом балансов. Мы его **не
  переносим** в CF-группу: там свои данные, здесь — живые из CF API. Мостик «CF как провайдер» — в Плане C.

## Что реально умеет CF API (проверено по developers.cloudflare.com, 2026-07-27)

| Возможность | Метод и путь | Отдаёт |
|---|---|---|
| Список аккаунтов | `GET /accounts` | id/name — нужен, чтобы не заставлять вводить `account_id` руками |
| Биллинг-профиль | `GET /accounts/{acc}/billing/profile` | адрес, платёжная информация, **баланс** |
| PayGo-инфо | `GET /accounts/{acc}/paygo-usage-info` | метаданные подписки, покрытие |
| PayGo-расход | `GET /accounts/{acc}/paygo-usage` | биллинговый расход, фильтр по датам |
| Usage v2 | `GET /accounts/{acc}/billable/usage` | **alpha, restricted** — FOCUS v1.3. Не полагаться |
| Подписки | `GET/POST /accounts/{acc}/subscriptions`, `PUT/DELETE /accounts/{acc}/subscriptions/{id}` | `{id, price, currency, frequency (weekly\|monthly\|quarterly\|yearly), state (Trial\|Provisioned\|Paid\|AwaitingPayment\|Cancelled\|Failed\|Expired), rate_plan, current_period_start/end}` |
| Подписка зоны | `GET /zones/{zone}/subscription` | то же для конкретной зоны |
| Поиск имён | `GET /accounts/{acc}/registrar/domain-search` | подсказки по ключевому слову |
| **Проверка + цена** | `POST /accounts/{acc}/registrar/domain-check` | доступность в реальном времени у реестра |
| **Регистрация** | `POST /accounts/{acc}/registrar/registrations` | **billable!** 201 (готово) / 202 (WorkflowStatus: `pending\|in_progress\|action_required\|blocked\|succeeded\|failed`) |
| Мои регистрации | `GET/GET{domain}/PATCH{domain} /accounts/{acc}/registrar/registrations` | срок, auto_renew, privacy |
| Домены Registrar | `GET/PUT /accounts/{acc}/registrar/domains[/{domain}]` | свойства домена |

**Тело `POST /registrations`:** обязателен только `domain_name`; опционально `contacts` (registrant —
обязателен, если у аккаунта нет дефолтного; внутри email, phone, postal_info{city, country_code, postal_code,
state, street} + name/organization), `years` (1-10; по умолчанию минимум реестра), `privacy_mode` (дефолт
`redaction`), `auto_renew` (дефолт false; `true` = **явное разрешение списывать** с дефолтного способа оплаты),
`acknowledgements`, `contact_extensions`.
**Предусловия:** валидный биллинг-профиль с дефолтным способом оплаты, <100 доменов на аккаунте, поддерживаемая
TLD, предварительный `domain-check`.

⚠️ **Истории платежей (ledger) у CF в публичном API НЕТ.** Пользовательские `/user/billing/history` в текущей
документации отсутствуют, account-level аналога не заявлено. Раздел «Платежи» **не выдумываем**: показываем
`paygo-usage` по датам + ближайшие списания из `subscriptions.current_period_end`, и честную плашку «CF не
отдаёт историю платежей через API» со ссылкой в dash.cloudflare.com. Проверить на живом аккаунте в Ф0 — если
ручка найдётся, дозаполнить.

## Реализация

### Ф0 — разведка на живом аккаунте (30 минут, до кода)

Скрипт `scripts/probe_cloudflare.py` (самодостаточный, по образцу `scripts/probe_subpage_config.py`:
`CF_TOKEN=… python scripts/probe_cloudflare.py`, `sys.stdout.reconfigure(utf-8)` — иначе cp1251 роняет вывод):
дёрнуть `/accounts`, `billing/profile`, `paygo-usage-info`, `paygo-usage`, `subscriptions`, `registrar/domains`,
`registrar/registrations`, `POST domain-check` на заведомо занятом и заведомо свободном имени; **записать
фактические формы ответов и коды ошибок при недостатке прав в plan-файл/CLAUDE.md**. `POST /registrations`
**НЕ дёргать** (billable). Заодно выяснить, какие scope'ы токена нужны для каждой ручки (по 403-ответам).

### Ф1 — подключение и клиент

- **`CloudflareConfig`** на `AppSettings`: `{enabled: bool = False, account_id: str = "",
  api_token_enc: str = "", default_contact: dict = {}}`. Токен — Fernet, наружу `has_token`.
  `default_contact` — **PII, не секрет** (имя/email/телефон/адрес регистранта), лежит в `settings.json`;
  помечается в экспорте как PII, но не зануляется (иначе восстановленный аккаунт потеряет контакт).
- **`services/cf_client.py`**: `class CfClient(token)`; `_req(method, path, json=None, params=None)` →
  разворачивает конверт `{result, success, errors[], messages[]}`; ошибка → `CfError(status, detail)` с
  **редактированием токена** в тексте (`telegram.redact`-подобный `_redact`). Хост фиксированный
  (`api.cloudflare.com`) → SSRF-гард не нужен (в отличие от `nodeflow_client`, где base_url пользовательский).
  Методы: `accounts()`, `billing_profile(acc)`, `paygo_info(acc)`, `paygo_usage(acc, start, end)`,
  `subscriptions(acc)`, `zones()`, `registrar_domains(acc)`, `registrations(acc)`, `registration(acc, name)`,
  `patch_registration(acc, name, **f)`, `domain_search(acc, q)`, `domain_check(acc, names)`,
  `register(acc, payload)`.
- **`api/cloudflare.py`** (под `require_account`): `GET/POST /api/cloudflare/config`, `POST /test`
  (accounts + billing/profile — как `haproxy._check`), `GET /accounts`.
  Гейт «не настроено» → 400 с текстом, ведущим в Настройки → «Cloudflare» (как `haproxy._client_or_400`).
- **Кэш:** `_CACHE[(account_id, key)] = (ts, value)`, TTL 15 мин, инвалидация кнопкой «Обновить»
  (`?refresh=1`). Внешние ручки не дёргаем на каждый рендер.

### Ф2 — 4 биллинговых раздела

- `GET /api/cloudflare/billing/summary` → `{profile:{balance, currency, payment_method_present},
  paygo:{covered, plan}, subscriptions_total_monthly, next_charge_at, degraded:[...]}` — **`degraded`** несёт
  список ручек, которые вернули 403/404 (частичный доступ токена), чтобы UI показал «нет прав на X», а не
  пустоту. Ни одна упавшая под-ручка не роняет весь ответ.
- `GET /api/cloudflare/subscriptions`, `GET /api/cloudflare/usage?from&to`, `GET /api/cloudflare/zones`.
- Фронт `components/cloudflare/`: `api.ts`, `gate.tsx` (`useCfReady`+`NotConnected` — копия
  `haproxy/gate.tsx`), `CfOverview.tsx` (баланс/способ оплаты/ближайшее списание/плитки), `CfSubscriptions.tsx`
  (таблица: продукт, цена, период, состояние, следующее списание), `CfUsage.tsx` (paygo по датам, inline-SVG
  как в `stats/`), `CfPayments.tsx` (расход + плашка про отсутствие ledger), `CfConnect.tsx` (форма
  токена + выбор аккаунта из `/accounts` + «Проверить»).
- Nav: группа **«Cloudflare»** после «HAPROXY»; табы `cf-overview`, `cf-subscriptions`, `cf-usage`,
  `cf-payments`, `cf-domains`; иконки `Cloud`, `ReceiptText`, `Gauge`, `CreditCard`, `Globe`.
  Connect-UI — вкладка **Настройки → «Cloudflare»** (`SubTab "cloudflare"`), как у HAProxy: nav-группа остаётся
  чисто операционной.

### Ф3 — раздел «Домены» (включая покупку)

- Роуты: `GET /api/cloudflare/domains` (мои регистрации + срок + auto_renew + privacy),
  `POST /api/cloudflare/domains/search {q}`, `POST /api/cloudflare/domains/check {names[]}`,
  `PATCH /api/cloudflare/domains/{name}` (auto_renew/privacy),
  **`POST /api/cloudflare/domains/register`**.
- **Гейты покупки (все обязательны, иначе 400):**
  1. `confirm: true` в теле (как `backup`/`panel_sync`/`migrate` в этом проекте);
  2. **`expected_price` + `expected_currency` в теле и совпадение с ответом `domain-check`**, снятым сервером
     **в этом же запросе** — если цена у реестра изменилась, покупка отклоняется, а не проходит по новой цене
     (защита от «нажал по устаревшей цене»);
  3. предполётная проверка `billing/profile` на наличие способа оплаты → иначе внятная ошибка вместо 4xx от CF;
  4. `years` 1-10, `domain_name` — валидация FQDN `_DOMAIN_RE.fullmatch` (как `certs.py`), никакой интерполяции
     в shell тут нет, но валидируем ради предсказуемости;
  5. `auto_renew` по умолчанию **false** (CF трактует true как разрешение на будущие списания — не включаем
     молча).
- 202 → вернуть `workflow` и опрашивать `GET /registrations/{name}` (или status-link) до
  `succeeded|failed|action_required`; в UI — прогресс, как у стрим-задач, но без `Task` (это не SSH-операция).
- Фронт `CfDomains.tsx`: поиск/проверка → таблица кандидатов (имя, доступность, цена, срок) → модалка покупки:
  **цена крупно**, срок, privacy, auto_renew (по умолчанию выкл), контакт регистранта (префилл из
  `default_contact`, чекбокс «сохранить как контакт по умолчанию»), **два шага подтверждения** (чекбокс
  «понимаю, что будет списание» + кнопка «Купить за X»). После успеха — запись появляется в «Моих доменах».
- ⚠️ **Платёжные реквизиты мы не собираем и не вводим** — CF списывает со СВОЕГО сохранённого способа оплаты.
  Это соответствует правилу проекта «не вводим платёжные данные»; в UI явно написать, что карта настраивается
  в панели Cloudflare.
- Мостик к остальному проекту: у купленного домена кнопка «Использовать для ноды/панели» → префилл поля
  `domain` в форме деплоя (просто передача строки, без записи DNS: A-запись всё равно поставит `step_ssl`).

## Верификация

- `backend/tests/test_cloudflare.py`: config CRUD + `has_token` + blank-keeps + шифрование at-rest (в
  `settings.json` нет plaintext); разворачивание конверта `{result,success}`; ошибка CF → 502 с
  **отредактированным** токеном; `summary` при 403 на под-ручке → `degraded`, а не 500; **register без
  `confirm` → 400**; **register с расхождением `expected_price` → 400** (мок `domain_check`); register без
  способа оплаты → 400; гейты «не настроено» → 400.
- Фронт: `tsc --noEmit` в Docker `ni-frontend-test`; vitest `CfDomains.test.tsx` (кнопка «Купить» дизейблена
  без чекбокса; в payload уходят `confirm` + `expected_price`), `gate` рендерит `NotConnected` без токена.
  `--maxWorkers=2`, сверять число файлов (§11g).
- **Живьём:** Ф0-скрипт на реальном токене (read-ручки). Покупку домена **не проверяем автотестом** — это
  трата денег; проверка руками пользователем на дешёвой TLD, если захочет.
- CLAUDE.md: новый § «Cloudflare — биллинг и домены» с таблицей ручек, формой `POST /registrations`, гейтами
  покупки и **фактом отсутствия ledger-API**; отметить, что `deploy_defaults.cloudflare_api_key` — отдельный
  DNS-токен и не пересекается с этим модулем.

## Открытые мелочи (дефолты)

- Один CF-аккаунт на аккаунт панели (`account_id` в конфиге). Мультиаккаунт CF — позже, если понадобится
  (тогда — реестр, как `RemnawaveRegistry`).
- Управление зонами/DNS-записями (создать зону, править записи) — **не в этом плане**; раздел «Зоны» read-only
  (список + план + подписка), т.к. запрошен был биллинг.
- Трансфер домена в CF — не в этом плане (`/registrar` умеет, но у покупки и трансфера разные воркфлоу).
- `billable/usage` (alpha/restricted) не используем: сломается при GA-изменениях.
