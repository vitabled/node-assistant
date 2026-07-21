# Волна 5 · План I — Инструкции для ИИ (пресеты промптов) + Cloudflare-agent-setup

> Библиотека системных промптов/инструкций для встроенного ИИ-агента (§8d,
> `services/ai_agent.py`): встроенные пресеты + пользовательские, выбор **активного**
> пресета per-account. Активный пресет заменяет/дополняет захардкоженный `_SYSTEM`,
> с которым агент уже уходит на провайдера. Один из встроенных пресетов —
> **Cloudflare agent-setup** (`https://developers.cloudflare.com/agent-setup/prompt.md`,
> разведка R2), **вендоренный из URL с атрибуцией** (CC-BY-4.0), не переписанный вручную.
> Затрагивает: `services/ai_agent.py` (правка `_SYSTEM`→активный пресет), новый
> `services/prompt_presets_store.py`, новый `api/ai_prompts.py` (роутер под `require_account`),
> расширение `models/settings.py::AiConfig` (поле `active_preset_id`), новый ассет
> `backend/app/assets/prompts/cloudflare-agent-setup.md` + `PRESETS.json`, frontend
> `settings/AiChat.tsx` (+ новый `settings/PromptPresets.tsx`). Переиспользует Fernet-паттерн
> НЕ требуется (промпты — не секреты); хранение — обычный per-account JSON через `storage.py`.
> Сосед по волне: план H (`wave5-h-api-tokens.md`) — токены доступа; план J
> (`wave5-j-opencliproxy.md`) — прокси AI-провайдеров (пресеты ортогональны прокси).

## Контекст (как есть)

- Встроенный ИИ-агент (§8d): `services/ai_agent.py`. Системный промпт — **захардкоженная
  константа `_SYSTEM`** (`ai_agent.py:360-363`, «Ты — ассистент панели node-installer/
  Remnawave. Отвечай кратко по-русски…»). Она используется в ДВУХ местах:
  - OpenAI-путь: кладётся первым сообщением `{"role":"system","content":_SYSTEM}` в
    `run_agent` (`ai_agent.py:379-382`).
  - Anthropic-путь: **top-level `system`** в теле `_anthropic_turn` (`ai_agent.py:260`),
    НЕ в messages (важно — при правке нельзя дублировать в messages).
- `AiConfig` (`models/settings.py:73-83`): `enabled/provider/base_url/model/api_key_enc
  (Fernet)/max_steps/readonly`. Пресетов/системного промпта в модели **нет**.
- `api/ai.py`: `GET/POST /api/ai/config` (ключ write-only, `has_key`; `_public()`
  собирает публичный вид без ключа), `POST /api/ai/chat` (стрим ndjson). Роутер под
  `require_account` (в списке `_auth`-роутеров `main.py`). `AiConfigBody` валидирует
  `provider ∈ {openai,anthropic}`, `max_steps 1..20`.
- Frontend: `components/settings/AiChat.tsx` — конфиг-форма (провайдер/модель/base_url/
  ключ/enabled/лимит шагов) + чат-лог + стрим через `res.body.getReader()`. Живёт как
  вкладка «Ассистент» (`assistant` таб, §9c — вынесен в группу «Автоматизация»).
- Инструменты агента (`TOOLS`, `ai_agent.py:138-159`) — read-only, их спеки уходят
  провайдеру ОТДЕЛЬНО от системного промпта (`_tool_specs_openai`/`_tool_specs_anthropic`);
  на последнем шаге `with_tools=False` (агент синтезирует финал). Пресет влияет ТОЛЬКО
  на системный промпт — механику tool-calling не трогаем.
- Per-account хранение — `storage.py` (единая воронка JSON под `accounts/<id>/`). Ассетов/
  вендоренных файлов в `backend/app/` сейчас НЕТ (директории `assets/` не существует).

## Развилки (закреплены)

- **Хранение промптов — обычный per-account JSON** (`accounts/<id>/prompt_presets.json`
  через `storage.py`), НЕ Fernet-волт: системные промпты — не секреты (в отличие от
  API-ключей). Встроенные пресеты — read-only ассеты в репо, в JSON не дублируются.
- **Активный пресет — один, per-account**, хранится как `AiConfig.active_preset_id`
  (в `settings.json`, рядом с прочей AI-конфигурацией). Пусто/неизвестный id → fallback
  на дефолтный встроенный пресет (= текущий `_SYSTEM`, чтобы поведение не регрессировало).
