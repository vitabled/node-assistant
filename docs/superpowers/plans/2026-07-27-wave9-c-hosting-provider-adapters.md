# Волна 9 · План C — адаптеры API хостинг-провайдеров для инфра-биллинга

> Продвинутый промпт: «Инфра-биллинг: добавь поддержку api этих хостинг-провайдеров: VK Cloud, Yandex Cloud,
> Reg.ru (cloudvps), RuVDS (прикреплён файл `ruvds-api-v2.yaml`), Veesp, Beget, Procloud (OpenStack), Oracle».
>
> Уточнено (Q&A): глубина — **баланс + список услуг** (+ платежи/расход там, где API их и так отдаёт);
> креды — **в «Хранилище»** (План A); обновление — **по открытию + кнопка + фоновый луп**.

## Контекст — как есть (проверено по коду)

- `services/infra_billing_store.py` — per-account SQLite `accounts/<id>/infra_billing.db`.
  `provider_meta(provider_uuid PK, balance, currency, low_balance_threshold, api_token_id, status)` — **баланс
  вводится руками**, `status ∈ active|auth_error|unknown`.
- `api/infra_billing.py` — `GET /dashboard/summary` считает total balance, burn-rate, days-left, critical<7 из
  ЭТОГО `provider_meta.balance`. Значит: **достаточно записать в существующее поле `balance`, и вся аналитика/
  уведомления заработают без изменений**.
- Волт `api_tokens(id, name, provider_kind='generic', secret_enc BLOB)` + `POST /api-tokens/{id}/verify` —
  «best-effort, секрет расшифровывается, но per-hosting адаптера нет» (CLAUDE.md §4c, раздел «Not implemented»).
  Этот план и есть закрытие того стаба.
- `services/net_guard.py::is_safe_url` — SSRF-гард; нужен **только там, где base_url вводит пользователь**
  (OpenStack `auth_url`, Veesp/Reg.ru — фиксированные хосты).
- `services/worker_lease.py::MONITORING` — гейт фоновых лупов (5 лупов уже под ним), `main.py` lifespan.
- `cryptography` уже в зависимостях (Fernet) → **RSA-подпись для Oracle и PS256-JWT для Yandex делаются без
  новых пакетов**. `httpx` уже есть. **Новых pip-зависимостей план не требует** (никаких `oci`/`openstacksdk`/
  `boto3` — они тянут десятки транзитивных пакетов ради 2-3 запросов).

## Матрица возможностей (проверено 2026-07-27; ⚠️ = уточнить в Ф0)

| Провайдер | `kind` | Авторизация (поля) | Баланс | Услуги | Платежи/расход |
|---|---|---|---|---|---|
| **RuVDS** | `ruvds` | `token` (Bearer; Basic убран в 2.23) | ✅ `GET /v2/balance` → `{amount, currency, type}` | ✅ `GET /v2/servers` (+`/{id}/cost`, `/paid_till`) | ✅ `GET /v2/payments` → `{dt, direction 1=приход/2=списание, amount, currency}` |
| **Beget** | `beget` | `login`, `password` | ✅ `GET /api/user/getAccountInfo` → `user_balance`, `user_rate_month` | ⚠️ shared-хостинг ≠ VPS; VPS — отдельный модуль `/api/vps/getList` | ❌ |
| **Veesp** | `veesp` | `email`, `password` (Basic или JWT `POST /login`) | ✅ `GET /balance` | ⚠️ имя ручки списка VPS не задокументировано на обзорной странице | ✅ `GET /invoice` |
| **Reg.ru CloudVPS** | `regru_cloudvps` | `token` (Bearer) | ❌ **в v2-swagger только `/v2/images` и `/v2/plans`**; ⚠️ в v1-доках есть раздел «Биллинговая информация» | ✅ `GET /v1/reglets` | ⚠️ |
| **Reg.ru аккаунт** | `regru_account` | `username`, `password` (POST form-data; или `sig` RSA-SHA512) | ✅ `POST /api/regru2/user/get_balance` | — (домены) | ❌ |
| **Yandex Cloud** | `yandex` | SA-ключ: `service_account_id`, `key_id`, `private_key`(PEM), `folder_id` | ✅ `GET billing/v1/billingAccounts` → `{currency (RUB\|USD\|KZT), balance (строка!), active}` | ✅ `GET compute/v1/instances?folderId=` | ✅ (billing service/sku) |
| **VK Cloud** | `openstack` (дефолт auth_url VK) | `auth_url`, `username`, `password`, `project_id`, `domain` | ❌ **публичного billing API не нашёл** → `None` + пометка в UI | ✅ Nova `GET /servers/detail` | ❌ |
| **Procloud** | `openstack` | то же, `auth_url` вводится вручную | ❌ | ✅ Nova | ❌ |
| **Oracle OCI** | `oracle` | `tenancy_ocid`, `user_ocid`, `fingerprint`, `private_key`(PEM), `region`, `compartment_id` | ❌ **у OCI нет «баланса»** (постоплата/кредиты) → показываем расход за месяц | ✅ `GET iaas/20160918/instances?compartmentId=` | ✅ `POST usageapi/20200107/usage` |

