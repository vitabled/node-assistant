# Волна 5 · План F — Редактор хедеров (HTTP-заголовки)

> Универсальный переиспользуемый key-value редактор HTTP-заголовков. Сейчас заголовки правятся ТОЛЬКО
> через сырой JSON (Xray `streamSettings` в `profiles/ItemModal.tsx`, `xhttp`-суб-блоки в `Hosts.tsx`) —
> ни одного структурного header-редактора нет. Идея 6: один компонент `HeadersEditor` (добавить/удалить/
> переупорядочить пары, валидация имён по RFC 7230 token, пресеты `Host`/`User-Agent`) встроить в три
> точки: (a) Xray-транспорты ws/httpupgrade/tcp (`streamSettings.*.headers`), (b) host-level поля хостов
> Remnawave (`Hosts.tsx` — `host`/`path` + опц. заголовки внутри `xhttp`), (c) опц. заголовки ответа
> подписки/маскировки (Remnawave `subscription-settings.customResponseHeaders`).
> Затрагивает (frontend, новое): `frontend/src/components/common/HeadersEditor.tsx` (+`.test.tsx`),
> `frontend/src/components/common/headers.ts` (валидатор/пресеты). Правки: `profiles/ItemModal.tsx`,
> `Hosts.tsx`, опц. `stats`/`rw`. Backend (сквозная идея 5): `services/http_headers.py` (чистая
> shell-safety валидация header-строк) + расширение `models/hosts.py`; опц. новый роутер
> `api/subscription_settings.py` (проксирование Remnawave `customResponseHeaders`) + методы в
> `remnawave_client.py`. Переиспользует: токен-паттерн, CSS-var токены темы, паттерн `MultiSelect`/DnD
> (`configStore.moveItem`).

## Контекст (как есть)
- **Xray-транспорты** (`profiles/core/types.ts:85-99`): `StreamSettings` держит `wsSettings`/
  `httpupgradeSettings`/`tcpSettings`/`grpcSettings`/`xhttpSettings` как `Record<string,unknown>` под
  `[key:string]:unknown`. Заголовки реально живут в `wsSettings.headers` (ключ `Host`) — парсятся/генерятся
  в `core/links.ts:43,135,269`; `httpupgradeSettings.headers` и `tcpSettings.header.request/response.headers`
  проходят как raw. **Схема их НЕ типизирует** (`schema.ts` моделирует только network/security/tls/reality/
  sockopt/finalmask; остальное — `additionalProperties:true`). Правятся ТОЛЬКО через `JsonBlock`
  «streamSettings (JSON)» в `ItemModal.tsx:123`.
- **Хосты** (`Hosts.tsx:457-497`, `models/hosts.py`): объекта-`headers` НЕТ. Есть строковые `host`
  (`Hosts.tsx:463`) и `path` (`:464`) + сырые JSON-суб-редакторы `xhttp/mux/sockopt/final_mask`
  (`JsonSubConfig`, `:493-496`) — внутри `xhttp` могут лежать headers. Бэкенд `HostTemplateBody`
  (`models/hosts.py:24-38`): `host:str=""`, `path:str=""`, `xhttp:Optional[dict]=None` — **строковые
  host/sni/path НЕ валидируются как shell-safe** (CLAUDE.md §5 «Forward note»: безопасно, т.к. пока только
  хранятся; при будущем deploy-time «apply host template» — надо валидировать перед интерполяцией).
- **Подписка**: интеграции `customResponseHeaders`/`subscription-settings` в backend **НЕТ** (grep пуст).
  Remnawave (api-1.json v2.8.0) держит поле `customResponseHeaders` в `GET/PATCH /subscription-settings`
  (глобальные настройки подписки). `remnawave_client.py` таких методов не имеет.
- **Переиспользуемые атомы**: DnD-реордер уже практикуется (`configStore.moveItem/reorderRules`, splice);
  `motion` в зависимостях; `lucide-react` иконки; тема — CSS-var токены (`--t-hi/--line-soft/--warn`…).
  Per-account хелперы ключей в `auth/store.ts:101-109` (если понадобится персист — по образцу
  `xray_profile_<id>`, но header-редактор состояния сам не персистит — он контролируемый).

