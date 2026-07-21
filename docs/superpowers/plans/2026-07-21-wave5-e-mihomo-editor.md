# Волна 5 · План E — Mihomo-редактор (порт 123jjck/mihomo-configurator)

> **Статус (2026-07-21): ⚠️ доехало ЧАСТИЧНО, но иначе, чем планировалось.** Вместо React-порта форк
> `123jjck/mihomo-configurator` **встроен КАК ЕСТЬ** (vanilla-JS) через same-origin `<iframe>`: приложение в
> `frontend/public/mihomo/`, обёртка `frontend/src/components/MihomoEditor.tsx`, nav-таб «Mihomo»
> (`Tab "mihomo"`, `Sidebar.tsx`/`App.tsx`), `js-yaml` вендорится локально на билде
> (`frontend/scripts/vendor-mihomo.mjs`, CSP-self-contained). Работает вживую (визард DNS→Серверы→Правила→Скачать,
> генерация YAML). Детали — CLAUDE.md §9i. **Причина отклонения:** апстрим = 3.5k строк глобального DOM-кода без
> лицензии → порт в React рискован и юридически мутен; встраивание даёт весь инструмент сразу, ноль backend.
> **НЕ сделано (осталось от этого плана):** привязка к mihomo-шаблонам Плана D (загрузка/сохранение в наш
> template-стор — планируется через postMessage поверх iframe); тема iframe не следует нашим skin×mode токенам
> (у приложения свой тёмный стиль). Ниже — исходный план React-порта (оставлен как референс на случай, если
> потребуется нативная привязка к шаблонам/темизация).

> Визуальный редактор конфигов **mihomo (Clash.Meta)** — YAML-ориентированный аналог нашего Xray-редактора
> (`frontend/src/components/profiles/*`). Правит `proxies` / `proxy-groups` / `rules` / `dns` / `proxy-providers` /
> `rule-providers`. Источник идей — **github.com/123jjck/mihomo-configurator** (браузерный генератор mihomo-YAML,
> vanilla-JS). Привязывается к **mihomo-типу** пользовательских шаблонов Плана D
> (`2026-07-21-wave5-d-custom-configs.md`; `templateType==MIHOMO` → поле `encodedTemplateYaml` = base64(YAML)).
> Затрагивает: `frontend/src/components/mihomo/*` (новый модуль по образцу `profiles/*`), роут `rw-mihomo` в
> `App.tsx` + пункт в `Sidebar.tsx`, вендоринг `js-yaml`. Backend — **минимальный** (хранение и синк через
> subscription-templates Плана D; отдельного стора у редактора нет, черновик в localStorage per-account).
> ⚠️ **Юридический блокер (см. РАЗВЕДКА):** у апстрима НЕТ файла LICENSE → «all rights reserved» → **прямой
> перенос кода недопустим**. Портируем только по референсу (формат/идеи/схема данных), код пишем свой; либо
> запрашиваем лицензию у автора. Это отличает план от `profiles/*` (там апстрим MIT, копирование легально).

## Контекст (как есть)

- **Xray-редактор** (`components/profiles/*`, CLAUDE.md §8b) — форк bropines/xray-config-ui-editor (**MIT**),
  переписан в React18/TS. Ядро `core/`: `types.ts`, `schema.ts` (ajv JSON-Schema), `validators.ts`,
  `diagnostics.ts`, `factories.ts`, `warp.ts`, `crypto.ts` (**CSPRNG `crypto.getRandomValues`** для UUID/shortId,
  X25519 через tweetnacl), `links.ts` (share-link ↔ Xray-outbound: vless/vmess(base64-JSON)/trojan/ss + WG).
  Стор `store/configStore.ts` (**Zustand + Immer**, `commit()` через `produce`, персист per-account
  `localStorage['xray_profile_<accountId>']`, `dirty` = «не синхронизировано с панелью»; `hydrate()` при mount).
  UI: `Profiles.tsx` (empty-state drag&drop + секции-карточки + toolbar импорт/экспорт/генераторы/синк),
  `JsonEditor.tsx` (**CodeMirror6 + ajv-linter**, тёмная `oneDark` в любой теме — конвенция `--term-bg`),
  `SectionJsonModal.tsx`, `ItemModal.tsx`, `GeneratorsModal.tsx`, `DiagnosticsPanel.tsx`.
- `App.tsx:221` — `{tab === "rw-profiles" && <Profiles/>}`; `CRUMB["rw-profiles"]=["Node Installer","Профили"]`
  (строка 72); импорт строка 18. Пункт «Профили» в `Sidebar.tsx` (NAV_MAIN после «Шаблоны», Wave3 §9c).
