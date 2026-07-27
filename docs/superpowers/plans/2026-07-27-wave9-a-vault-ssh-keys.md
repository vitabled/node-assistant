# Волна 9 · План A — «Хранилище» секретов + SSH-авторизация по ключу

> Продвинутый промпт: «В группе разделов "Справка" добавь раздел "Хранилище", где можно будет хранить
> пароли/ключи от разных ресурсов, в основном api ключи и ssh ключи/пароли».
>
> Уточнено (Q&A): **менеджер + автоподстановка** — записи можно раскрывать/копировать И выбирать в формах;
> **key-auth добавляем по-настоящему** (весь проект сейчас умеет только SSH-пароль); креды хостинг-провайдеров
> (План C) живут **в этом же Хранилище**.

## Контекст — как есть (проверено по коду)

- **SSH — ТОЛЬКО пароль.** `services/ssh_manager.py:36-42` → `asyncssh.connect(..., password=self.password)`;
  `models/deploy.py:13` → `ssh_password: str = Field(..., min_length=1)` (обязателен!). Приватных ключей нет нигде.
- **Масштаб ssh-полей:** `ssh_password` встречается в **20 backend-файлах** (61 вхождение): модели
  `models/deploy.py`, `models/panel_deploy.py` + инлайн-модели в `api/{backup,certs,certwarden,migrate,netbird,
  panel_deploy,node_ops,panel_metrics,panel_sync,replace_domain,speedtest,stats,subpages,testservers,
  xray_checker}.py` и сервисы `pipeline.py`/`panel_pipeline.py`/`panel_sync.py`. На фронте — **23 файла**
  (101 вхождение).
- **Волты, которые уже есть (3 разных паттерна):**
  - `services/rules_store.py` — `put_secret(plaintext)->ref` / `read_secret(ref)` / `delete_secret(ref)`,
    Fernet, SQLite `accounts/<id>/rules_secrets.db`. **Опаковый ref — ровно та модель, что нужна.**
  - `services/infra_billing_store.py` — таблица `api_tokens(secret_enc BLOB)` + `_mask()`, **одна строка-секрет**
    на запись. Плюс задокументированный module-scoped override «секреты at-rest можно».
  - `AppSettings.*_enc` (mcp/ai/haproxy/auto_backup/cliproxy) — Fernet-строка в `settings.json`, наружу только
    `has_token`. Ключ Fernet везде один: `base64(sha256(settings.encryption_key))`.
- **Правило, которое мы осознанно ослабляем:** «SSH-креды не хранятся на сервере — передаются на каждый запрос»
  (README, CLAUDE.md §7d). Пользователь явно попросил хранилище → override, как в §4c для infra-billing.
- **Экспорт:** `services/export_service.py` умеет 15 per-account **JSON**-сторов и **стрипает секреты по
  умолчанию** (зануляет `*_enc`); SQLite-сторы в экспорт НЕ входят (отложено, §5).
- **Группа «Справка»** = `Sidebar.tsx:71-76` `HOSTINGS_TABS` (Карта хостингов / Хостинги / Анализ подписки /
  Библиотека), `Tab`-юнион `Sidebar.tsx:11-20`, крошки `App.tsx:85-115` `CRUMB`, рендер `App.tsx:298-300`.

## Зафиксированные решения

1. **Стор — JSON, а не SQLite.** `accounts/<id>/vault.json`, атомарная запись + `threading.Lock` (паттерн
   `hostings_store`). **Почему:** (а) `export_service` работает только с JSON-сторами и **сам зануляет поля
   `*_enc`** → назвав поле `fields_enc`, мы бесплатно получаем экспорт/импорт и автобэкап-в-Telegram со
   стрипом секретов; (б) объём — десятки записей, SQLite не нужен; (в) нет схемы → нет миграций.
