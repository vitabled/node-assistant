# Волна 3 · План C — Редакторы конфигов: Шаблоны (CodeMirror + $xhttp_path), Хосты, Профили

> Пункты: 4a (CodeMirror в Шаблонах), 4b (`$xhttp_path`), 5a (переменные в Хостах + убрать селектор сквадов),
> 10a (перенос Профилей), 10b (двусторонняя связь профили↔шаблоны).
> Затрагивает: `Templates.tsx`, `profiles/*`, `Hosts.tsx`, `Sidebar.tsx`, `App.tsx`, `services/pipeline.py`,
> `services/storage.py`, `api/templates.py`, `api/hosts.py`, `models/hosts.py`.

## Контекст (как есть)

- **Шаблоны** (`Templates.tsx`) — Xray-config JSON (`templates.json`, per-account), переменные `$domain`/`$name`;
  редактор = обычная `<textarea>` с `JSON.parse`-валидацией. Подставляется в `create_config_profile` при деплое.
- **Профили** (`components/profiles/*`) — визуальный редактор того же Xray-конфига: `store/configStore.ts`
  (Zustand+Immer, per-account localStorage `xray_profile_<id>`, черновик на каждый commit), CodeMirror-редактор
  `JsonEditor.tsx` (`@codemirror/lang-json` + lint + one-dark), ajv-валидация (`core/validators.ts`, enum-нарушения
  down-rank в warning). Синк браузер→панель — НЕ реализован (TODO).
- **Хосты** (`Hosts.tsx` + `models/hosts.py::HostTemplateBody`, `hosts.json`) — локальные шаблоны Remnawave-хостов
  (~25 полей), применяются в `pipeline.step_create_hosts` при деплое (`_map_host_optional`). Есть поле
  `exclude_squads` (MultiSelect) и — в форме деплоя/хостов — **селектор внутреннего/внешнего сквада**.
- Подстановка переменных в пайплайне: `.replace("$domain",…).replace("$name",…)` (напр.
  [pipeline.py:1812](../../backend/app/services/pipeline.py)); порядок `$domaincert` ДО `$domain` (CLAUDE.md §6).

## Развилки (закреплены)

- 4a: переиспользовать `profiles/JsonEditor.tsx` (CodeMirror) в модалке Шаблонов.
- 4b: добавить `$xhttp_path` (= `DeployRequest.xhttp_path`) в подстановку Xray-config-шаблона.
- 5a: переменные `$domain`/`$xhttp_path`/`$name` в строковых полях хост-шаблона, подстановка в
  `step_create_hosts` **с shell-валидацией**; **убрать селектор внутр./внешн. сквада** — сквад подтягивается сам.
- 10a: «Профили» в сайдбаре сразу после «Шаблонов». 10b: **вариант C — общий источник данных** (двусторонняя
  авто-синхронизация профиль↔шаблон).

## Стратегия

Ф1 (Шаблоны: CodeMirror + `$xhttp_path`) → Ф2 (Хосты: переменные + убрать сквады) → Ф3 (Профили↔Шаблоны: общий
стор, перенос в сайдбаре).

---

### Ф1 — Шаблоны: CodeMirror + `$xhttp_path` → verify: preview + pytest

1. **Редактор** (`Templates.tsx::TemplateModal`): заменить `<textarea>` на `JsonEditor` из
   `profiles/JsonEditor.tsx` (вынести/переиспользовать компонент; он уже даёт подсветку JSON + lint-подчёркивание
   ошибок). Сохранить текущую логику `jsonError`-гейта сохранения (lint даёт ошибки → блок save).
2. **Переменные** (`services/pipeline.py`, место `create_config_profile`-подстановки): добавить
   `.replace("$xhttp_path", req.xhttp_path or "")`. **Порядок:** ставить до/после `$domain` безопасно (нет
   префиксного пересечения имён; но помнить правило `$domaincert` перед `$domain`). Обновить подсказку в
   `Templates.tsx` (строка «переменные: …») и `TemplateModal` — перечислить `$domain`, `$name`, `$xhttp_path`.
   - **РАЗВЕДКА:** найти точную функцию, где `templates.json` config подставляется перед `create_config_profile`
     (grep `create_config_profile` + `template`), добавить `$xhttp_path` там же.
3. verify: preview (подсветка+ошибка в редакторе), pytest на подстановку `$xhttp_path` (если есть покрытие
   pipeline-подстановки; иначе smoke-скрипт).

---

### Ф2 — Хосты: переменные + авто-сквад → verify: pytest test_hosts + test_host_autocreate

1. **Переменные в хост-полях** (`pipeline.step_create_hosts`): в строковых полях хост-шаблона
   (`address`/`sni`/`host`/`path`/`remark`) поддержать `$domain`/`$xhttp_path`/`$name`, подставляя значения из
   `DeployRequest`. **Shell/inject-валидация** результата ДО отправки в Remnawave API (CLAUDE.md §5 forward-note:
   host-строки сейчас не валидируются). Подстановка — чистая строковая (эти значения не идут в bash, но валидируем
   на всякий случай host charset).
2. **Убрать селектор сквадов** — «внутренний/внешний сквад» удаляется из UI (форма деплоя/хостов); сквад
   подтягивается **автоматически** при деплое хоста в Remnawave: `add_inbounds_to_internal_squad` уже юнионит
   инбаунды ноды в сквад — оставить это как единственный механизм; убрать ручной выбор `internal_squad_ids`/
   `external_squad_ids` из формы (модель может сохранить поля для обратной совместимости, но UI их не показывает,
   деплой не требует). **РАЗВЕДКА:** подтвердить, где именно рисуется селектор (`DeployForm.tsx` squad-селекторы
   / `Hosts.tsx` `exclude_squads`) — убрать «выбор целевого сквада», НЕ трогая `exclude_squads`, если это разные
   вещи (уточнить у кода: `exclude_squads` = исключения, оставить; «внутр./внешн. сквад» = целевой выбор, убрать).
3. verify: `backend/tests/test_hosts.py` + `test_host_autocreate.py` — подстановка переменных в host-полях,
   авто-сквад без ручного выбора; `tsc`.

---

### Ф3 — Перенос «Профилей» в сайдбаре (10a) → verify: preview

> **10b (синхронизация профили↔шаблоны, вариант C) — ОТМЕНЕНО по решению пользователя (2026-07-20).**
> Общий стор `templates.json` для редактора профилей, UI-мосты «Открыть в редакторе профилей» / «Сохранить как
> шаблон» и round-trip шаблон↔профиль **не делаем**. «Профили» и «Шаблоны» остаются независимыми разделами со
> своими сторами (профиль — localStorage `xray_profile_<id>`; шаблон — backend `templates.json`).

Осталось только 10a:
- **Перенос в сайдбаре** (`Sidebar.tsx`, `App.tsx`): пункт «Профили» (tab `rw-profiles`) — сразу после
  «Шаблоны». Порядок в сайдбар-конфиге + `CRUMB`.
- verify: preview — «Профили» стоят сразу после «Шаблонов».

## Критерии готовности плана C

- Редактор шаблонов — CodeMirror с подсветкой и подчёркиванием ошибок; `$xhttp_path` подставляется при деплое.
- Хост-поля поддерживают `$domain`/`$xhttp_path`/`$name`; ручной выбор сквада убран, сквад подтягивается сам.
- «Профили» стоят сразу после «Шаблонов» (без синхронизации со шаблонами — 10b отменён).
- pytest (backend 4b/5a) + preview.