- **Синк браузер→панель у Xray НЕ реализован** (кнопка «Синхронизировать» — TODO-stub, `Profiles.tsx:91`);
  привязка профили↔шаблоны была **явно отменена** (Wave3 10b, коммит 9bf2f20). Редактор пока чисто локальный.
- `remnawave_client.py:116-160` — есть **только config-profiles** (`create/list/get/update_config_profile`).
  Методов **subscription-templates НЕТ** — их добавляет План D (list/get/create/update/delete по 6 типам
  клиента `XRAY_JSON·XRAY_BASE64·MIHOMO·STASH·CLASH·SINGBOX`; MIHOMO → `encodedTemplateYaml`=base64(YAML)).
- **`js-yaml` НЕ является прямой зависимостью** `frontend/package.json` (только транзитивно в lock). Новый
  YAML-редактор требует рантайм-YAML — пакет ставится в Docker-билде фронта (хосту npm не нужен, ср. Wave4 карта
  world-atlas / §9d).

## Развилки (закреплены)

- **Отдельный модуль `components/mihomo/*`, НЕ расширение Xray-стора.** Форматы несовместимы: Xray = JSON-дерево
  `inbounds/outbounds/routing` + `streamSettings`; mihomo = **плоский YAML** kebab-case (`proxies`,
  `proxy-groups`, `rules`, `dns`, `rule-providers`, `proxy-providers`). Нужен параллельный YAML-слой (js-yaml +
  свои TS-типы proxy/group/rule), а не общий стор. Переиспользуем **паттерны и часть ядра** (см. Ф1), не код.
- **Код пишем свой (референс-порт), НЕ копируем апстрим** — из-за отсутствия лицензии (РАЗВЕДКА п.3). Апстрим —
  источник формата/полей/логики парсеров; реализация оригинальная в нашем стиле. В шапках файлов указываем
  «referenced from 123jjck/mihomo-configurator (no license — reimplemented, not copied)», а НЕ «Ported from … (MIT)».
- **Хранение и синк — через шаблоны Плана D** (subscription-templates, `templateType==MIHOMO`). Свой backend-стор
  редактор НЕ заводит: черновик — per-account localStorage (`mihomo_profile_<accountId>`, по образцу configStore);
  «Сохранить в шаблон» → `encodedTemplateYaml=base64(js-yaml.dump)` через API Плана D. Если План D к моменту
  реализации не готов — редактор поставляется автономным (импорт/экспорт `.yaml`), кнопка синка — TODO-stub (как
  у Xray сейчас), без блокировки.
- **Обратного парсинга целого mihomo-YAML** апстрим не делает — импортируем только **share-ссылки/подписки/WG**
  в `proxies[]`. У нас же есть js-yaml → добавляем и **импорт целого `.yaml`** (load→объект→редактор), это дельта
  к апстриму и низкий риск (js-yaml.load безопасен для наших данных, без `!!js/function` — используем `load`, не
  `loadAll` с кастомными тегами).
- **Диагностики — предупреждения, не блокеры** (как Xray: наши enum-списки могут отставать от mihomo). Критично
  только структурное (не-объект `proxies`, дубликаты `name`, битый YAML).
- **В фоне не переспрашивать:** дефолтный device-профиль `desktop`; дефолтный `MATCH`-таргет `DIRECT`; DNS-пресет
  Quad9 (как в апстриме). Меняются в UI.

## Стратегия

Ф1 (core: YAML-типы/парсеры/генератор/валидаторы + стор) → Ф2 (UI-модуль `mihomo/*` + роут/навигация) →
Ф3 (привязка к mihomo-шаблонам Плана D: загрузка/сохранение через subscription-templates + backend-метод).

---

### Ф1 — Ядро mihomo (core + store) → verify: tsc + unit

Новый модуль `frontend/src/components/mihomo/`:
- `core/types.ts` — TS-типы mihomo: `MihomoConfig` (top-level `mode/ipv6/log-level/allow-lan/external-controller/
  dns/proxies/proxy-providers/proxy-groups/rule-providers/rules/sniffer/profile/tun/listeners`), `Proxy` (плоский:
  `name,type,server,port,udp,tls,servername,skip-cert-verify` + протокол-специфичные `uuid/password/cipher/flow/
  alpn/fingerprint` + вложенные `ws-opts/grpc-opts/reality-opts`), `ProxyGroup` (`name,type(select|url-test|
  fallback|load-balance),proxies[],url,interval`), `Rule` (строка `TYPE,payload,target` или структурная модель),
  `RuleProvider`, `ProxyProvider`, `DnsConfig`.