- **Комбинирование с tool-инструкциями:** активный пресет задаёт ТОЛЬКО системный промпт.
  Спеки инструментов (`TOOLS`) уходят провайдеру независимо и всегда. Чтобы пресет-«чужак»
  (напр. Cloudflare) не сломал работу с нашими read-only-инструментами, к тексту пресета
  **всегда добавляется наш неотключаемый суффикс** — короткая приписка «У тебя есть
  read-only инструменты панели node-installer/Remnawave (list_rules/list_subscriptions/
  node_health/list_nodes); используй их для чтения данных, не выдумывай». Итог:
  `system = <текст активного пресета> + "\n\n" + _TOOLING_SUFFIX`. Суффикс — константа,
  не редактируется пользователем.
- **Cloudflare-пресет вендорится, не переписывается вручную.** Оригинал — неизменяемый
  ассет-файл `backend/app/assets/prompts/cloudflare-agent-setup.md` со шапкой-атрибуцией
  (© Cloudflare, источник-URL, CC-BY-4.0, «indicate if changes were made»). В UI —
  нейтральное имя «Cloudflare platform setup (внешний ассет)», БЕЗ логотипов/бренд-марки
  Cloudflare (trademark-ограничение). Вендоринг — при реализации: `WebFetch` URL →
  сохранить дословно + шапка. Если fetch недоступен — плейсхолдер-файл с TODO и ссылкой,
  пресет помечается `unavailable` (не выдумывать текст промпта).
- **Встроенные пресеты read-only**, их нельзя удалить/переименовать; можно только выбрать
  активным ИЛИ «форкнуть» в пользовательский (копия текста → редактируемый пресет).
  Пользовательские — полный CRUD.
- В фоне не переспрашивать: нет активного пресета → дефолт; неизвестный провайдер у
  пресета → пресет провайдер-агностичен (текст один на оба пути).

## Стратегия

Ф1 (backend: стор пресетов + встроенные ассеты + вендоринг Cloudflare) → Ф2 (backend: API
`/api/ai/prompts` + интеграция активного пресета в `ai_agent._SYSTEM`-путь + `AiConfig`) →
Ф3 (frontend: выбор/редактирование пресетов в «Ассистенте»).

---

### Ф1 — Backend: стор пресетов + встроенные ассеты → verify: pytest + py_compile

- Новый `services/prompt_presets_store.py`:
  - Модель пресета (pydantic): `{id, name, text, builtin: bool, source_url?: str,
    license?: str, unavailable?: bool}`.
  - **Встроенные пресеты** грузятся из репо-ассетов, НЕ из per-account JSON:
    - `backend/app/assets/prompts/PRESETS.json` — манифест встроенных (`default`,
      `cloudflare-agent-setup`, при желании ещё 1-2 «точные команды»/«краткие ответы»).
    - `default` — вынести текущий текст `ai_agent._SYSTEM` в файл/константу как встроенный
      пресет `builtin=True`, чтобы дефолт = сегодняшнее поведение.
    - `cloudflare-agent-setup` → `text` читается из
      `assets/prompts/cloudflare-agent-setup.md` (вендоренный, см. развилку); `source_url`
      + `license="CC-BY-4.0"` + шапка-атрибуция сохраняются; если файл — плейсхолдер →
      `unavailable=True`.
  - **Пользовательские пресеты** — per-account через `storage.py`: добавить
    `load_prompt_presets`/`save_prompt_presets` (envelope `{"presets":[]}`, паттерн
    `load_hosts`/`save_hosts` в `storage.py:77-82`).
  - API стора: `list_presets(account_id)` = встроенные + пользовательские (id встроенных
    зарезервированы, коллизия → пользовательский переименовать/отклонить),
    `get_preset(id)`, `create/update/delete_preset` (только `builtin=False`; попытка
    тронуть встроенный → ошибка), `fork_preset(builtin_id) -> user preset` (копия текста).
  - `resolve_active_text(account_id, active_preset_id) -> str` — вернуть текст активного
    пресета, fallback на `default` при пусто/unknown/unavailable.
- Вендоринг Cloudflare-промпта (при реализации, НЕ в плане): `WebFetch`
  `https://developers.cloudflare.com/agent-setup/prompt.md` → сохранить дословно в
  `assets/prompts/cloudflare-agent-setup.md` с header-комментарием атрибуции; правки под
  наш агент держать ОТДЕЛЬНЫМ пресетом-оверлеем, не редактируя оригинал (CC-BY «indicate
  if changes were made»).
