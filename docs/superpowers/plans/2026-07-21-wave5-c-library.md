# Волна 5 · План C — «Библиотека»: заметки/знания + файловое хранилище

> Раздел **«Библиотека»** в группе «Справка» (создаётся планом
> `2026-07-21-wave5-a-spravka-nav.md`): per-account база знаний (markdown-заметки, теги/папки)
> **И** общее файловое хранилище с полноценным превью-рендером форматов **pdf, doc, docx, txt, md,
> xlsx, xls, odt**. Полнотекстовый поиск по извлечённому тексту (SQLite **FTS5**, ноль новых pip-зависимостей
> под сам поиск). Файлы приватны per-account (изоляция, НЕ «секреты» — Fernet-волт тут не нужен).
> Затрагивает: backend `services/library_store.py` (новый, файлы+метаданные+FTS), `services/doc_extract.py`
> (новый, извлечение текста + превью-адаптеры), `api/library.py` (новый, под `require_account`);
> frontend `components/library/*` (дерево/список, аплоадер, вьюеры, md-редактор), новый пункт в Sidebar
> (`Tab` union + `CRUMB` + роут в `App.tsx`). Вендорит `pdf.js` (как `world-atlas` topojson через `import` +
> `src/vendor.d.ts`). Переиспользует: паттерн `subpage_store.py` (файлы на диске: MAX-байты/MAX-файлов/атомарная
> запись/membership-guard от traversal), паттерн `speedtest_store.py`/`user_stats_store.py` (per-account SQLite
> с explicit `account_id` + lazy-schema под локом), `JsonEditor` из `profiles/` (для md-редактора/подсветки),
> санитайзер HTML на бэке.

## Контекст (как есть)

- **Файлового хранилища/заметок в проекте НЕТ.** Ближайший прецедент хранения файлов на диске —
  `services/subpage_store.py`: HTML лежит в `accounts/<id>/subpages/<page_id>.html`, метаданные — в
  `subpages/index.json`; лимиты `MAX_HTML_BYTES=512KiB` + `MAX_PAGES=100`, атомарная запись (`tmp`+`replace`),
  `_INDEX_LOCK` вокруг read-modify-write, **membership-guard**: id генерятся нами (12-hex), traversal/мусорный
  id не найдётся в индексе → путь `_dir / f"{id}.html"` не выйдет за пределы аккаунта.
- **Per-account SQLite** — устоявшийся паттерн: `speedtest_store.py`, `user_stats_store.py`,
  `server_monitor_store.py` (explicit `account_id` + ContextVar-fallback, lazy per-path schema под
  `_init_lock`, retention на записи, sync через `asyncio.to_thread`). `metrics_store.py` уже использует
  **FTS-соседа** — обычный sqlite3 из stdlib; ветка про FTS5 в проекте ещё не задействована.
- **Изоляция**: `storage.py::_dir` → `accounts.data_dir(aid)` (traversal-guard на id уже внутри `accounts.py`);
  `current_account` ContextVar; все data-роутеры под `_auth=[Depends(require_account)]` в `main.py` (28 штук,
  строки 99-126). Новый роутер добавляется одной строкой туда же.
- **Загрузка файлов**: `python-multipart==0.0.9` уже в `backend/requirements.txt` → `UploadFile`/`Form`
  из FastAPI доступны без новых зависимостей. Парсеров документов (pdf/docx/xlsx/odt) в зависимостях **нет** —
  их надо добавить.
- **Frontend nav** (`Sidebar.tsx`): группы `NAV_MAIN`/`STATS_TABS`/`AUTOMATION_TABS`/`RW_TABS`/`HOSTINGS_TABS`/
  `INFRA_TABS`; `Tab`-union строки 10-17; `App.tsx` `CRUMB` (строки 59-81) + рендер по `tab ===` (строки ~221).
  Группы **«Справка» ещё нет** — её создаёт план A (переименование «Хостинги»→«Справка», «Карта»→«Карта
  хостингов», добавление пунктов). План C добавляет в эту группу пункт **«Библиотека»**.