**Ключевые детали авторизации (нужны для кода):**
- **Yandex:** JWT `alg=PS256` (единственный), `kid=key_id`, claims `iss=service_account_id`,
  `aud=https://iam.api.cloud.yandex.net/iam/v1/tokens`, `iat`, `exp ≤ iat+3600` → `POST .../iam/v1/tokens
  {"jwt": …}` → IAM-токен живёт **до 12 ч** (обновлять раз в час). Хосты: `billing.api.cloud.yandex.net`,
  `compute.api.cloud.yandex.net`.
- **OpenStack/VK:** `POST {auth_url}/v3/auth/tokens` (password + scope project) → токен в **заголовке
  `X-Subject-Token`**, дальше во всех запросах `X-Auth-Token`; эндпоинты сервисов берём **из service catalog
  ответа**, а не хардкодим (у VK Москва это `infra.mail.ru:8774/v2.1` для Nova).
- **Oracle:** подпись draft-cavage: `Signature keyId="{tenancy}/{user}/{fingerprint}",algorithm="rsa-sha256",
  headers="(request-target) date host",signature="…"` (для POST добавить `x-content-sha256 content-type
  content-length`). **Расхождение часов >5 минут → 401** — записать в квирки.
- **Reg.ru Рег.API 2:** только `POST` form-data, «query string parameters are disallowed».

## Реализация

### Ф0 — разведка (перед кодом; закрывает ⚠️ в матрице)

`scripts/probe_hosting_apis.py` — по одному провайдеру за раз, креды из env, вывод **без секретов**
(`sys.stdout.reconfigure(utf-8)`). Задачи: (1) точное имя ручки списка VPS у Veesp и Beget; (2) есть ли баланс в
CloudVPS v1 («Биллинговая информация»); (3) конверт ответа Beget (`{status, answer:{status, result}}` —
подтвердить); (4) фактические коды при неверных кредах (для маппинга в `auth_error`). Результат — таблицу
дописать в этот файл и CLAUDE.md. **Провайдеры без доступных кредов у пользователя пропускаются** — их адаптеры
пишутся по документации и помечаются «не проверено вживую».

### Ф1 — вертикальный срез: каркас + RuVDS end-to-end

**`services/hosting_providers/base.py`:**
```python
@dataclass
class CredField:  key: str; label: str; kind: Literal["text","password","textarea"]; required: bool = True
@dataclass
class Balance:    amount: float; currency: str            # None → провайдер не отдаёт баланс
@dataclass
class ServiceItem: id: str; name: str; kind: str; cost: float|None; currency: str; period: str
                   status: str; ip: str = ""; region: str = ""; paid_till: str = ""
class ProviderAdapter(Protocol):
    KIND: str; TITLE: str; FIELDS: list[CredField]; CAPS: set[str]   # {"balance","services","payments"}
    async def verify(creds)  -> tuple[bool, str]          # (ok, human error) — НЕ бросает
    async def balance(creds) -> Balance | None
    async def services(creds)-> list[ServiceItem]
    async def payments(creds)-> list[dict]                # опционально, по CAPS
```
Общие правила для всех адаптеров: `httpx.AsyncClient(timeout=20, follow_redirects=False)`; ошибки **никогда не
эхают креды** (свой `_redact`); 401/403 → `(False, "неверные креды")`; 429 → уважать `Retry-After`/
`ratelimit-reset` (у RuVDS они есть) и вернуть понятную ошибку, а не падать; любое исключение → пустой
результат + текст в `last_error`.