2. **Секрет записи — ОДИН Fernet-блоб над JSON-объектом полей**, не строка. Причина: креды провайдеров (План C)
   требуют 2-5 полей (Oracle — 5, OpenStack/VK — 5, Beget — логин+пароль), и делать по записи на поле — мусор.
   Один блоб = один путь расшифровки.
3. **Приватные SSH-ключи в браузер НЕ отдаём при автоподстановке** — форма получает `ssh_key_ref` (opaque id),
   ключ достаёт бэкенд. **Отклонение от «автоподстановки» и почему:** `DeployCard.savedForm` (и `panel_jobs`)
   **персистится в localStorage** → подставленный ключ навсегда лёг бы plaintext'ом в браузер, что хуже
   статус-кво. Пароли и API-ключи подставляются значением (как выбрано), ключи — по ref. Кнопка «Показать» в
   самом Хранилище ключ отдаёт (это осознанное действие пользователя, не автоподстановка).
4. **Правило размещения секретов в проекте (записать в CLAUDE.md):** один секрет на модуль → `AppSettings.*_enc`
   (mcp/ai/haproxy/cloudflare); много однотипных пользовательских секретов → **Хранилище**.

## Реализация

### Ф1 — стор + API

**`services/vault_store.py`** (новый):
```python
Entry = {id, name, kind, resource, username, note, tags[], fields_enc, created_at, updated_at, revealed_at}
kind ∈ {api_key, ssh_password, ssh_key, login, provider_creds, note}
```
- `fields_enc: str` — base64 Fernet-шифротекст JSON-объекта `{field: value}`. Для `ssh_key` —
  `{private_key, passphrase}`; для `login` — `{username, password}`; для `provider_creds` — схема из
  `hosting_providers.registry` (План C); для `api_key` — `{token}`.
- Публичное API: `list_entries(account_id=None)` (**без секретов** — отдаёт `field_names`, `hint` через
  `_mask`, `has_secret`), `get_entry`, `create/update/delete`, **`read_fields(entry_id, account_id)->dict|None`**
  (внутренний резолв — им пользуются ssh-auth и адаптеры провайдеров), `touch_revealed`.
- Лимиты (дублируются на клиенте): ≤500 записей, `name` ≤80, `resource` ≤200, суммарный секрет ≤64 KiB
  (ed25519-ключ ~400 Б, RSA-4096 ~3 КБ — с запасом), ≤10 тегов по ≤24 симв. (нормализация как
  `HostingBody.tags`: `" ".join(raw.split())`).
- Ошибки расшифровки (сменили `ENCRYPTION_KEY`) → `read_fields` возвращает `None`, а `list_entries` помечает
  запись `broken: true`. **Не бросать** — иначе один битый секрет ломает всю страницу.

**`api/vault.py`** (под `require_account`):
- `GET /api/vault` — список без секретов. `POST /api/vault`, `PUT /api/vault/{id}`, `DELETE /api/vault/{id}`.
- **`POST /api/vault/{id}/reveal`** — отдаёт plaintext-поля. **POST, а не GET:** id в query/URL уходит в
  access-логи nginx и историю браузера, а тело — нет; плюс GET подвержен префетчу/спекулятивной загрузке.
  Пишет `revealed_at` (аудит «когда последний раз смотрели»), в лог — только id, никогда значение.
- `GET /api/vault/{id}/download` — для `ssh_key`: `application/octet-stream` + `Content-Disposition:
  attachment` + `X-Content-Type-Options: nosniff` (паттерн §11h «непрозрачная загрузка» — файл никогда не
  рендерится на нашем origin).
- `GET /api/vault/schemas` — описания наборов полей по `kind` (фронт строит форму динамически; План C
  докладывает туда схемы провайдеров).

**Verify Ф1:** `backend/tests/test_vault.py` — CRUD; **изоляция по аккаунтам**; `fields_enc` в файле не содержит
plaintext (grep по байтам файла); список НЕ содержит секретов; reveal возвращает поля и двигает `revealed_at`;
лимиты (501-я запись → 400, секрет >64 KiB → 422, имя >80 → 422); битый Fernet → `broken`, а не исключение;
download отдаёт `nosniff`+`attachment`.