- **Вендоринг сторонних ассетов** уже практикуется: `world-atlas` topojson бандлится через `import` +
  ambient-декларация `src/vendor.d.ts` (`any`); npm-deps ставятся внутри Docker-билда фронта (хосту npm не нужен).
  CodeMirror (`@codemirror/*`) уже в `package.json` → `JsonEditor` переиспользуем. Тема — через CSS-var токены
  (skin×mode), цвета не хардкодить.

## Развилки (закреплены)

- **Профиль зависимостей — «лёгкий, permissive, pure-python-максимум»** (см. РАЗВЕДКА). Никакого PyMuPDF
  (**AGPL** — лицензионный блокер для SaaS) и никакого LibreOffice-headless (+300-500 МБ к образу) в первой
  итерации. Превью-адаптер проектируем расширяемым, чтобы LibreOffice-ветку можно было воткнуть позже без
  переписывания — но **в этом плане не ставим**.
- **`.doc` (legacy OLE)**: чистого Python-решения нет. Ставим системный бинарник **`antiword`** (в Docker-образ
  бэка, apt) — только для извлечения текста-под-поиск; превью legacy `.doc` — **текстовое/деградированное**
  (не HTML). Если antiword недоступен/упал — файл индексируется без текста (поиск по имени/тегам), превью = «формат
  не поддерживает богатый предпросмотр, скачайте файл». **В фоне не переспрашивать** — деградируем молча в лог.
- **Табличные (`.xls`)**: pure-python-тройка — `openpyxl` (xlsx) + `xlrd≥2.0` (только .xls) + `odfpy` (odt/ods).
  НЕ берём `python-calamine` (Rust-wheel) в этой итерации ради permissive-pure максимума.
- **PDF-превью — во фронте через вендорный `pdf.js`** (Apache-2.0). Снимает вопрос AGPL и серверного рендера.
  Текст для индексации — на бэке `pypdf` (основной) → `pdfminer.six` (fallback при пустом/мусорном результате).
  OCR отсканированных PDF — **вне области** (фаза 2, опционально).
- **Поиск — SQLite FTS5**, токенизатор **`unicode61`** (`remove_diacritics=2`) + **префиксный** поиск (`term*`).
  Русского стемминга нет (принято как есть). Как страховка от морфологии — **опция `trigram`-таблицы** для
  подстрочного поиска можно добавить позже; в этой итерации — `unicode61`+префикс. **В фоне не переспрашивать.**
- **Заметки** — markdown, CRUD, свои теги/папки; хранятся как строки в SQLite (не как файлы) и **тоже
  индексируются в FTS** (единый поиск «файлы + заметки»). Папки/теги — плоские строковые метки (не отдельные сущности).
- **Санитизация HTML превью** (mammoth-docx / markdown-render): на бэке — санитайзер **`nh3`** (Rust, MIT;
  `bleach` заброшен — НЕ берём); во фронте дополнительно рендер в изолированный контейнер. Никогда не инжектим
  сырой HTML документа без очистки (XSS).
- **Лимиты**: `MAX_FILE_BYTES` (дефолт **25 MiB**/файл), `MAX_FILES` (дефолт **500**/аккаунт),
  `MAX_TEXT_INDEX_BYTES` (кап извлечённого текста, напр. 2 MiB — чтобы не раздувать FTS). Валидация типа —
  по расширению **и** магическим байтам; исполнение файлов исключено (только чтение/парсинг/скачивание).

## Стратегия

Ф1 (backend: хранилище файлов + метаданные + FTS5-поиск) → Ф2 (backend: извлечение текста + превью-адаптеры
на форматы) → Ф3 (backend: заметки markdown CRUD в тот же FTS) → Ф4 (frontend: раздел «Библиотека» — дерево/
список, аплоадер, вьюеры, md-редактор, поиск).

---

### Ф1 — Backend: файлы + метаданные + FTS5 → verify: pytest + py_compile

`services/library_store.py` (per-account, паттерн `subpage_store` + `speedtest_store`):
- Файлы на диске: `accounts/<id>/library/<file_id>.<ext>` (`file_id` = 12-hex, генерим мы). Атомарная запись
  (`tmp`+`replace`), `_INDEX_LOCK` вокруг метаданных, **membership-guard**: чтение/удаление только по id,
  найденному в БД (traversal невозможен).