**`services/hosting_providers/registry.py`** — `ADAPTERS: dict[kind, ProviderAdapter]`, `schemas()` (отдаётся во
фронт и в `GET /api/vault/schemas` из Плана A, чтобы форма кредов строилась сама).

**`services/hosting_providers/ruvds.py`** — первый адаптер (файл спеки лежит у пользователя, всё подтверждено):
balance/services/payments по таблице выше.

**Проводка в инфра-биллинг:**
- `infra_billing_store`: **идемпотентные `ALTER TABLE`** (приём из `metrics_store`/`server_monitor`) —
  `provider_meta.adapter_kind TEXT DEFAULT ''`, `vault_entry_id TEXT DEFAULT ''`,
  `balance_synced_at INTEGER DEFAULT 0`, `last_error TEXT DEFAULT ''`. Старый `api_token_id` **не удаляем**
  (совместимость), новые подключения пишут `vault_entry_id`.
- `POST /api/infra-billing/providers/{uuid}/sync` → `read_fields(vault_entry_id)` → `verify` → `balance` →
  запись в `provider_meta.balance/currency/status/balance_synced_at/last_error`; ответ отдаёт и `services` (не
  персистим — это живой список; персистить услуги = вторая копия правды).
- `POST /api/infra-billing/providers/{uuid}/import-services` — **опционально**, по кнопке: создать локальные
  `services` из списка провайдера (дедуп по `node_uuid`/имени). Автоимпорта нет: пользователь мог уже завести
  услуги руками.
- `GET /api/infra-billing/adapters` — список `kind/title/поля/caps` для селектора.

**Фронт (в этой же фазе, чтобы срез был рабочим):** `infra/InfraProviders.tsx` — у провайдера селектор
«Адаптер API» + выбор записи Хранилища (`VaultPicker` из Плана A, `kind=provider_creds`) + кнопки «Проверить»/
«Синхронизировать»; в строке — `balance_synced_at` («обновлено N мин назад»), `last_error` (amber),
бейдж «баланс вручную» для адаптеров без `balance` в CAPS. `InfraDashboard` — плашка «часть балансов вручную»,
если такие есть.

**Verify Ф1:** `test_hosting_providers.py` — маппинг ответа RuVDS из **записанной фикстуры** (никаких живых
вызовов в тестах), 401→`auth_error`, 429→понятная ошибка, `_redact` не пропускает токен в текст;
`test_infra_billing.py` +`/sync` (мок адаптера) + идемпотентность ALTER TABLE (дважды `_ensure_schema` на одном
файле) + `dashboard/summary` продолжает считать из `balance`.

### Ф2-Ф5 — адаптеры по одному (каждый = один файл + фикстура + тест)

| Фаза | Адаптер | Особенности реализации |
|---|---|---|
| **Ф2** | `beget.py`, `veesp.py` | Оба на **логине+пароле аккаунта** (не токен!) → в UI amber-плашка «это пароль от личного кабинета». Veesp: Basic проще JWT — берём Basic. Конверт Beget — двойной (`answer.result`) |
| **Ф3** | `regru_cloudvps.py` + `regru_account.py` | Два `kind` на одного вендора: серверы по Bearer, баланс — через Рег.API 2 (POST form-data). В UI — подсказка, что для полной картины подключают обе записи |
| **Ф4** | `yandex.py` | PS256-JWT (`cryptography`: `padding.PSS`, `hashes.SHA256`) → IAM-токен, **кэш в памяти на 55 мин** по `service_account_id`. `balance` — строка → `float()` c try. Мультибиллинг-аккаунт: берём первый `active`, остальные — в ответе |
| **Ф4** | `openstack.py` (`kind=openstack`, пресеты VK/Procloud) | Keystone v3 → `X-Subject-Token`, эндпоинты **из service catalog**. `auth_url` пользовательский → **`net_guard.is_safe_url` обязателен** (и при сохранении, и на каждом запросе — DNS-rebinding, как в `nodeflow_client`). `balance()` → `None` |
| **Ф5** | `oracle.py` | Своя подпись (RSA-SHA256 над строкой `(request-target)\ndate\nhost`), `date` в RFC 1123 GMT; usage через `POST usageapi/.../usage` за текущий месяц → отдаём как `spend_month`, а не `balance`. Самый тяжёлый — делать последним |