- `core/yaml.ts` — обёртка над **`js-yaml`** (вендорится): `dumpConfig(cfg)` = `dump(obj,{lineWidth:-1,noRefs:true})`
  + пост-обработка пустых строк между секциями (как апстрим `generate.js`); `loadConfig(text)` = `load` (безопасный,
  без кастомных тегов) → объект. Пустые/битые входы → `null`.
- `core/parsers.ts` — **реимплементация** апстримовского `parsers.js` (по референсу, свой код): share-ссылка →
  `Proxy`-объект для `vless/vmess/ss/ssr/trojan/hysteria(1|2)/hy2/tuic/socks5/anytls/mieru`, Amnezia `vpn://`
  (zlib-JSON), WireGuard/AmneziaWG из `.conf`; подписка (base64-список) → `Proxy[]`. Ошибки → `null` без утечки
  фрагментов ссылки. **crypto:** для генерации `name`/random-суффиксов — `crypto.getRandomValues` (переиспользуем
  паттерн `profiles/core/crypto.ts`, НЕ Math.random для чувствительного материала).
- `core/presets.ts` — пресеты rules (services/CDN/Telegram/Discord/ru-blocked/geosite) + DNS (Quad9/Google/
  Cloudflare) + device-профили (PC/Android/iOS/OpenWRT — меняют `external-controller`/`tproxy-port`/`routing-mark`/
  `external-ui`). По референсу `state.js`/`generate.js`.
- `core/validators.ts` + `core/diagnostics.ts` — структурная валидация (proxies — массив объектов, уникальность
  `name`, наличие `MATCH`-fallback, ссылки rules→group существуют); enum-нарушения → **warning**, не блокер (зеркалит
  `profiles/core/validators.ts` down-ранк enum). `collectDiagnostics(cfg)` → `{rows, blockers}`.
- `store/configStore.ts` — **Zustand + Immer** по образцу `profiles/store/configStore.ts` 1:1 (тот же `commit()`/
  `produce`/`persist`/`hydrate`, `dirty`), но ключ `mihomo_profile_<accountId>` и секции mihomo (CRUD proxies/
  groups/rules/providers, `toggleSection`, reorder строк прокси/правил как в апстрим-таблице).
- verify: `npx tsc --noEmit`; unit-тесты `core/parsers.test.ts` (round-trip share-link по каждому протоколу),
  `core/yaml.test.ts` (dump→load идемпотентность), `core/validators.test.ts` (enum→warning, дубликат name→blocker),
  `store/configStore.test.ts` (persist/hydrate per-account) — фреймворк тот же, что у profiles-тестов.

---

### Ф2 — UI-модуль mihomo + навигация → verify: tsc + preview

- `Mihomo.tsx` — по образцу `Profiles.tsx`: empty-state (drag&drop `.yaml` + «Пустой конфиг», лимит 5 МБ как
  `MAX_IMPORT_BYTES`), toolbar (Импорт/Экспорт `.yaml` / Генераторы / Сохранить-в-шаблон / Очистить), секции-
  карточки **Proxies / Proxy-groups / Rules** + блоки **DNS / Providers / Прочее**, `dirty`-чип «не синхронизировано».
- `YamlEditor.tsx` — **CodeMirror6** с `@codemirror/lang-yaml` (вендорится; в profiles используется `lang-json`),
  тёмная `oneDark` (конвенция `--term-bg`). Линтер — наш `validators` (js-yaml parse + структурные проверки), НЕ ajv.
- `SectionYamlModal.tsx` / `ProxyModal.tsx` / `RuleModal.tsx` / `GeneratorsModal.tsx` (импорт share-ссылки/подписки/
  WG-файла → `parsers` → `addProxies`) / `DiagnosticsPanel.tsx` — по образцу одноимённых у profiles, тема через
  CSS-var токены (skin×mode), цвета НЕ хардкодить.
- **Роут/навигация:** `App.tsx` — импорт `Mihomo`, `{tab === "rw-mihomo" && <Mihomo/>}`, `CRUMB["rw-mihomo"]=
  ["Node Installer","Mihomo"]`; `Sidebar.tsx` — пункт «Mihomo» рядом с «Профили» (NAV_MAIN). Тип `Tab` расширить
  `"rw-mihomo"`. (Навигационная группировка — согласовать с Планом A `2026-07-21-wave5-a-spravka-nav.md`, если он
  меняет структуру NAV_MAIN.)
- **CSP-self-contained:** `js-yaml` + `@codemirror/lang-yaml` — обычные npm-deps, бандлятся Vite в Docker-билде;
  без CDN/внешних загрузок. geo/rule-providers — только как строки-ссылки в конфиге (mihomo сам их тянет на ноде),
  редактор НЕ фетчит внешние geosite.