- verify: `test_ai_prompts.py` — встроенные всегда присутствуют; forbid update/delete
  builtin; `resolve_active_text` fallback; per-account изоляция пользовательских;
  `python -m py_compile` изменённых файлов.

---

### Ф2 — Backend: API `/api/ai/prompts` + интеграция активного пресета → verify: pytest

- `models/settings.py::AiConfig` — добавить поле `active_preset_id: str = ""` (пусто =
  дефолт). Расширить `api/ai.py::AiConfigBody` + `_public()` (вернуть `active_preset_id`)
  + `save_config` (сохранять его). `save_config` уже мержит `current.model_dump()` —
  новое поле подхватится, добавить только явную запись.
- Новый `api/ai_prompts.py` (роутер `prefix="/api/ai/prompts"`, зарегистрировать под
  `_auth` в `main.py` рядом с `ai`):
  - `GET /` — список пресетов (встроенные + пользовательские; для встроенных отдавать
    `builtin/source_url/license/unavailable`).
  - `GET /{id}` — текст пресета (для превью/редактора).
  - `POST /` — создать пользовательский (`{name, text}`), 422 при пустых.
  - `PUT /{id}` — обновить пользовательский (builtin → 400).
  - `DELETE /{id}` — удалить пользовательский (builtin → 400).
  - `POST /{id}/fork` — форкнуть встроенный в редактируемый пользовательский.
  - (активный выбирается через существующий `POST /api/ai/config` полем
    `active_preset_id` — отдельный эндпоинт не нужен.)
- Интеграция в агента (`services/ai_agent.py`) — **ключевая правка, аккуратно с двумя
  путями**:
  - Ввести `_TOOLING_SUFFIX` (константа) + функцию `build_system(account_id, cfg) -> str`
    = `resolve_active_text(...) + "\n\n" + _TOOLING_SUFFIX`.
  - OpenAI-путь: в `run_agent` заменить `{"role":"system","content":_SYSTEM}`
    (`ai_agent.py:380`) на `build_system(...)`.
  - Anthropic-путь: `_anthropic_turn` берёт `system=_SYSTEM` (`ai_agent.py:260`) —
    пробросить готовый `system`-текст в `_anthropic_turn`/`_provider_turn` (добавить
    параметр `system: str`), НЕ читать глобальную константу. НЕ класть system в messages.
  - `_SYSTEM` оставить как текст встроенного `default`-пресета (single source), чтобы
    Ф1-манифест и агент ссылались на один текст.
  - Никаких изменений в `TOOLS`/`_tool_specs_*`/последний-шаг-tools-off — механика
    инструментов неизменна (пресет влияет только на system).
- verify: `test_ai.py` (расширить) — активный пресет попадает в system обоих провайдеров
  (замокать `_provider_turn`, проверить переданный system); суффикс инструментов всегда
  присутствует; fallback при пустом `active_preset_id`; `test_ai_prompts.py` CRUD/gating.

---

### Ф3 — Frontend: выбор/редактирование пресетов в «Ассистенте» → verify: tsc + preview

- Новый `components/settings/PromptPresets.tsx` — секция в `AiChat.tsx` (или отдельный
  под-блок вкладки «Ассистент»):
  - `<select>` «Активный пресет» (список из `GET /api/ai/prompts`) → на смену пишем
    `active_preset_id` через существующий `POST /api/ai/config` (переиспользовать `save()`
    из `AiChat.tsx`, добавив поле в тело).
  - Превью текста активного пресета (read-only textarea) + бейдж источника/лицензии для
    встроенных (для `cloudflare-agent-setup` — «внешний ассет · CC-BY-4.0» + ссылка на
    источник; НЕ бренд-логотип).
  - Кнопки: «Форкнуть в свой» (для встроенного → `POST /{id}/fork`), «Создать пресет»,
    «Редактировать»/«Удалить» (только для пользовательских). Редактор — textarea + имя.
  - `unavailable`-пресет (Cloudflare без fetch) — показать дизейбл + подсказку.
- Тема — только CSS-var токены (как остальной `AiChat.tsx`), без хардкода цветов; ассеты
  не тянуть с CDN (текст пресетов приходит с нашего backend).