### Ф6 — фоновая синхронизация

**`services/provider_sync.py`:** `loop()` — гейт `worker_lease.MONITORING` (как `poller_loop`), per-account с
**явным `account_id`** (у лупа нет request-контекста → `read_fields(..., account_id)` и
`infra_billing_store(..., account_id)`), интервал из существующей billing-настройки `refresh_interval`
(дефолт 15 мин, минимум 5), тумблер `auto_sync` в `billing_settings`. Per-provider try/except → `last_error`,
луп не умирает; провайдер с `auth_error` **не долбим каждый тик** (backoff ×2 до 6 ч).
Регистрация в `main.py` lifespan рядом с прочими лупами.
⚠️ **Смена посыла:** до этого плана внешние API дёргались только по действию пользователя. Теперь панель
регулярно ходит чужими кредами → в CLAUDE.md записать, что это осознанно, и что тумблер выключает поведение.

Уведомление о низком балансе: `infra_notify` уже вызывается из `dashboard/summary`; после Ф6 порог
срабатывает и без открытого браузера (собственно, ради этого фоновый луп и выбран).

### Ф7 (опционально) — CF как провайдер

После Плана B: `hosting_providers/cloudflare.py` — `balance()` через `cf_client.billing_profile`, креды —
`CloudflareConfig` (а не волт: у CF одно подключение на аккаунт, см. правило размещения секретов в Плане A).
Даёт CF в общий total/burn-rate инфра-биллинга без дублирования вкладок.

## Верификация

- **Ни одного живого вызова в тестах.** Для каждого адаптера — записанная фикстура (`tests/fixtures/
  hosting/<kind>_*.json`) и тест маппинга; отдельно pure-тесты «строителей»: тело Keystone-запроса, claims+alg
  JWT Yandex (проверить подпись публичной частью сгенерированного ключа), **строка подписи и сама подпись
  Oracle** (верифицируется публичным ключом — так ловится опечатка в порядке заголовков), form-data Рег.API 2.
- `test_provider_sync.py`: луп не падает на упавшем провайдере; backoff после `auth_error`; explicit-account_id
  путь (без ContextVar) — регресс из §10d.
- Фронт: `tsc` + vitest `InfraProviders.test.tsx` (селектор адаптера, «Синхронизировать» шлёт uuid, ошибка
  рисуется). Docker `ni-frontend-test`, `--maxWorkers=2`, сверять число файлов (§11g).
- **Живьём** — только теми кредами, что даст пользователь; в CLAUDE.md отметить по каждому адаптеру
  «проверено вживую / только по документации» (не выдавать второе за первое).

## Критерии готовности

1. У провайдера в инфра-биллинге можно выбрать адаптер + запись Хранилища, нажать «Проверить» (ok/ошибка) и
   «Синхронизировать» → баланс и валюта подтягиваются, `dashboard/summary` считает total/burn-rate из них.
2. Провайдеры без API-баланса (VK, Procloud, Oracle) честно помечены в UI, ручной ввод не сломан.
3. Список услуг провайдера виден и **по кнопке** может быть импортирован в локальные услуги.
4. Фоновый луп обновляет балансы, выключается тумблером, не падает на битых кредах, уважает лимиты.
5. Ни один секрет не появляется в логах/ответах/сообщениях об ошибках (тест `_redact` на каждый адаптер).
6. `pytest` зелёный (кроме известного пре-существующего фейла `test_haproxy`), `tsc` чисто, новых pip-зависимостей нет.
7. CLAUDE.md: § с матрицей возможностей, деталями авторизации (PS256/Keystone-catalog/OCI-подпись+skew),
   новыми колонками `provider_meta` и статусом проверки каждого адаптера.

## Открытые мелочи (дефолты)

- Управление ресурсами (создать/удалить/перезапустить сервер) — **вне плана** (выбран уровень «баланс + услуги»).
  Адаптеры спроектированы расширяемо: добавить `power_action` позже можно, не ломая интерфейс.
- Конвертация валют — существующие ручные `fx_rates` в billing-настройках; автокурс не вводим.
- Reg.ru `sig` (RSA-SHA512 вместо пароля) — только если пользователь попросит: пароль в волте уже работает.
- Oracle Instance Principal / OKE workload identity — не применимо (мы не в OCI).