### Ф2 — SSH-авторизация по ключу (ядро)

**`services/ssh_manager.py`:** `SSHSession.__init__(..., private_key: str = "", key_passphrase: str = "")`;
в `connect()` — если `private_key`, то `client_keys=[asyncssh.import_private_key(private_key, key_passphrase or None)]`
и `password=None`, иначе как раньше. Ошибку `asyncssh.KeyImportError` → своё понятное сообщение
(«ключ не распознан / неверный пароль ключа»), **без содержимого ключа в тексте ошибки**.

**`models/ssh_creds.py`** (новый, миксин):
```python
class SshCreds(BaseModel):
    ip: str; ssh_user: str = "root"
    ssh_password: str = ""          # было Field(..., min_length=1)
    ssh_key_ref: str = ""           # id записи Хранилища (kind=ssh_key)
    @model_validator(mode="after")  # ровно один способ обязателен
    def _auth_present(self): ...     # ни пароля, ни ключа → ValueError («укажите пароль или ключ»)
```
⚠️ **Сейчас `ssh_password` обязателен на уровне поля** — ослабление обязательности не должно превратить
«забыл пароль» в 500 на этапе connect: валидатор модели обязан давать тот же 422 с внятным текстом.

**`services/ssh_auth.py`** (новый): `async resolve(req, account_id=None) -> dict` → `{"password": ...}` или
`{"private_key": ..., "key_passphrase": ...}` (читает `vault_store.read_fields(req.ssh_key_ref)`;
ref не найден/битый → `HTTPException(400, "Ключ из Хранилища недоступен")`). Каждый call-site становится
`SSHSession(ip, port, user, **await ssh_auth.resolve(req))` — правка в одну строку.

### Ф3-Ф5 — раскатка по call-site'ам (порядок = риск)

| Фаза | Файлы | Почему в этой фазе |
|---|---|---|
| **Ф3** | `models/deploy.py`, `services/pipeline.py`, `api/node_ops.py`, `api/stats.py` | Главный путь: деплой ноды, операции над компонентами, 5-минутный poll карточки |
| **Ф4** | `models/panel_deploy.py`, `services/panel_pipeline.py`, `api/{panel_deploy,panel_metrics,panel_sync,backup,subpages,replace_domain}.py`, `services/panel_sync.py` | Панель Remnawave: деплой/переменные/бэкап/синк/смена домена |
| **Ф5** | `api/{certs,certwarden,netbird,migrate,speedtest,testservers,xray_checker}.py` | Остальные однократные операции |

Чек-лист приёмки каждой фазы: `grep -rn "ssh_password" backend/app | wc -l` уменьшается ровно на число
переведённых файлов, и **ни один** call-site не остаётся с прямым `password=req.ssh_password`.

⚠️ `panel_sync` релеит бандл между двумя серверами (`download_file`/`upload_file`) — там **две** сессии,
и у каждой свой способ авторизации: `resolve` вызывается дважды с разными payload'ами.

### Ф6 — фронтенд

- **`components/vault/`**: `api.ts` (типы + fetch), `Vault.tsx` (страница), `EntryModal.tsx` (динамическая форма
  по `kind` из `/schemas`), `SecretField.tsx` (reveal-глаз + копирование + «скачать» для ключей).
  Nav: `Tab "vault"` в `HOSTINGS_TABS` (иконка `KeyRound` или `Lock`), `CRUMB["vault"] = ["Справка","Хранилище"]`,
  маршрут в `App.tsx`.
- Плашка-предупреждение (amber, постоянная): секреты зашифрованы ключом `ENCRYPTION_KEY` из `.env`; потеря или
  смена ключа = потеря Хранилища; сделайте резервную копию `.env`. Ссылка на README-раздел «Безопасность».