- Метаданные + поиск — SQLite `accounts/<id>/library.db` (explicit `account_id`, lazy-schema под `_init_lock`):
  - `items(id, kind[file|note], name, ext, size, folder, tags_json, mime, extracted TEXT, created_at, updated_at)`
  - `CREATE VIRTUAL TABLE items_fts USING fts5(name, body, content='items', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2')` — **external-content** (не дублируем текст; `body` = извлечённый/
    markdown-текст), триггеры sync insert/update/delete.
  - На старте — проверка `sqlite_compileoption_used('ENABLE_FTS5')`; при отсутствии FTS5 — graceful-degrade на
    `LIKE`-поиск (лог-предупреждение). Retention НЕ применяем (файлы живут пока не удалены), но лимиты MAX_FILES.
- API-функции: `add_file`/`get_file_meta`/`get_file_bytes`/`list_items`/`update_item`(rename/folder/tags)/
  `delete_item`/`search(q, limit)` (BM25-ранжирование `ORDER BY bm25(items_fts)`, `snippet()`/`highlight()` для
  сниппетов результата с подсветкой).
- `api/library.py` (`/api/library`, под `require_account` — добавить строку в `main.py`):
  - `GET /items?folder=&tag=` (список), `POST /files` (multipart `UploadFile`+`folder`/`tags`) — валидация
    расширения+магии, лимиты (превышение → **413**/**400**), сохранение + извлечение (Ф2) + индексация,
  - `GET /files/{id}/download` (`StreamingResponse`, `Content-Disposition: attachment`),
  - `GET /files/{id}/preview` (см. Ф2 — отдаёт `{kind, html|rows|text|pdf_url}`),
  - `GET /files/{id}/raw` (сырые байты для pdf.js; `Content-Type` по mime, `X-Content-Type-Options: nosniff`),
  - `PATCH /items/{id}` (rename/folder/tags), `DELETE /items/{id}`, `GET /search?q=`.
- verify: `backend/tests/test_library.py` — CRUD+изоляция (два аккаунта не видят файлы друг друга), лимиты
  (413/400), traversal-guard (мусорный id → 404), FTS-поиск (по имени/тексту/тегу, сниппет), `python -m py_compile`.

---

### Ф2 — Backend: извлечение текста + превью-адаптеры → verify: pytest

`services/doc_extract.py` — по одному чистому адаптеру на формат (диспетчер по расширению; каждый адаптер
`fail-soft` — при ошибке возвращает пустой текст + флаг «превью недоступно», НЕ бросает):
- **pdf**: текст `pypdf` → при пустом/мусорном `pdfminer.six` (fallback). Превью — **не серверное**: роут отдаёт
  `{kind:"pdf"}`, фронт рисует через вендорный pdf.js по `/raw`.
- **docx**: текст `python-docx` (параграфы+таблицы); превью — `mammoth` → семантический HTML → **санитайз `nh3`**.
- **doc (legacy)**: текст через `antiword` (`subprocess`, `shlex`/argv-list, timeout); превью — `{kind:"text"}`
  (деградированное). antiword отсутствует/упал → текст пустой, превью-текст = уведомление.
- **xlsx**: `openpyxl(data_only=True)` — текст (значения) для FTS + первые N строк×M колонок → JSON-матрица
  (`{kind:"table", sheets:[...]}`). **xls**: `xlrd≥2.0` (только .xls) — та же матрица. Лимит строк/листов.
- **odt**: `odfpy` (обход `odf.text`, teletype) — текст; превью `{kind:"text"}` или простой HTML.
- **md**: сырой текст в FTS; превью — рендер `markdown-it-py` → HTML → **санитайз `nh3`** (`{kind:"html"}`).
- **txt**: детект кодировки `charset-normalizer` (cp1251/koi8-r/utf-8 — кириллица!) → `{kind:"text"}`.
- Кап извлечённого текста `MAX_TEXT_INDEX_BYTES`. Извлечение вызывается синхронно при загрузке (в threadpool),
  результат кладётся в `items.extracted` + FTS.
- Новые зависимости в `backend/requirements.txt`: `pypdf`, `pdfminer.six`, `python-docx`, `mammoth`, `openpyxl`,
  `xlrd`, `odfpy`, `markdown-it-py`, `charset-normalizer`, `nh3`. Системный бинарник `antiword` — в
  `backend/Dockerfile` (`apt-get install -y antiword`).
- verify: `test_library.py` (доп.) — по фикстуре-файлу каждого формата: извлечение непустого текста, форма
  превью (`kind` корректный, HTML санитизирован — `<script>` вырезан), деградация при битом файле (не 500).

---

### Ф3 — Backend: заметки (markdown) CRUD → verify: pytest

- `items.kind='note'` в том же `library.db`: `POST /notes` (`{name, body_md, folder, tags}`) → сохранить,
  отрендерить превью-HTML на лету (Ф2 md-адаптер), проиндексировать `body_md` в FTS. `PATCH /notes/{id}`,
  `DELETE` (через общий `/items/{id}`), `GET /notes/{id}` (сырой markdown для редактора + rendered HTML).
- Единый поиск `/search` покрывает файлы+заметки (одна FTS-таблица). Теги/папки — общие строковые метки.
- verify: `test_library.py` (доп.) — заметка создаётся/редактируется/удаляется, попадает в общий поиск,
  markdown рендерится+санитизируется.

---

### Ф4 — Frontend: раздел «Библиотека» → verify: tsc + preview

- `Tab`-union (`Sidebar.tsx`) + `CRUMB` (`App.tsx`) + роут: новый `library`, крошки `["Справка","Библиотека"]`.
  Пункт «Библиотека» добавить в группу «Справка» (её вводит план A; если A ещё не влит — временно в
  `HOSTINGS_TABS`/новую секцию, отметить зависимость).
- `components/library/`:
  - `api.ts` — типизированный клиент `/api/library` (auth добавляется глобальным fetch-интерсептором — без
    per-call токена).
  - `Library.tsx` — двухпанельный layout: слева дерево папок/тегов + список элементов (файлы/заметки), сверху
    строка поиска (`/search`, дебаунс) с подсветкой сниппетов; справа — вьюер выбранного элемента.
  - `Uploader.tsx` — drag&drop/выбор файла (`multipart` POST `/files`), прогресс, показ лимитов/ошибок 413/400.
  - `viewers/` — `PdfViewer` (**вендорный pdf.js** по `/raw`; воркер бандлить локально — CSP-self-contained,
    без CDN; ambient-декларация в `src/vendor.d.ts` при необходимости), `HtmlViewer` (docx/md — санитизированный
    HTML в изолированный контейнер), `TableViewer` (xlsx/xls — свой React-компонент по JSON-матрице, тема через
    var-токены), `TextViewer` (`<pre>` для txt/odt/doc-деградации).
  - `NoteEditor.tsx` — md-редактор на базе `JsonEditor`/CodeMirror (уже в `package.json`) + предпросмотр
    отрендеренного HTML; CRUD заметок.
- CSP-self-contained: pdf.js и его worker вендорить/бандлить, никаких CDN/шрифтов/тайлов. Цвета — CSS-var
  токены (skin×mode), не хардкод.
- verify: `npx --no-install tsc --noEmit`; preview — загрузить по одному файлу каждого формата, открыть превью
  (pdf рендерится, docx/md как HTML, xlsx как таблица, txt/odt/doc как текст), создать/найти заметку, поиск с
  подсветкой; `Library.test.tsx` (список/загрузка/поиск/пустое состояние).

## РАЗВЕДКА (факты)

- **PyMuPDF (fitz) = AGPL-3.0-only** (или коммерческая от Artifex) — для SaaS network-use триггерит раскрытие
  исходников всего сервиса → **не брать**. Подтверждено: github.com/pymupdf/pymupdf/issues/4504, pypi.org/project/pymupdf.
- **`bleach` архивирован/заброшен (2023)** — санитайзер брать **`nh3`** (Rust, MIT), актуальная замена.
- **PDF-текст**: `pypdf` (BSD-3, pure, быстрый) как основной; `pdfminer.six` (MIT, pure, layout-анализ, медленнее)
  как fallback. Отсканированные PDF без текст-слоя требуют OCR (`tesseract`/`ocrmypdf`) — вне первой итерации.
- **docx**: `python-docx` (MIT, pure) — текст; `mammoth` (BSD-2, pure) — docx→семантический HTML для превью.
- **.doc (legacy OLE)**: чистого Python нет. `antiword` (GPL-2 системный бинарник, вызов через subprocess —
  GPL не заражает наш код) — только текст, ~15 лет без развития. `textract` — заброшен, **избегать**. LibreOffice-
  headless — самый верный, но +300-500 МБ / менеджмент soffice-процессов → отложено.
- **xlsx**: `openpyxl` (MIT, pure), читать `data_only=True` (кэш формул, иначе `=SUM(...)`). **xls**: `xlrd≥2.0`
  (BSD, pure) — с 2.0 читает **только .xls**. Альтернатива на всё сразу — `python-calamine` (MIT, Rust-wheel,
  активный) — **не** pure-python, отложено.
- **odt**: `odfpy` (dual Apache-2.0/GPLv2+ → берём Apache) — pure, релиз старый, но ODF стабилен.
- **md**: `markdown-it-py` (MIT, pure, CommonMark, активный). **txt**: builtin `open()` + `charset-normalizer`
  (MIT) для детекта кодировки (кириллица cp1251/koi8-r/utf-8).
- **Поиск — SQLite FTS5** (stdlib, Public Domain, ноль pip-зависимостей). BM25 из коробки (`ORDER BY bm25()`),
  `snippet()`/`highlight()` для сниппетов. Токенизатор `unicode61` корректно режет юникод-слова (кириллица)
  и лоуэркейсит (`remove_diacritics=2`), **НО русского стемминга нет** (`porter` = только англ.). Варианты
  морфологии: префикс `term*` (принято), либо `trigram`-токенизатор (подстрочный, ценой индекса, без ранжирования),
  либо стемминг на входе (`pymorphy3` — доп. зависимость, отложено). Источник: sqlite.org/fts5.html.
- **Подтвердить на живой системе**: (1) собран ли Python-билд с FTS5 (`SELECT sqlite_compileoption_used(
  'ENABLE_FTS5')`); (2) хватает ли `unicode61` для русского контента или нужен стемминг.
- **Прецедент в проекте**: файлы на диске per-account — `subpage_store.py` (MAX-байты/MAX-файлов/атомарно/
  membership-guard); per-account SQLite — `speedtest_store.py`/`user_stats_store.py`; вендоринг ассета —
  `world-atlas` topojson (`import` + `src/vendor.d.ts`). Загрузка — `python-multipart` уже установлен.

## Критерии готовности плана C

- Файл загружается (multipart), сохраняется под `accounts/<id>/library/`, метаданные+извлечённый текст в
  `library.db`; лимиты (размер/кол-во) и валидация типа (расширение+магия) работают; traversal невозможен.
- Превью работает для всех форматов: **pdf** (вендорный pdf.js, без CDN), **docx** (mammoth→санитизированный HTML),
  **md** (markdown-it-py→санитизированный HTML), **xlsx/xls** (JSON-матрица→таблица), **txt/odt/doc**
  (текст/деградация). Битый файл не роняет бэк (не 500).
- Заметки (markdown) CRUD + единый полнотекстовый поиск (файлы+заметки) с BM25-ранжированием и подсветкой
  сниппетов; кириллица ищется (точные формы + префикс).
- Полная per-account изоляция (два аккаунта не видят данные друг друга); новый роутер `library` под
  `require_account` в `main.py`; тесты `backend/tests/test_library.py` зелёные.
- Frontend раздел «Библиотека» в группе «Справка»: дерево/список, аплоадер, вьюеры, md-редактор, поиск —
  CSP-self-contained, тема через var-токены.
- verify: `python -m pytest` (test_library) + `python -m py_compile` + `tsc --noEmit` + preview (ручной smoke
  по одному файлу каждого формата) + `docker build` (проверить, что новые pip-зависимости и `antiword`
  ставятся в образ). Обновить CLAUDE.md (§5 роуты, новый store/сервис, §9/новая секция) при реализации.