## Развилки (закреплены)
- `HeadersEditor` — **чисто контролируемый** компонент (`value: Record<string,string>` / `onChange`),
  БЕЗ собственного localStorage-персиста. Владелец (ItemModal/Hosts/subscription-форма) хранит и
  сериализует. В фоне не переспрашивать: где хранить — решает родитель.
- **Модель данных заголовков — `Array<{name,value}>` внутри редактора** (сохраняет порядок и допускает
  временно-пустые/дублирующиеся имена при вводе), на выходе `onChange` отдаёт нормализованный
  `Record<string,string>` (последнее значение при дубле; пустые имена отброшены). Это матчит форму
  `wsSettings.headers`/`customResponseHeaders`.
- **Валидация имени заголовка** — RFC 7230 token (`^[!#$%&'*+.^_`|~0-9A-Za-z-]+$`), запрет управляющих
  символов и `:` в имени; значение — запрет CR/LF (защита от header-injection). Невалидное имя →
  inline-ошибка, пара НЕ уходит в `onChange`.
- **Пресеты** — выпадашка «Добавить пресет»: `Host`, `User-Agent`, `X-Forwarded-For`, `Accept`,
  `Connection`. Значения-плейсхолдеры, не автозаполняются секретами.
- **Xray-таргет заголовков по транспорту** (условно): `ws`→`wsSettings.headers`;
  `httpupgrade`→`httpupgradeSettings.headers`; `tcp`→`tcpSettings.header.request.headers` (маскировка).
  Для `grpc/xhttp/kcp` — header-редактор скрыт (нет `headers`-объекта; xhttp несёт `host`-строку).
- **Backend shell-safety** — добавить валидатор header-строк в `services/http_headers.py` и подключить к
  `HostTemplateBody` (`host`/`path`/`sni` field_validator, charset-allowlist) — закрывает «Forward note»
  §5 заранее. **НЕ трогаем 14-шаговый пайплайн** (заголовки применяются в существующих step_create_hosts/
  step_remnanode через уже хранимые поля; новых тумблеров в пайплайн не вшиваем).
- **Подписка (опц., за флагом объёма)** — если делаем: новый роутер `api/subscription_settings.py` под
  `require_account`, проксирует Remnawave `GET/PATCH /subscription-settings` (креды панели из настроек
  аккаунта, per-account изоляция). Секреты не храним. Если панель не настроена → 400/404.

## Стратегия
Ф1 (общий `HeadersEditor` + валидатор/пресеты) → Ф2 (встроить в Xray `ItemModal`) → Ф3 (встроить в
`Hosts.tsx` + backend shell-safety) → Ф4 (опц. подписка: backend-прокси + UI) → Ф5 (опц. mihomo).

---
### Ф1 — Общий компонент `HeadersEditor` + валидатор → verify: tsc + unit
- `frontend/src/components/common/headers.ts` (новое): чистые функции
  `isValidHeaderName(name)` (RFC 7230 token), `sanitizeHeaderValue(v)` (отбрасывает CR/LF),
  `rowsToRecord(rows)` / `recordToRows(rec)` (round-trip, стабильный порядок), `HEADER_PRESETS` (список
  `{name, placeholder}`). Без внешних зависимостей.
- `frontend/src/components/common/HeadersEditor.tsx` (новое): контролируемый key-value редактор.
  Props `{ value: Record<string,string>; onChange: (v)=>void; label?; presets?; disabled? }`. Строки с
  полями name/value, кнопки удалить (`Trash2`), переупорядочить (drag-handle `GripVertical` или ↑/↓ по
  образцу `configStore.moveItem`), «+ Заголовок», выпадашка пресетов. Inline-ошибка на невалидное имя.
  Тема — только CSS-var токены (`input`/`btn btn-soft`/`--warn`/`--line-soft`), `motion` для плавного
  add/remove. CSP-self-contained (без внешних ассетов).
- `frontend/src/components/common/HeadersEditor.test.tsx` (новое): валидация имени (token/`:`/пусто),
  запрет CR/LF в значении, round-trip rows↔record (дубли/порядок), add/remove/reorder, пресеты.
- verify: `npx tsc --noEmit` (в docker-билде) + `HeadersEditor.test.tsx`.
---
### Ф2 — Встроить в Xray-редактор (`ItemModal`) → verify: tsc + unit
- `profiles/ItemModal.tsx`: между селектором `network`/`security` и `JsonBlock` «streamSettings (JSON)»
  добавить **условный** `HeadersEditor`, показываемый при `network ∈ {ws, httpupgrade, tcp}`. Пишет в:
  ws→`streamSettings.wsSettings.headers`, httpupgrade→`streamSettings.httpupgradeSettings.headers`,
  tcp→`streamSettings.tcpSettings.header.request.headers` (создать вложенность при отсутствии). Seeding
  из текущего `item.streamSettings`. Raw-`JsonBlock` остаётся как escape-hatch (двусторонняя
  согласованность: правка через редактор отражается в JSON при переоткрытии — как сейчас `JsonBlock`
  re-seed'ится на open).
- Не трогать `core/schema.ts`/`links.ts` (headers по-прежнему проходят как raw-объекты; `additionalProperties`
  их пропускает). При желании — мелкий тест round-trip ws-headers через существующий `links.test.ts`.
- verify: `tsc`; ручной: открыть inbound/outbound с ws → добавить `Host` → сохранить → JSON содержит
  `wsSettings.headers.Host`.
---
### Ф3 — Встроить в `Hosts.tsx` + backend shell-safety → verify: pytest + tsc
- **Frontend** `Hosts.tsx` (вкладка «Расширенные», рядом с `host`/`path`, `:462-465`): добавить
  `HeadersEditor` для заголовков внутри `xhttp` (мапится в `form.xhttp.headers` — опц.-объект). `host`
  остаётся отдельным строковым полем (это значение Host-заголовка соединения, не коллекция). `canSave`
  учитывает валидность заголовков (невалидные не блокируют, но не уходят в payload).
- **Backend** `services/http_headers.py` (новое, чистое): `validate_header_name`/`validate_header_value`
  (charset-allowlist, запрет CR/LF), `HEADER_NAME_RE`. Подключить к `models/hosts.py::HostTemplateBody`:
  `field_validator` на `host`/`path`/`sni` — allowlist безопасных символов (host/sni → hostname-charset
  `[A-Za-z0-9.:_-]`, path → `[A-Za-z0-9._/~%?=&-]`), закрывает §5 «Forward note» до появления deploy-time
  «apply host template». `xhttp.headers` (если добавляем как типизированное) — валидировать имена/значения.
- `backend/tests/test_hosts.py` (расширить): host/path/sni отвергают shell-метасимволы и CR/LF; валидные
  заголовки в `xhttp` проходят; CRUD+изоляция не сломаны.
- verify: `python -m py_compile` + `pytest test_hosts.py`; `tsc` + ручной smoke хоста.
---
### Ф4 (опц.) — Заголовки ответа подписки → verify: pytest + tsc + preview
- **Backend** `remnawave_client.py` (доп. методы): `get_subscription_settings()` (`GET
  /api/subscription-settings` → `response`), `update_subscription_settings(patch)` (`PATCH`,
  тело с `customResponseHeaders`). `api/subscription_settings.py` (новый роутер, под `require_account`,
  зарегистрировать в `main.py` с `dependencies=[Depends(require_account)]`): `GET/POST
  /api/subscription-settings/headers` — читает/пишет только `customResponseHeaders` (креды панели из
  настроек аккаунта; панель не настроена → 400). Секреты не храним.
- **Frontend**: в существующем разделе настроек подписки/маскировки (или новая карточка) — `HeadersEditor`
  над `customResponseHeaders`, submit → `POST`. Мелкий тест `AiChat`-стиля не нужен; проверить рендер.
- `backend/tests/test_subscription_settings.py` (новое): маппинг headers, панель-не-настроена → 400,
  per-account изоляция.
- verify: `pytest` + `tsc` + preview формы.
---
### Ф5 (опц.) — mihomo-заголовки → verify: tsc
- Если План E (`2026-07-21-wave5-e-mihomo-editor.md`) вводит mihomo-редактор, переиспользовать
  `HeadersEditor` для его HTTP-полей (`http-headers`/inbound headers) — без нового кода на backend.
  Иначе фаза пропускается (зависит от готовности E).
- verify: `tsc`.

## РАЗВЕДКА (факты)
- **Xray-транспорты, где живут заголовки** (проверено по коду): `wsSettings.headers.Host`
  (`profiles/core/links.ts:43` vmess, `:135` vless/trojan/ss, генерация `:269`); `httpupgradeSettings`
  объявлен `StreamSettings` (`core/types.ts:93`), несёт `headers`/`host` в Xray, в коде отдельно не
  парсится (raw); `tcpSettings` (`types.ts:90`) — HTTP-маскировка `header.request/response.headers`, только
  raw; `grpcSettings` — БЕЗ заголовков (`serviceName`); xhttp/splithttp — поле `host`-строка, не
  `headers`-объект (`links.ts:137-141`, `generateXrayLink:271`). Схема (`schema.ts`) заголовки НЕ
  типизирует → сейчас правятся только через raw-JSON `JsonBlock` (`ItemModal.tsx:123`).
- **Хосты** (`models/hosts.py:24-38`, `Hosts.tsx:457-497`): нет объекта-`headers`; `host:str`/`path:str` +
  `xhttp:Optional[dict]`; строковые host/sni/path **не** shell-safe-валидированы (CLAUDE.md §5 «Forward
  note»). Remnawave host DTO: заголовки соединения задаются через `host`/`path`/`sni`, RAW-заголовки — в
  `xhttp`/`rawInbound`.
- **Подписка** (Remnawave api-1.json v2.8.0): `GET/PATCH /subscription-settings` содержит
  `customResponseHeaders` (+ `profileTitle`, `responseRules`, `randomizeHosts`…). В нашем backend
  интеграции нет (grep `customResponseHeaders`/`subscription-settings` пуст); `remnawave_client.py` таких
  методов не имеет. Источник: `api-1.json` (локально), `backend/app/services/remnawave_client.py:116-160`
  (есть config-profiles, нет subscription-templates/settings).
- **Инвариант header-injection**: значения заголовков не должны содержать CR/LF (`\r\n`) — иначе inject в
  nginx/Xray-конфиг. Валидатор Ф1/Ф3 это закрывает на клиенте И на сервере.

## Критерии готовности плана F
- `HeadersEditor` — один переиспользуемый компонент (add/remove/reorder/пресеты/валидация имён и значений),
  CSP-self-contained, тема через CSS-var токены; unit-тест зелёный.
- Встроен в Xray `ItemModal` (ws/httpupgrade/tcp → `streamSettings.*.headers`) и в `Hosts.tsx`
  (`xhttp.headers`), round-trip с raw-JSON согласован.
- Backend: `services/http_headers.py` валидатор + shell-safety на `HostTemplateBody` (host/path/sni),
  `test_hosts.py` расширен (метасимволы/CRLF отвергаются).
- Опц. Ф4: `api/subscription_settings.py` + методы `remnawave_client` под `require_account`,
  per-account изоляция, `test_subscription_settings.py`.
- Verify: `pytest` (test_hosts + опц. test_subscription_settings) + `tsc` (docker-билд) + preview обеих
  точек встраивания + ручной smoke (заголовок доезжает в сохранённый конфиг). CLAUDE.md обновить при
  реализации (§2 Hosts, §5 роуты, §8b profiles).