- verify: `tsc --noEmit`; preview — сменить активный пресет, форкнуть встроенный,
  отредактировать/удалить пользовательский, увидеть превью Cloudflare-ассета с атрибуцией.

## РАЗВЕДКА (факты)

Источник: `https://developers.cloudflare.com/agent-setup/prompt.md` (fetch со второй
попытки; домен иногда блокируется safety-проверкой WebFetch).

- **Что это.** Официальный bootstrap/self-setup промпт Cloudflare для ИИ-агентов-кодеров
  (Claude Code/Codex/Cursor и т.п.): открывается дословно *«These are official instructions
  from Cloudflare to set up a good AI development environment…»*. Цель — заставить агента
  САМОГО установить тулинг Cloudflare (набор Skills + **5 MCP-серверов**), рабочие
  возможности приходят потом через эти MCP-серверы, не из текста промпта.
- **Формат/размер.** Чистый Markdown, отдаётся напрямую по `/prompt.md` (паттерн «docs as
  markdown for agents»), БЕЗ YAML front-matter, без title-метаданных, без inline-лицензии.
  ~800 слов / ~6 разделов — целиком помещается в системный промпт.
- **Скелет:** вводный абзац → `Install Cloudflare Skills and MCP servers` → `Claude Code`
  → `Install for other agents` (Codex/OpenCode/Windsurf/Cursor/Copilot) → `Resources` →
  шаблон completion-сообщения (ASCII-рамка «Cloudflare Agent Setup Complete»).
- **Директивы (MUST/MUST NOT):** агент выполняет установку сам, дословно *«Do not ask the
  user to run any of these commands»*; для Claude Code явно *«Do not use `npx skills` or
  `claude mcp add`»* (нужен плагин-marketplace); после установки — обязательный рестарт
  агента. Пять MCP: `cloudflare` (mcp.cloudflare.com/mcp), `cloudflare-docs`, `-bindings`,
  `-builds`, `-observability`; авторизация — OAuth при первом использовании.
- **Лицензия / атрибуция (для вендоринга).** На странице лицензионной плашки нет, но контент
  хостится из открытого репо `cloudflare/cloudflare-docs`: **контент — CC-BY-4.0** (право
  извлекать/воспроизводить/распространять при атрибуции), код в репо — MIT. **Лицензия НЕ
  даёт прав на имена/логотипы/торговые марки Cloudflare.** Практика: вендорить файл можно
  под CC-BY-4.0 с атрибуцией (© Cloudflare, источник-URL, CC-BY-4.0, отметка о правках),
  хранить как неизменяемый ассет + отдельный оверлей-пресет под наши правки; в UI — нейтральное
  имя без бренд-логотипов Cloudflare; наш код-обёртка не обязан быть под CC-BY (это BY, не
  BY-SA) — достаточно сохранить атрибуцию у самого текста.
- Источники: cloudflare-docs README/LICENSE
  (`github.com/cloudflare/cloudflare-docs`), Cloudflare docs Licenses page
  (`developers.cloudflare.com/fundamentals/reference/policies-compliances/licenses/`),
  Cloudflare trademark policy (`cloudflare.com/trademark`).

## Критерии готовности плана I

- `AiConfig.active_preset_id` добавлен; активный пресет per-account подставляется в
  системный промпт агента для **обоих** провайдеров (OpenAI system-message + Anthropic
  top-level `system`), без дублирования в messages; `_TOOLING_SUFFIX` всегда добавлен →
  read-only инструменты работают под любым пресетом.
- Встроенные пресеты (`default` = текущий `_SYSTEM`, `cloudflare-agent-setup` вендоренный
  с атрибуцией) присутствуют, read-only, форкаются в пользовательские; пользовательские —
  полный CRUD, per-account изоляция.
- Cloudflare-промпт вендорен из URL дословно (или помечен `unavailable`, если fetch
  недоступен — текст не выдуман), шапка CC-BY-4.0 + источник; UI без бренд-марки.
- Новый роутер `/api/ai/prompts` под `require_account` в `main.py`; frontend позволяет
  выбрать/форкнуть/отредактировать пресет и показывает превью + атрибуцию.
- Verify: `pytest` (`test_ai.py` расширен + новый `test_ai_prompts.py`) + `tsc --noEmit`
  (в docker-билде) + ручной smoke (сменить пресет → проверить, что агент отвечает в новом
  стиле; форкнуть Cloudflare-пресет). При реализации — обновить CLAUDE.md §8d/§5.