- verify: `npx tsc --noEmit`; `preview` — импорт `.yaml`, добавление прокси из share-ссылки, редактирование правил,
  экспорт, диагностика; проверка light/dark × apple/console (нет тёмных островов, ср. §2a).

---

### Ф3 — Привязка к mihomo-шаблонам Плана D → verify: pytest + tsc + preview

- **Backend (минимальный, зависит от Плана D):** subscription-templates методы в `remnawave_client.py` добавляет
  План D. Здесь — при необходимости — маршрут-прокси для загрузки/сохранения именно MIHOMO-шаблона под
  `require_account` (реюз роутера Плана D; своего стора не заводим). Если План D уже даёт generic
  `GET/PUT /api/custom-configs/templates/{uuid}` — используем его, backend-дельты нет.
- **Frontend:** в `Mihomo.tsx` кнопка «Загрузить из шаблона» (список шаблонов `templateType==MIHOMO` из API Плана D
  → `base64decode(encodedTemplateYaml)` → `loadConfig`) и «Сохранить в шаблон» (`dumpConfig` → `base64` →
  `encodedTemplateYaml` через двухшаговый create/patch Плана D). `dirty` сбрасывается по успешному сохранению.
- **Изоляция/секреты:** всё под `require_account`; шаблоны — панельная сущность Remnawave (per-account креды из
  настроек аккаунта). Конфиги mihomo секретов at-rest у нас не создают (черновик в localStorage, как Xray).
- verify: `pytest backend/tests/` (если добавлен роут-прокси — `test_*` на нём); `tsc`; `preview` — round-trip
  редактор↔MIHOMO-шаблон панели (сохранить, перечитать, совпадает).

## РАЗВЕДКА (факты)

- **Что это (R1):** `123jjck/mihomo-configurator` (ветка `main`) — браузерный пошаговый генератор YAML-конфигов
  для **mihomo (форк Clash.Meta)**. Импорт прокси из share-ссылок (`vless/vmess/ss/ssr/trojan/hysteria(1/2)/hy2/
  tuic/socks5/anytls/mieru`, Amnezia `vpn://` zlib-JSON, WG/AmneziaWG `.conf`), подписки (proxy-providers по HTTPS),
  rules из пресетов (services/CDN/Telegram/Discord/ru-blocked/geosite) + кастом, device-профили (PC/Android/iOS/
  OpenWRT), двуязычный UI (ru/en), хостинг на GitHub Pages.
- **Стек (R1):** **vanilla JS (ES-модули), без фреймворка** (JS 81% / CSS 9% / HTML 9%). `package.json`:
  `private, type:module, 0.1.0`, **рантайм-зависимостей нет** (`js-yaml` только в devDependencies — инлайнится
  собственным билд-скриптом `tests/helpers/build-site.mjs`, не Vite/webpack). Тесты Vitest + Playwright.
- **⚠️ Лицензия (R1, п.3):** **файла LICENSE НЕТ** (GitHub API → 404), в README не упомянута → по умолчанию
  «all rights reserved» → **юридически нельзя копировать/портировать код без разрешения автора**. В отличие от
  `profiles/*` (апстрим bropines — MIT) это **блокер прямого переноса**: либо запросить лицензию у автора, либо
  использовать репозиторий **только как референс идей/формата**, а код написать свой. План закладывает второй путь.
- **Структура апстрима (R1, п.4):** папка `app/` (нет `src/`): `state.js` (модель+i18n+localStorage), `parsers.js`
  (share-link/подписки/WG → объекты прокси), `generate.js` (JS-объект → `jsyaml.dump()` + пост-обработка пустых
  строк), `ui.js` (рендер визарда plain-DOM `innerHTML`+inline-`onclick`), `style.css`, `index.html`.
- **Модель данных (R1, п.7):** плоский мутабельный `initialState()` (`step,ipv6,dns{defaultNs,nameservers},
  proxies[],proxyProviders[],rules[],active*Presets(Set),matchTarget,device,lang,importedRawConfig`), персист
  **только языка** в localStorage, обновления через ручной `renderAll()`. Объект прокси — плоский mihomo-вид
  (`name,type,server,port,udp,tls,servername,skip-cert-verify` + протокол-специфичные + `ws-opts/grpc-opts/
  xhttp-opts/reality-opts`). **Обратного парсинга целого YAML нет** (`importedRawConfig` есть, но парсер конфига
  отсутствует) — импортируются только отдельные ссылки/подписки.