- **`components/vault/VaultPicker.tsx`** — контролируемый компонент «Взять из Хранилища»: селектор записей,
  отфильтрованных по `kind`. Поведение: `ssh_password`/`api_key`/`login` → `POST /reveal` → значение в поле;
  `ssh_key` → в форму кладётся **только `ssh_key_ref`**, поле пароля дизейблится с подписью «авторизация по
  ключу из Хранилища».
- Встроить пикер в формы (в том же порядке, что бэкенд-фазы): `DeployForm`, `DeployDashboard` («Существующий
  сервер»), `PanelDeployForm`, `PanelManageModal`, `CertsForm`, `DomainsPanel`, `ReplaceDomainModal`,
  `rw/{Backup,Migration,PanelVariables,SyncGroupPanel}`, `settings/{InfraTab,TestServers}`,
  `monitoring/CheckerRegistry`, `stats/SpeedTests`.
- ⚠️ **`savedForm` в localStorage:** при сохранении карточки деплоя из формы вычищать раскрытый пароль? НЕТ —
  это статус-кво (карточка и сейчас хранит пароль, иначе не работали бы retry и poll статистики). Меняем только
  то, что ключ туда не попадает (в `savedForm` едет `ssh_key_ref`). Записать в CLAUDE.md как явное свойство.

**Verify Ф2-Ф6:** `test_ssh_auth.py` (пароль / ключ из волта / ни того ни другого → 422 / битый ref → 400 /
ключ с passphrase — сгенерировать ed25519 через `cryptography`, `asyncssh.import_private_key` офлайн);
`test_vault.py` (см. Ф1); `test_deploy.py` — регресс «нет ни пароля, ни ключа → 422»; `tsc --noEmit` в Docker
`ni-frontend-test`; vitest `Vault.test.tsx` + `VaultPicker.test.tsx` (ключ кладёт ref и НЕ вызывает reveal —
самый важный тест этого плана) с `--maxWorkers=2` и сверкой числа файлов (§11g).

## Критерии готовности

1. Раздел «Справка → Хранилище» создаёт/редактирует/удаляет записи 6 типов; секрет виден только по явному
   «Показать»; в `GET /api/vault` секретов нет ни в одном поле.
2. `vault.json` на диске не содержит plaintext-секретов (проверено grep'ом по файлу в тесте).
3. Деплой ноды проходит **по ключу из Хранилища** — без пароля вообще (проверить на живом VPS, иначе —
   до первого SSH-шага на TEST-NET-1 `192.0.2.1`, как в `tests/e2e/split_smoke.py`).
4. Приватный ключ не появляется ни в `localStorage`, ни в логах задач, ни в тексте ошибок (grep по
   стриму задачи в тесте).
5. Экспорт аккаунта включает `vault.json` **со занулёнными** `fields_enc`; автобэкап с `include_secrets=true`
   — с секретами (сознательно, по конфигу).
6. `pytest` зелёный (кроме известного пре-существующего `test_haproxy.py::test_deploy_reports_images_not_built`,
   §11g), `tsc` чисто.
7. CLAUDE.md: новый § про Хранилище + правило размещения секретов + «SSH теперь умеет ключи» + отмена строки
   «SSH-креды не хранятся на сервере» (теперь: не хранятся, **если пользователь сам не положил их в Хранилище**).
   README — то же в разделе «Безопасность».

## Открытые мелочи (дефолты — не переспрашивать)

- Мастер-пароль/повторная аутентификация перед reveal — **не делаем** (сессия уже аутентифицирована; добавить
  можно позже, если попросят).
- Срок жизни раскрытого значения в UI — прячем обратно через 30 с и по blur вкладки; в буфер обмена не чистим
  (браузер не даёт надёжно).
- Генерация SSH-ключевой пары внутри панели — **не в этом плане** (сначала хранение и использование).
