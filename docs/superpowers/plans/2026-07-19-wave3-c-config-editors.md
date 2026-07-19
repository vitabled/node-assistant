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

### Ф3 — Профили↔Шаблоны: общий стор + перенос → verify: unit configStore + preview

**Цель (10b, вариант C):** созданный шаблон виден как профиль и наоборот — один источник данных.

1. **Модель данных:** шаблон = `{id,name,config(JSON-строка с $-плейсхолдерами),is_default,host_template_ids}`
   (backend `templates.json`); профиль = Xray-config-объект в localStorage (`xray_profile_<id>`). Развести:
   - **Решение:** сделать backend `templates.json` **единым источником**. Профиль-редактор читает/пишет тот же
     список шаблонов через `/api/templates` (а не только localStorage-черновик). localStorage остаётся кэшем
     черновика (dirty-состояние), но «сохранить» пишет в `/api/templates`. Так шаблон, созданный в «Шаблонах»,
     сразу доступен в «Профилях» для визуального редактирования, и наоборот.
   - Профиль-редактор при открытии шаблона парсит `config` (JSON) в свою модель; `$`-плейсхолдеры в строковых
     полях остаются как есть (ajv down-ранкает enum, а плейсхолдеры в строках валидны — не блокируют).
     При сохранении — сериализует обратно в JSON-строку шаблона (плейсхолдеры сохраняются дословно).
2. **UI-мосты:** в «Шаблонах» — кнопка «Открыть в редакторе профилей» (переход на профиль с этим id); в
   «Профилях» список = те же шаблоны (`/api/templates`), «Сохранить» → `PUT /api/templates/{id}`. Оба раздела
   показывают один и тот же набор.
   - **РАЗВЕДКА/риск:** профиль-модель может не покрывать 1:1 все поля произвольного шаблона (доп. ключи Xray).
     `configStore` должен сохранять неизвестные ключи (round-trip без потерь) — проверить, что сериализация не
     выкидывает поля вне схемы (schema `additionalProperties:true` — CLAUDE.md §8b — значит ок, но покрыть тестом
     round-trip шаблон→профиль→шаблон).
3. **Перенос в сайдбаре** (`Sidebar.tsx`, `App.tsx`): пункт «Профили» (tab `rw-profiles`) — сразу после
   «Шаблоны». Порядок в сайдбар-конфиге + `CRUMB`.
4. verify: `profiles/store/configStore.test.ts` — round-trip шаблон↔профиль без потери ключей и плейсхолдеров;
   `Profiles.test.tsx`/`Templates.test.tsx` — общий список; preview: создать шаблон → появился в профилях и
   наоборот.

## Критерии готовности плана C

- Редактор шаблонов — CodeMirror с подсветкой и подчёркиванием ошибок; `$xhttp_path` подставляется при деплое.
- Хост-поля поддерживают `$domain`/`$xhttp_path`/`$name` (валидируются); ручной выбор сквада убран, сквад
  подтягивается сам.
- Профили и Шаблоны — один список; правка в одном видна в другом; round-trip без потери полей/плейсхолдеров.
- «Профили» стоят сразу после «Шаблонов». pytest + configStore round-trip + preview.
