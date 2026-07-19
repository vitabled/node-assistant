# Волна 4 · План A — Группа «Хостинги»: Карта + каталог хостингов

> Новая фича. Группа сайдбара «Хостинги» с двумя разделами: «Карта» (интерактивная оффлайн-вектор карта мира) и
> «Хостинги» (карточки хостингов; их локации авто-отмечаются на карте с точностью до города).
> Затрагивает: `frontend/src/components/Sidebar.tsx`, `App.tsx`, новый `components/hostings/*`, новый backend
> `services/hostings_store.py` + `api/hostings.py`, `frontend/package.json` (motion + react-simple-maps).

## Контекст (как есть)

- Сайдбар — группы разделов (`Sidebar.tsx`), `Tab`-union + `CRUMB`-метки в `App.tsx`; страницы инфры используют
  общий каркас `components/infra/ui.tsx` (`Page`/`PageHeader`, `ni-pagebody`/`ni-pagehead` для мобильной вёрстки).
- Инфра-биллинг (§4c) — отдельная сущность (провайдеры с балансом/стоимостью). **«Хостинги» — независимый раздел**,
  НЕ переиспользует инфра-биллинг (опц. линк на провайдера — необязателен).
- Флаги/страны — `utils/format.ts::getFlagEmoji`, `CountrySelect::COUNTRIES` (ISO alpha-2). Графики в проекте —
  inline-SVG, CSP self-contained.

## Развилки (закреплены)

- Новый независимый раздел. Карта — **оффлайн-вектор** (react-simple-maps + d3-geo + world topojson-ассет),
  без внешних тайл-серверов. **Анимации через `motion`** (маркеры, переходы, зум). Локация = город+страна →
  координаты из **встроенного world-cities датасета** (фолбэк ручной lat/lon). Тоггл континентов + масштаб.

## Стратегия

Ф1 (backend: стор хостингов) → Ф2 (frontend: раздел «Хостинги» — карточки CRUD) → Ф3 (frontend: раздел «Карта»
+ маркеры из локаций) → Ф4 (сайдбар-группа + роутинг + зависимости).

---

### Ф1 — Backend: стор хостингов → verify: pytest test_hostings

`services/hostings_store.py` — per-account JSON (`accounts/<id>/hostings.json`, атомарная запись + lock, как
`sync_store`/`testserver_registry`):
- `Hosting{id, name, website, notes, features, tariffs:[{name, specs, price, currency, period}],
  locations:[{city, country_code, lat, lng, note}], provider_ref?(инфра-биллинг, опц.), created_at}`.
- Координаты локации: если оператор ввёл только город+страну — backend резолвит `lat/lng` из **встроенного
  world-cities датасета** (`services/geo_cities.py` + компактный ассет `data/world_cities.json`, город+код
  страны → координаты; MIT/CC источник, напр. simplemaps worldcities basic). Не нашлось → оставить пустыми,
  требовать ручной ввод.
- CRUD: `list/add/update/delete_hosting`; `geo_resolve(city, country_code)`.

`api/hostings.py` (`/api/hostings`, под `require_account`): `GET/POST /`, `PUT/DELETE /{id}`,
`GET /geo/resolve?city=&country=` (для автокомплита координат в форме).
- verify: `backend/tests/test_hostings.py` — CRUD + isolation + geo_resolve (известный город → координаты,
  неизвестный → пусто).

---

### Ф2 — Frontend: раздел «Хостинги» (карточки) → verify: tsc + preview

`components/hostings/HostingsCatalog.tsx` (каркас `infra/ui.tsx::Page`):
- Сетка карточек хостингов; кнопка «Добавить хостинг» → модалка-редактор: Название / Сайт / Особенности /
  Примечания / **Тарифы** (повторяемые строки: имя/спеки/цена+валюта/период) / **Локации** (повторяемые:
  `CountrySelect` страна + город; координаты авто-резолвятся через `/api/hostings/geo/resolve`, с ручной
  правкой lat/lng). Опц. линк на провайдера инфра-биллинга (select).
- Карточка показывает тарифы (мин. цена), число локаций (с флагами), особенности.
- Тема — var-токены (light/apple-light coherent, как остальной инфра-UI).
- verify: `tsc`, preview — создать/редактировать/удалить хостинг с тарифами и локациями.

---

### Ф3 — Frontend: раздел «Карта» → verify: preview + скриншот

`components/hostings/HostingsMap.tsx`:
- **react-simple-maps** (`ComposableMap`/`Geographies`/`Geography`/`ZoomableGroup`/`Marker`) + статический
  **world topojson** ассет (`world-atlas` 110m, положить в `public/`/`assets/` — self-contained, без внешних
  тайлов).
- **Тоггл континентов**: чекбоксы (Европа/Азия/Африка/Сев.Америка/Юж.Америка/Океания/Антарктида) → фильтр
  отрисовки стран по континенту (country→continent мапа как ассет или из свойств topojson). Скрытые континенты
  не рисуются/приглушены.
- **Масштаб/пан**: `ZoomableGroup` (кнопки +/− и колесо); ограничить zoom-экстент.
- **Маркеры городов**: из всех `locations` всех хостингов (`GET /api/hostings`), позиция по lat/lng; кластеризация
  не обязательна. Клик по маркеру → поповер (хостинг, город, тариф-мин, примечание).
- **Анимации `motion`**: маркеры — pop-in/scale на маунте и при появлении новых; плавный zoom/переходы;
  hover-подсветка. (Добавить зависимость `motion`.)
- verify: preview + скриншот — карта рендерится оффлайн, континенты тоглятся, зум работает, маркеры хостингов
  на месте с анимацией.

---

### Ф4 — Сайдбар-группа + роутинг + зависимости → verify: tsc + preview + mobile smoke

- `Sidebar.tsx`: новая группа **«Хостинги»** с пунктами «Карта» (`hostings-map`) и «Хостинги» (`hostings-list`);
  `App.tsx` — роуты + `CRUMB` + валидные ключи для `tabKey`-персиста. Иконки lucide (Map/Server).
- Мобильная вёрстка: карта в `ni-pagebody`, на узких экранах — упрощённый зум/тач (проверить в `mobile-smoke`).
- `frontend/package.json`: добавить `react-simple-maps` (+ `d3-geo`/типы) и `motion`; топоjson/world-cities —
  статические ассеты (не npm-тайлы).
- verify: `tsc`, preview обоих разделов, `tests/e2e/mobile-smoke.mjs` (группа доступна из «Ещё»-drawer).

## Критерии готовности плана A

- Группа «Хостинги» с разделами «Карта» и «Хостинги»; карточки CRUD (тарифы/спеки/стоимости/особенности/
  примечания/локации), per-account.
- Локации авто-резолвятся до координат города и отмечаются на оффлайн-карте; континенты тоглятся; зум работает;
  анимации через `motion`.
- Карта self-contained (без внешних тайл-серверов). `pytest` (test_hostings) + `tsc` + preview + mobile-smoke.