- **Портируемость (R1, п.6):** **средняя.** Высокая ценность и переносимость (pure-функции без DOM, ложатся в наш
  `core/` как аналог `profiles/core/links.ts`): **`parsers.js`** (share-link→proxy по всем протоколам) и
  **`generate.js`** (объект→YAML). Требует переписывания весь `ui.js` (строковый DOM → JSX + наш Zustand/Immer стор),
  state → типизированный TS-store, i18n. Депенденси: нужен `js-yaml` в рантайме (у нас его нет прямым — ставится в
  Docker-билде фронта, ср. Wave4 карта). Итог: ядро (парсеры/генератор) переносимо по референсу, UI пишется заново.
- **Формат mihomo vs Xray (различия, влияет на выбор «отдельный модуль»):** mihomo — **YAML** плоский kebab-case
  top-level (`proxies/proxy-groups/rules/dns/listeners/tun/sniffer/rule-providers/proxy-providers`), роутинг —
  human-readable **rules** (`DOMAIN-SUFFIX,…,PROXY`, `GEOSITE`, `GEOIP`, `MATCH`) + **proxy-groups** (select/
  url-test/fallback/load-balance). Xray — **JSON** `inbounds/outbounds/routing/dns/policy`, транспорт в
  `streamSettings`, балансировка через `balancers`, групп-переключателей нет. mihomo нативно тянет
  `proxy-providers/rule-providers` (у Xray аналога нет). Практический вывод: **параллельный YAML-слой**, не
  расширение Xray-стора.
- **Шаблоны Remnawave (R4, связь с Планом D):** `templateType` enum = ровно 6 —
  `XRAY_JSON·XRAY_BASE64·MIHOMO·STASH·CLASH·SINGBOX`. Контент в двух полях: **`templateJson`** (object) для
  JSON-ядер (XRAY_JSON/SINGBOX/XRAY_BASE64), **`encodedTemplateYaml`** (**base64-строка YAML**) для YAML-ядер
  (**MIHOMO**/CLASH/STASH). Create — **двухшаговый** (`POST {name,templateType}` пустой → `PATCH {uuid,
  encodedTemplateYaml}` контент). `name` 2–255, `^[A-Za-z0-9_\s-]+$`. Методов subscription-templates в
  `remnawave_client.py` сейчас нет — их добавляет План D. Дефолтный `mihomo.yaml` — в `remnawave/templates`.
- **Источники:** github.com/123jjck/mihomo-configurator + `/main/README.md`, `git/trees/main?recursive=1`,
  `raw…/app/{state,generate,parsers,ui}.js`, `package.json` (LICENSE → HTTP 404);
  `api-1.json` (OpenAPI Remnawave v2.8.0); github.com/remnawave/templates; docs.rw templates/mihomo.md;
  локально `backend/app/services/remnawave_client.py:116-160`, `frontend/src/components/profiles/*`.

## Критерии готовности плана E

- **Юр-вопрос закрыт до реализации:** подтверждено, что редактор — оригинальный код по референсу (шапки файлов
  «reimplemented, not copied»), либо получена лицензия автора. Никакого прямого копирования `app/*.js` апстрима.
- Модуль `components/mihomo/*` даёт визуальный редактор mihomo-YAML: импорт share-ссылок/подписок/WG в `proxies[]`,
  редактирование `proxy-groups`/`rules`/`dns`/providers, импорт/экспорт `.yaml`, диагностики (enum→warning,
  структурное→blocker), CodeMirror-YAML-редактор секций.
- Стор per-account (`mihomo_profile_<accountId>`), Zustand+Immer, `hydrate` на mount, `dirty`-трекинг — 1:1 паттерн
  `profiles/store/configStore.ts`. Черновик секретов at-rest не создаёт.
- Привязка к **mihomo-шаблонам Плана D**: загрузка (`base64decode(encodedTemplateYaml)`) и сохранение
  (`base64(js-yaml.dump)` через двухшаговый create/patch) под `require_account`; при отсутствии Плана D — автономный
  режим (импорт/экспорт `.yaml`), синк — TODO-stub без блокировки.
- CSP-self-contained: `js-yaml` + `@codemirror/lang-yaml` вендорятся и бандлятся в Docker-билде (хосту npm не нужен),
  без CDN/внешних geosite-фетчей; тема через CSS-var токены (light/dark × apple/console).
- verify: `tsc` (в docker-билде) + unit-тесты ядра (parsers round-trip, yaml dump/load, validators, store) +
  `pytest` (если добавлен backend-прокси-роут) + preview (round-trip редактор↔MIHOMO-шаблон). CLAUDE.md обновлён
  (новый §8b-подобный блок про `components/mihomo/*`) при реализации.
