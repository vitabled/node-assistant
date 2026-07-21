# Волна 6 · План D — eGames-вариант установки ПАНЕЛИ + внешний доступ к /api

> Второй вариант установки панели Remnawave — `panel_variant: plain|egames`, зеркало уже отгруженного
> `node_variant: egames|vanilla`. eGames-вариант портирует конфиги `eGamesAPI/remnawave-reverse-proxy`
> (их bash-установщик мы НЕ запускаем): **контейнерный** nginx в том же compose, один редактируемый
> `/opt/remnawave/nginx.conf`, опциональный cookie-gate и carve-out `location ^~ /api/`.
> Затрагивает: `models/panel_deploy.py`, `services/panel_pipeline.py` (генераторы + шаги 5–7),
> `api/panel_deploy.py` (detect/uninstall/read-back), фронт `rw/PanelDeployForm.tsx` + `rw/PanelManageModal.tsx`.
> **Честная рамка:** у апстрима carve-out для `/api` — это РУЧНОЙ шаг документации, а входящего
> webhook-прокси нет вовсе (`WEBHOOK_*` у Remnawave — ИСХОДЯЩИЕ). Поэтому «прокид вебхуков» в v1 = тот же
> префиксный carve-out для произвольных путей, честно подписанный, а не мифическая фича панели.
> Попутно (решение пользователя) чиним мёртвый тумблер `cookie_gate` на ноде.

## Контекст (как есть)

- **Пайплайн панели — 8 фиксированных шагов** (`services/panel_pipeline.py:37-47` `PANEL_STEP_LABELS`,
  `:47` `PANEL_TOTAL`), дублируется вербатим на фронте (`rw/PanelWidget.tsx:18-27` + `INITIAL_STATUS` `:29-31`).
  Порты контейнеров — модульные константы `_PANEL_APP_PORT=3000`/`_PANEL_METRICS_PORT=3001`/`_SUBPAGE_PORT=3010`
  (`panel_pipeline.py:50-52`), все публикуются **только на 127.0.0.1** → reverse-proxy обязателен.
- **`run_panel_pipeline` (`panel_pipeline.py:635-719`) НЕ имеет ни одной variant-развилки.** Шаг 1 —
  подключение, 2 — docker, 3 — тест-инструменты, 4–7 — `_install_panel` (`:473-529`), 8 — `_install_subpage`
  (`:532-594`). Это единственная точка врезки варианта — ровно как `is_vanilla` в `run_pipeline`.
- **Reverse-proxy — ХОСТОВЫЙ сервис, вилка на две ветки** (`panel_pipeline.py:411-465`):
  - caddy → apt из cloudsmith (`_caddy_install_script` `:389-403`), файл **`/etc/caddy/Caddyfile`** (`:421`),
    `caddy validate` + `systemctl reload caddy`;
  - nginx → `apt-get install nginx` (`:432`), на каждый домен `upsert_a_record` (при cloudflare) +
    `pipeline.build_ssl_script` (`:449-455`), файл **`/etc/nginx/conf.d/remnawave-panel.conf`** (`:458`),
    `nginx -t` + reload.
- **Шаблон nginx-сайта — фикс-строка с двумя плейсхолдерами** `__DOMAIN__`/`__PORT__`
  (`panel_pipeline.py:237-265` `_NGINX_SITE`, рендер `_render_nginx` `:268-273`): `:80`→301 и `:443 ssl` с
  ОДНИМ `location /` на `127.0.0.1:__PORT__`. **Нет** `location /api`, нет cookie-gate/`map`, нет
  `default_server ssl_reject_handshake`, нет per-path хука. Сертификаты берутся по путям
  `/etc/ssl/certs/__DOMAIN___fullchain.pem` + `/etc/ssl/private/__DOMAIN__.key` (`:251-252`).
- **`_PANEL_COMPOSE` — статичная строка без подстановок** (`panel_pipeline.py:125-188`, возвращается
  `_compose_yml` `:191-194` как есть): `remnawave-backend` (`remnawave/backend:2`, порты
  `127.0.0.1:3000/3001`), `remnawave-db` (`postgres:18.4`, TZ=UTC), `remnawave-redis` (`valkey/valkey:9-alpine`),
  сеть `remnawave-network` с явным `name:`. **Сервиса прокси в compose НЕТ** — это главное структурное
  отличие от eGames.
- **`.env` пишется идемпотентно и МОЛЧА** (`_write_env_script` `:348-363`): существующий `/opt/remnawave/.env`
  не трогается (`__ENV_EXISTS__`), новый пишется под `umask 077` через одинарно-кавыченный heredoc и
  прогоняется через тихий канал `ssh.get_script_output` (`:476`). ⇒ **новые ключи на уже установленной панели
  НЕ появятся при reinstall** — только через «Переменные».
- **`_env_file` (`:84-118`)**: `WEBHOOK_ENABLED/WEBHOOK_URL/WEBHOOK_SECRET_HEADER` пишутся только при
  `enable_webhooks` (`:106-109`), секрет — `secrets.token_hex(32)` (у апстрима там захардкоженный плейсхолдер).
  Ключей `IS_DOCS_ENABLED`/`SWAGGER_PATH`/`SCALAR_PATH` мы не пишем вообще.
- **Модель `PanelDeployRequest`** (`models/panel_deploy.py:65-206`): `target` `:67`, `reverse_proxy`
  `Literal["caddy","nginx"]="caddy"` `:85`, `cert_provider` `:86`, `extra_env` с закрытым списком
  `_PROTECTED_ENV_KEYS` `:29-38`, кросс-полевой `validate_by_target` `:179-206` (для nginx требует
  `cf_api_key`/`email`).
- **Управление панелью** (`api/panel_deploy.py`): `Component = Literal["panel","subpage","docker",
  "test_tools","reverse_proxy"]` `:158`; `_panel_reinstall` `:263-280` вызывает те же функции пайплайна
  (в т.ч. `_setup_reverse_proxy(req, _proxy_targets(req,"panel"))` `:277-280`); `_UNINSTALL_SCRIPTS`
  `:183-212` — для `reverse_proxy` только `systemctl stop caddy/nginx` (`:196-200`), конфиг не удаляется.
  Detect-пробы `_PANEL_PROBES` `:84-100` грепают **`remnawave-backend`**.
- **Переменные** `:298-548`: `/env/read`+`/env/write` по тихому каналу, маскировка `_is_secret_env_key`
  `:313-324`, merge сохраняет нетронутые секреты `:511-522`, применение через `_COMPOSE_UP_SCRIPT` `:372-387`
  (тоже грепает `remnawave-backend`).
- **Фронт формы** `rw/PanelDeployForm.tsx`: `PanelFormData` `:19-42`, wire-shape `PanelDeployPayload` `:48-66`,
  `PANEL_FORM_DEFAULT` `:68-91`, `validatePanelForm` `:117+` (зеркалит серверные валидаторы 1:1). Сегменты есть
  для `target` и `reverse_proxy`; **селектора варианта установки нет**. Добавление поля = 4 места
  (`PanelFormData`, `PanelDeployPayload`, `PANEL_FORM_DEFAULT`, `toPayload`).
- **Фронт управления** `rw/PanelManageModal.tsx`: `panelManageableComponents` `:28-42` (panel/subpage/docker/
  test_tools/reverse_proxy), вкладки «Компоненты»/«Статистика» `:81-99`, оп через `POST /api/panel/step`
  `:125-131` в `OpStreamModal`. **Поверхности настройки путей/прокси нет вообще.**
- **Прецедент `node_variant` (зеркалим его буквально):** поле `Literal["egames","vanilla"]="egames"`
  (`models/deploy.py:37-40`), кросс-полевая ветка `is_vanilla` в `validate_by_mode` (`models/deploy.py:92-107`),
  защитный `getattr(req,"node_variant","egames")` в раннере (`services/pipeline.py:2266`) с диспетчером на
  СИБЛИНГ-шаг `step_remnanode_vanilla`, **переиспользующий тот же индекс 11** (`pipeline.py:2292-2301`);
  UI — две кнопки-суб-таб + variant-гейтед тумблеры (`DeployForm.tsx:530-569`); тест — набор вызванных шагов
  (`backend/tests/test_node_detect.py:203-216`).

### Два бага, входящие в этот план

- **БАГ A — `cookie_gate` на ноде мёртв.** Поле есть (`models/deploy.py:47`), тумблер есть
  (`DeployForm.tsx:34`, `:102`, `:565-569`), ассерт дефолта есть (`backend/tests/test_deploy.py:112`),
  **потребителей в `pipeline.py` НОЛЬ** — репо-греп по `cookie` даёт только эти места плюс несвязанный
  `net.ipv4.tcp_syncookies` (`pipeline.py:326`). Нодовый `_NGINX_TPL` (`pipeline.py:1128-1165`) не содержит
  ни `map $http_cookie`, ни `$authorized`, ни `return 444` по авторизации.
  **CLAUDE.md §9a** перечисляет «тумблеры Hysteria2/Cookie-gate/Docker-mirror» так, будто тумблер работает —
  **прав код, CLAUDE.md требует правки** (см. Ф5).
- **БАГ B — `replace_domain.panel_replace_script` не видит наши реальные конфиги.**
  `_PANEL_REPLACE_TPL` (`services/replace_domain.py:75-99`) делает `cd /opt/remnawave` и седит только
  `.env docker-compose.yml Caddyfile caddy/Caddyfile nginx.conf` (`:83`), затем `docker compose down && up -d`.
  Наш **plain**-вариант пишет прокси-конфиг **вне** этого каталога — `/etc/caddy/Caddyfile`
  (`panel_pipeline.py:421`) и `/etc/nginx/conf.d/remnawave-panel.conf` (`panel_pipeline.py:458`) — и это
  хостовые сервисы, которые скрипт ни разу не перезагружает. ⇒ `POST /api/replace-domain/panel`
  (`api/replace_domain.py:191-199`) сегодня меняет `.env`+compose и перевыпускает серт, **но живой
  reverse-proxy остаётся на старом домене**. Список файлов, который скрипт покрывает, — это ровно
  **eGames-раскладка**: помощник писали против апстрима, а не против нашего установщика.
  ⇒ для eGames-варианта проблема исчезает сама (конфиг в `/opt/remnawave/nginx.conf`, nginx — сервис того же
  compose, который скрипт и рестартит), для plain-варианта нужна отдельная мелкая правка (Ф5).

## Развилки (закреплены)

1. **Поле `panel_variant: Literal["plain","egames"] = "plain"`** на `PanelDeployRequest` — буквальное зеркало
   `node_variant`. Дефолт = ТЕКУЩЕЕ поведение, поэтому старые записи `panel_jobs_<id>.savedForm` (в
   localStorage, без нового поля) продолжают работать без миграции. В раннере читаем защитно:
   `getattr(req, "panel_variant", "plain")` — как `pipeline.py:2266`.
2. **eGames-вариант ⇒ nginx КОНТЕЙНЕРНЫЙ, 1:1 с апстримом.** Сервис `remnawave-nginx` (`nginx:1.28`,
   `network_mode: host`) добавляется в тот же `/opt/remnawave/docker-compose.yml` и монтирует
   `./nginx.conf:/etc/nginx/conf.d/default.conf:ro`. Один редактируемый файл `/opt/remnawave/nginx.conf`,
   который управление панелью перегенерирует. **Существующие caddy/host-nginx ветки не трогаем вообще** —
   `plain` идёт по нынешнему коду байт-в-байт.
3. **Их установщик не запускаем.** Портируем ТОЛЬКО текст конфигов в наши чистые генераторы (как
   `_NGINX_SITE`/`_PANEL_COMPOSE`), тестируемые без SSH.
4. **Имена контейнеров — НАШИ.** Апстрим зовёт backend `remnawave`, мы — `remnawave-backend`; на это имя
   завязаны `_PANEL_PROBES["panel"]` (`api/panel_deploy.py:88`), `_COMPOSE_UP_SCRIPT` (`:380`), проверка в
   `_install_panel` (`panel_pipeline.py:520-528`) и `replace_domain._PANEL_REPLACE_TPL:94`. Перенимаем ФОРМУ
   конфига, а не нейминг. Новый контейнер прокси зовём `remnawave-nginx` (как у апстрима — коллизий нет).
5. **Сертификаты — наши пути, БЕЗ wildcard.** Апстрим умеет wildcard на базовый домен; у нас правило §6
   (per-FQDN, иначе `429 rateLimited`). Выпуск остаётся через `pipeline.build_ssl_script` в
   `/etc/ssl/certs/<d>_fullchain.pem` + `/etc/ssl/private/<d>.key`, в контейнер прокидываем **пофайлово, на
   каждый домен, `:ro`** (не монтировать целиком `/etc/ssl/private` — там все приватные ключи хоста).
   Пути ВНУТРИ контейнера оставляем теми же, что снаружи → нулевая трансляция путей в шаблоне.
6. **Шагов остаётся 8.** eGames переиспользует слоты 5 (compose), 6 (SSL + генерация `nginx.conf`),
   7 (`up -d`, поднимающий и nginx-контейнер) — как `step_remnanode_vanilla` переиспользует индекс 11.
   `PANEL_STEP_LABELS` и его фронт-зеркало (`PanelWidget.tsx:18-27`) **не двигаем ни на строку**.
7. **eGames ⇒ только `reverse_proxy="nginx"`.** Caddy+eGames → 422 с внятным сообщением в
   `validate_by_target`. Причина: у апстрима carve-out `/api` задокументирован ТОЛЬКО для nginx; частично
   применённый Caddy хуже честного отказа. Caddy-вариант eGames — в бэклог «later».
8. **Cookie-gate на панели — опциональный тумблер `panel_cookie_gate` (дефолт OFF), режим panel-only.**
   Неавторизованный → `return 444` (как `src/nginx/install_panel.sh`), плюс carve-out `/oauth2/` с проверкой
   Referer (иначе ломается вход через Telegram OAuth). **Вне scope v1:** selfsteal-домен, decoy
   `418 → @unauthorized`, `unix:/dev/shm/nginx.sock ssl proxy_protocol` и порт 8443 — всё это существует
   у апстрима только ради single-box режима, где :443 занят Xray; на нашем панельном боксе :443 свободен.
9. **`/api` наружу — тумблер `api_public` (дефолт OFF).** Рендерит `location ^~ /api/ { … proxy_pass … }`
   ПЕРЕД `location /` (префикс `^~` выигрывает у `/`, поэтому gate обходится и защищает только Bearer-токен).
   **Честно в UI:** при выключенном gate carve-out функционально ничего не меняет — его ценность только в
   паре с gate. При включении — предупреждение про `IS_DOCS_ENABLED=false` (см. п.11).
10. **Вебхуки — разводим честно.** У Remnawave `WEBHOOK_ENABLED/URL/SECRET_HEADER` — **ИСХОДЯЩИЕ**
    (панель стучится на ваш URL), проксировать нечего; входящего webhook-прокси у апстрима нет нигде
    (ни location-блока, ни пункта меню). Поэтому в v1 отдаём **общий список публичных префиксов**
    `public_paths: list[str]` (дефолт пусто, максимум 8), которые пробиваются сквозь gate тем же
    `location ^~ <prefix>`, с подписью в UI: «для входящих колбэков сторонних сервисов (Telegram-бот,
    платёжки). НЕ для WEBHOOK_* Remnawave — те исходящие и прокси не требуют».
    **НЕ ПРОВЕРЕНО:** есть ли у Remnawave backend 2.x вообще входящий webhook-приёмник — подтверждён только
    ИСХОДЯЩИЙ env-контракт. Не обещать в UI приём вебхуков панели.
11. **`IS_DOCS_ENABLED=false`** пишем в `.env` при ПЕРВИЧНОЙ установке, когда `api_public` включён
    (`_env_file`). Для уже установленной панели `.env` идемпотентен (`__ENV_EXISTS__`, `panel_pipeline.py:354`)
    → новый ключ не доедет; в блоке «Доступ» показываем подсказку «выставьте `IS_DOCS_ENABLED=false` в разделе
    Переменные». Осознанное ограничение — не плодим второй путь записи `.env`.
12. **Секрет cookie-пары генерим на бэкенде** (`secrets.token_urlsafe`, как остальные секреты `_env_file`)
    и пишем ТОЛЬКО в `/opt/remnawave/nginx.conf` через **тихий канал** `get_script_output`.
    **В task-лог ссылка НЕ печатается** (строже апстрима, согласуется с «секреты не в логах») — в логе только
    строка «ссылка входа доступна в Управление панелью → Доступ». Read-back — отдельная тихая ручка
    (grep пары из `nginx.conf`, как `manage_panel.sh`). **Идемпотентность:** при перегенерации конфига
    существующая пара переиспользуется (иначе каждый reinstall инвалидировал бы ссылку у оператора).
13. **Никаких новых `Component`/`Action`.** Тумблеры живут в `savedForm` (та же запись `panel_jobs_<id>`),
    «Применить» = существующий `reinstall` компонента `reverse_proxy` (`api/panel_deploy.py:277-280`), который
    и так вызывает `_setup_reverse_proxy` → variant-ветка достаётся бесплатно. Это заметно проще, чем
    отдельные компоненты `api_proxy`/`webhook_proxy`, которые предлагала разведка (правило §2 — минимум кода).
14. **Порты хоста.** Контейнерный nginx в `network_mode: host` берёт :80/:443 — перед `up -d` скрипт
    останавливает хостовые `nginx`/`caddy` (`systemctl disable --now … || true`) и логирует это. Без этого
    первый же `up -d` упадёт на занятом порту.
15. **`_PANEL_PROBES` пополняем пробой `panel_nginx`** (`docker ps | grep remnawave-nginx`) — чтобы
    «Существующий сервер» отличал eGames-панель от plain. 3 строки, окупается.
16. **`is_safe_path` для этой задачи НЕДОСТАТОЧЕН.** `services/http_headers.py:16` `_PATH_RE` разрешает
    `? = &` — для префикса nginx-`location` это мусор. Заводим локальный строгий валидатор
    `^/[A-Za-z0-9._~/-]{0,63}$` в модели панели (и не «улучшаем» общий хелпер — правило §3).

## Стратегия

Ф1 (модель: `panel_variant` + тумблеры + гейты) → Ф2 (чистые генераторы eGames-конфигов) →
Ф3 (проводка в пайплайн/reinstall/detect + тихий read-back ссылки) → Ф4 (фронт: суб-таб варианта +
блок «Доступ» в управлении) → Ф5 (уборка мёртвого `cookie_gate` на ноде + починка `replace_domain` для plain).

---

### Ф1 — Модель: `panel_variant` + eGames-тумблеры + кросс-полевые гейты → verify: pytest

- **`backend/app/models/panel_deploy.py`** — добавить рядом с `reverse_proxy` (`:85`):
  - `panel_variant: Literal["plain", "egames"] = "plain"`;
  - `panel_cookie_gate: bool = False` — gate только для eGames-варианта;
  - `api_public: bool = False` — carve-out `location ^~ /api/`;
  - `public_paths: list[str] = Field(default_factory=list)` — дополнительные публичные префиксы.
- **Валидаторы:**
  - `field_validator("public_paths")`: ≤8 элементов, каждый — `re.fullmatch(r"/[A-Za-z0-9._~/-]{0,63}", p)`,
    обязан начинаться с `/`, дедуп. Мотив — строка попадает в root-run nginx-конфиг (см. развилку 16).
  - В `validate_by_target` (`:179`) добавить ветку: `if self.panel_variant == "egames"` →
    (a) `reverse_proxy != "nginx"` → `ValueError("eGames-вариант панели поддерживает только nginx")`;
    (b) `target == "subpage"` → `ValueError` (eGames-раскладка описывает панельный бокс; отдельная
    subpage-коробка остаётся plain);
    иначе (`plain`) → `panel_cookie_gate/api_public/public_paths` должны быть пустыми/False, иначе `ValueError`
    («доступны только в eGames-варианте») — не даём молча проглотить настройку, которая никуда не поедет.
  - Требования nginx-ветки (`cf_api_key`/`email`, `:189-200`) применяются автоматически, т.к. eGames форсит
    `reverse_proxy="nginx"` — новых правил про серты не пишем.
- **`_env_file` (`services/panel_pipeline.py:84-118`)** — при `panel_variant=="egames" and api_public`
  добавить `base["IS_DOCS_ENABLED"] = "false"` (до применения `extra_env`, чтобы оператор мог осознанно
  переопределить). Больше НИЧЕГО в `.env` не меняем.
- verify: `cd backend && python -m pytest tests/test_panel_deploy.py` — новые кейсы: дефолт `plain`
  (обратная совместимость), `egames`+`caddy` → `ValidationError`, `egames`+`target="subpage"` →
  `ValidationError`, тумблеры на `plain` → `ValidationError`, `public_paths` с `?`/`;`/пробелом → отказ,
  9 путей → отказ, `IS_DOCS_ENABLED=false` появляется только при `egames+api_public`.

---

### Ф2 — Чистые генераторы eGames-конфигов (compose + nginx.conf) → verify: pytest (без SSH)

Всё новое — **чистые функции** рядом с существующими билдерами (`panel_pipeline.py:73-325`), чтобы юнит-тесты
шли без SSH и сети (как `test_panel_deploy.py` для `_env_file`/`_nginx_conf`).

- **`_egames_compose_yml(req, cert_domains) -> str`** — `_PANEL_COMPOSE` + сервис:
  ```
  remnawave-nginx:
    image: nginx:1.28
    container_name: remnawave-nginx
    restart: always
    network_mode: host
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - /etc/ssl/certs/<d>_fullchain.pem:/etc/ssl/certs/<d>_fullchain.pem:ro   # на каждый домен
      - /etc/ssl/private/<d>.key:/etc/ssl/private/<d>.key:ro
    depends_on: [remnawave-backend]
  ```
  `network_mode: host` несовместим с `networks:` — сервис в `remnawave-network` НЕ входит и ходит на
  `127.0.0.1:3000`/`:3010` (backend уже публикует их на loopback, `panel_pipeline.py:134-136`).
- **`_egames_nginx_conf(req, targets, secret_pair) -> str`** — порт апстримового
  `src/nginx/install_panel.sh`, ужатый до panel-only:
  - `upstream remnawave { server 127.0.0.1:3000; }`, `upstream json { server 127.0.0.1:3010; }` (второй —
    только если подписка бандлится на этом же боксе, т.е. `_proxy_targets(req,"panel")` вернул её);
  - `map $http_upgrade $connection_upgrade { default upgrade; "" close; }`;
  - **только при `panel_cookie_gate`** — четыре мапа `$auth_cookie` / `$auth_query` / `$authorized` /
    `$set_cookie_header` на паре `(c1, c2)` + в панельном server-блоке `add_header Set-Cookie $set_cookie_header;`
    и `location / { if ($authorized = 0) { return 444; } proxy_pass http://remnawave; … }`;
    без gate — обычный `location / { proxy_pass http://remnawave; … }`;
  - **при `api_public`** — `location ^~ /api/ { … proxy_pass http://remnawave; … }` (полный набор
    `proxy_set_header` из апстримового `external-api.mdx`, см. РАЗВЕДКУ) **перед** `location /`;
  - на каждый `public_paths[i]` — такой же `location ^~ <prefix>` на `http://remnawave`;
  - **при gate** — `location ^~ /oauth2/ { if ($http_referer !~ "^https://oauth\.telegram\.org/") { return 444; } proxy_pass http://remnawave; }`;
  - server-блок подписки (если бандл) — `proxy_pass http://json`, БЕЗ gate;
  - `:80` → 301 на https (как в нынешнем `_NGINX_SITE:238-243`), и финальный
    `server { listen 443 ssl default_server; server_name _; ssl_reject_handshake on; }`;
  - пути сертов — те же, что в `_NGINX_SITE:251-252` (`/etc/ssl/certs/<d>_fullchain.pem`,
    `/etc/ssl/private/<d>.key`), т.к. монтируем пофайлово 1:1.
  Шаблон пишем **plain-строкой** с плейсхолдерами `__DOMAIN__`/`__PORT__`/`__C1__`/`__C2__` — нативные
  nginx-переменные (`$host`, `$http_upgrade`, `$proxy_add_x_forwarded_for`) должны пережить подстановку
  (тот же приём, что в `_NGINX_SITE` и `pipeline._render_remnanode_files:1168-1194`).
- **`_gate_pair() -> tuple[str,str]`** — `secrets.token_urlsafe(9)`-подобная пара из `[A-Za-z0-9]`
  (значение уезжает в `map`-регексп → без спецсимволов), плюс
  **`_read_gate_pair_script()`** — тихий `grep -A2 'map $http_cookie $auth_cookie' /opt/remnawave/nginx.conf`
  + извлечение пары (порт `manage_panel.sh`), чтобы reinstall переиспользовал уже выданную ссылку.
- verify: `cd backend && python -m pytest tests/test_panel_deploy.py` — новые чистые тесты:
  compose содержит `remnawave-nginx`+`network_mode: host` и по 2 mount-строки на домен; nginx-конфиг
  без gate НЕ содержит `$authorized`; с gate содержит все 4 мапа, `return 444` и `/oauth2/`;
  `api_public` даёт `location ^~ /api/` и он идёт РАНЬШЕ `location /` (проверка по индексу подстроки);
  `public_paths` рендерятся; секретная пара НЕ попадает ни в один другой артефакт (`.env`, compose);
  нативные `$host`/`$http_upgrade` уцелели.

---

### Ф3 — Проводка: пайплайн, reinstall, uninstall, detect, read-back → verify: pytest

- **`services/panel_pipeline.py::_install_panel` (`:473-529`)** — ветка по варианту, БЕЗ новых шагов:
  ```python
  is_eg = getattr(req, "panel_variant", "plain") == "egames"
  ```
  - шаг 5 — `_egames_compose_yml(...)` вместо `_compose_yml(req)`;
  - шаг 6 — `_setup_reverse_proxy_egames(ssh, task, req, targets)`: (a) серты — тем же циклом
    `upsert_a_record` + `build_ssl_script`, что и nginx-ветка (`:437-455`) — код переиспользуем, не копируем;
    (b) прочитать существующую пару (тихо) или сгенерировать; (c) записать `/opt/remnawave/nginx.conf`
    через тихий канал (`get_script_output`, как `.env`), в task-лог — только «[nginx] конфиг записан»;
    (d) `systemctl disable --now nginx caddy || true` (развилка 14);
  - шаг 7 — существующий `up_script` (`:503-518`) поднимает и `remnawave-nginx`; после проверки backend
    добавить проверку `docker ps --filter name=remnawave-nginx --filter status=running` → при провале
    FAILED с подсказкой `docker logs remnawave-nginx` (обычно = занятый :443 или битый конфиг).
  - в лог шага 6 при gate — строка «ссылка входа доступна в Управление панелью → Доступ» (без самой ссылки).
- **`api/panel_deploy.py`**:
  - `_panel_reinstall` (`:263-280`) — ветку добавлять НЕ нужно, если `_setup_reverse_proxy` сам диспетчеризует
    по `req.panel_variant` (предпочтительно: одна точка ветвления, как `_begin_step`-диспетчер на ноде);
  - `_UNINSTALL_SCRIPTS["reverse_proxy"]` (`:196-200`) — дописать
    `docker stop remnawave-nginx 2>/dev/null || true` (идемпотентно, plain не задевает);
  - `_PANEL_PROBES` (`:84-100`) — проба `panel_nginx`:
    `docker ps --format '{{.Names}}' | grep -q remnawave-nginx`;
  - **новая тихая ручка** `POST /api/panel/gate-link` (creds-per-request, как `EnvReadRequest` `:390-404`):
    `get_script_output(_read_gate_pair_script())` → `{"present": bool, "url": "https://<panel>/auth/login?c1=c2"}`;
    файла нет → 404, SSH не поднялся → 502. Ссылка **никогда** не логируется и не персистится.
- verify: `cd backend && python -m pytest` — по образцу `tests/test_node_detect.py:203-216`: замокать
  step-функции и убедиться, что при `panel_variant="egames"` (a) индексы 1..8 begun ровно по разу,
  (b) вызван eGames-генератор compose, (c) хостовый `apt-get install nginx` НЕ вызывался; при `plain` —
  байт-в-байт нынешнее поведение. Плюс: `gate-link` на панели без gate → 404; секрет не появляется в
  `task.logs` (тот же паттерн, что тест «секреты .env не в логе» в `test_panel_deploy.py`).

---

### Ф4 — Фронт: суб-таб варианта + блок «Доступ» в управлении → verify: tsc + npm test

- **`frontend/src/components/rw/PanelDeployForm.tsx`** — четыре места (`PanelFormData:19`,
  `PanelDeployPayload:48`, `PANEL_FORM_DEFAULT:68`, `toPayload`):
  - `panel_variant: "plain" | "egames"` + `panel_cookie_gate` / `api_public` / `public_paths: string[]`;
  - две кнопки-суб-таба **Обычная / eGames** прямо над сегментом `reverse_proxy` — копия разметки
    `DeployForm.tsx:531-540`; при `egames` **принудительно** ставим `reverse_proxy="nginx"` и дизейблим
    сегмент прокси (плюс поясняющая строка, как `DeployForm.tsx:541-545`);
  - variant-гейтед тумблеры (только при `egames`): «Cookie-gate (скрыть панель от сканеров)»,
    «Внешний доступ к /api» + textarea «Публичные пути (по одному в строке)»;
  - `validatePanelForm` зеркалит серверные гейты 1:1 (правило файла): eGames+caddy, eGames+subpage,
    тумблеры на plain, формат/лимит `public_paths`;
  - `toPayload` обнуляет eGames-поля при `plain` (как он уже обнуляет `cf_api_key`/`sub_server`).
- **`frontend/src/components/rw/PanelManageModal.tsx`** — новый блок **«Доступ»** внутри вкладки
  «Компоненты» (не новая вкладка — экономим поверхность), рендерится только при
  `job.savedForm.panel_variant === "egames"`:
  - три контрола (gate / api_public / public_paths) поверх локальной записи, кнопка **«Применить»** =
    `onEditJob(patch)` + существующий `runOp("reverse_proxy","reinstall", …)` (`:125-131`);
  - при включённом `api_public` — амбер-подсказка «выставьте `IS_DOCS_ENABLED=false` в разделе Переменные»
    (развилка 11) и предупреждение «`/api` доступен снаружи, защищён только Bearer-токеном»;
  - подпись у публичных путей: «для входящих колбэков сторонних сервисов; вебхуки Remnawave — ИСХОДЯЩИЕ,
    прокси не требуют» (развилка 10);
  - кнопка **«Показать ссылку входа»** → `POST /api/panel/gate-link` с кредами записи, показ в модалке с
    «Скопировать», без записи в state/localStorage.
- **`rw/PanelWidget.tsx:18-27` НЕ трогаем** — шагов по-прежнему 8 (ловушка-близнец
  `DEPLOY_STEPS`/`STEP_LABELS` на ноде).
- verify: `npx --no-install tsc --noEmit`; `npm test` — юнит на `validatePanelForm` (eGames+caddy отклоняется,
  тумблеры на plain отклоняются, кривой путь отклоняется) и на `toPayload` (обнуление при plain);
  ручной preview: создать eGames-панель в форме, увидеть блок «Доступ» в управлении.

---

### Ф5 — Уборка мёртвого `cookie_gate` на ноде + починка `replace_domain` для plain → verify: pytest + tsc

Две независимые мелочи; каждую можно отгружать отдельным коммитом.

- **БАГ A — убираем ложное обещание.** Cookie-gate на НОДЕ закрывать нечего: нодовый nginx отдаёт
  маскировочный сайт (`pipeline.py:1144-1156`), который по замыслу ДОЛЖЕН быть публично видимым — gate на
  нём ломает саму маскировку; панельный gate живёт в Ф1–Ф4 этого плана. Поэтому: удалить
  `cookie_gate` из `models/deploy.py:45-47`, тумблер из `DeployForm.tsx:34,102,565-569`, ассерт из
  `backend/tests/test_deploy.py:112`; **поправить CLAUDE.md §9a** (сейчас читается так, будто тумблер
  работает). Старые записи `deploy_jobs_<id>.savedForm` с лишним `cookie_gate` не сломаются — pydantic v2 по
  умолчанию игнорирует неизвестные поля (`extra="ignore"`). Альтернатива (НЕ выбрана): реализовать gate в
  `_NGINX_TPL` — это ухудшит маскировку и никем не запрошено (§2).
- **БАГ B — plain-вариант `replace_domain`.** В `services/replace_domain.py::_PANEL_REPLACE_TPL` добавить
  вторую фазу для ХОСТОВЫХ конфигов: sed по `/etc/caddy/Caddyfile` и `/etc/nginx/conf.d/remnawave-panel.conf`
  (если существуют, тот же экранированный `OLD_ESC`), затем `nginx -t && systemctl reload nginx || true` и
  `caddy validate … && systemctl reload caddy || true`. Идемпотентность сохраняется (второй прогон не находит
  OLD). eGames-вариант эта правка не задевает (его конфиг уже в `/opt/remnawave`, рестарт даёт
  `docker compose up -d`).
- verify: `cd backend && python -m pytest tests/test_deploy.py tests/test_replace_domain.py` (новый кейс:
  скрипт содержит оба хостовых пути и reload-строки; старые кейсы зелёные); `npx --no-install tsc --noEmit`;
  грепом убедиться, что `cookie_gate` больше не встречается в репозитории.

## РАЗВЕДКА (факты)

Апстрим скачан на ветке `main` (не на теге релиза) в ходе разведки; все цитаты — из его собственных
`src/` и `docs/`, не из отрендеренной вики.

- **`/api` наружу — РУЧНОЙ шаг документации, установщик его не делает.**
  <https://github.com/eGamesAPI/remnawave-reverse-proxy/blob/main/docs/src/content/docs/configuration/external-api.mdx>
  предписывает вставить в панельный server-блок:
  `location ^~ /api/ { proxy_http_version 1.1; proxy_pass http://remnawave; proxy_set_header Host $host;
  Upgrade $http_upgrade; Connection $connection_upgrade; X-Real-IP $remote_addr;
  X-Forwarded-For $proxy_add_x_forwarded_for; X-Forwarded-Proto $scheme; X-Forwarded-Host $host;
  X-Forwarded-Port $server_port; proxy_send_timeout 60s; proxy_read_timeout 60s; }`.
  `^~` выигрывает у `location /` ⇒ gate обходится, остаётся только Bearer-токен. Блок `:::danger:::`
  требует после этого поставить `IS_DOCS_ENABLED=false` (иначе наружу торчат `SWAGGER_PATH=/docs`,
  `SCALAR_PATH=/scalar`). Задокументированная АЛЬТЕРНАТИВА — не открывать `/api`, а слать gate-куку из
  API-клиента вместе с `Authorization: Bearer`.
- **Входящего webhook-прокси у апстрима НЕТ.** Единственные webhook-артефакты во всём репозитории — три
  строки `.env`, одинаковые у всех четырёх установщиков (`src/nginx/install_panel.sh` ~L116-123):
  `WEBHOOK_ENABLED=false`, `WEBHOOK_URL=https://your-webhook-url.com/endpoint`,
  `WEBHOOK_SECRET_HEADER=<захардкоженный 64-символьный плейсхолдер, одинаковый на каждой установке>`.
  Это ИСХОДЯЩИЕ уведомления Remnawave. Наш `secrets.token_hex(32)` (`panel_pipeline.py:109`) строго лучше и
  уже удовлетворяет требованию «ровно 64 символа a-zA-Z0-9».
- **Панельный nginx-конфиг апстрима** (`src/nginx/install_panel.sh`, heredoc → `/opt/remnawave/nginx.conf`):
  `upstream remnawave {127.0.0.1:3000}` / `upstream json {127.0.0.1:3010}`;
  `map $http_cookie $auth_cookie { default 0; "~*${c1}=${c2}" 1; }`; `map $arg_${c1} $auth_query {…}`;
  `map "$auth_cookie$auth_query" $authorized { "~1" 1; default 0; }`;
  `map $arg_${c1} $set_cookie_header { "${c2}" "${c1}=${c2}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=31536000"; default ""; }`;
  панельный сервер — `listen 443 ssl; http2 on;` + `add_header Set-Cookie $set_cookie_header;` +
  `location / { if ($authorized = 0) { return 444; } proxy_pass http://remnawave; }` +
  `location ^~ /oauth2/ { if ($http_referer !~ "^https://oauth\.telegram\.org/") { return 444; } … }`;
  сервер подписки — `proxy_pass http://json` + `proxy_intercept_errors on; error_page 400 404 500 502 @redirect;`
  и `location @redirect { return 444; }`; плюс `server { listen 443 ssl default_server; server_name _;
  ssl_reject_handshake on; }`. Пара `c1`/`c2` — два случайных токена (`generate_user()`), ссылка входа —
  `https://panel/auth/login?c1=c2`.
- **Комбинированный panel+node конфиг** (`src/nginx/install_panel_node.sh`) отличается тем, что все серверы
  слушают `unix:/dev/shm/nginx.sock ssl proxy_protocol` (на :443 сидит Xray), неавторизованных не режет, а
  подсовывает decoy (`return 418` → `error_page 418 = @unauthorized` → `root /var/www/html`), и требует
  ТРЕТИЙ домен (SELFSTEAL). Установщик прямо запрещает совпадение любых двух из трёх доменов. **Всё это —
  вне scope v1** (развилка 8). Любопытно: наш НОДОВЫЙ `_NGINX_TPL` (`pipeline.py:1146,1158-1164`) уже
  использует ровно эту схему (`unix:/dev/shm/nginx.sock ssl proxy_protocol` + `default_server
  ssl_reject_handshake` + `return 444`) — на ноде архитектура апстрима уже перенята, не хватало только мапов.
- **Compose/env апстрима vs наш** (`src/nginx/install_panel.sh` ~L149-311): backend-контейнер зовётся
  `remnawave` (у нас `remnawave-backend`); postgres публикуется на `127.0.0.1:6767` с паролем `postgres`;
  valkey ходит через unix-сокет (`REDIS_SOCKET`), у нас — TCP `REDIS_HOST/REDIS_PORT`;
  `remnawave-nginx` = `nginx:1.28`, `network_mode: host`, `./nginx.conf:/etc/nginx/conf.d/default.conf:ro`;
  серты хоста бинд-моунтятся как `/etc/nginx/ssl/$domain/…`. Лишние относительно нас ключи `.env`:
  `API_INSTANCES`, `JWT_AUTH_LIFETIME=168` (у нас 48), `SWAGGER_PATH`, `SCALAR_PATH`, `IS_DOCS_ENABLED`,
  `TELEGRAM_NOTIFY_*`, `BANDWIDTH_USAGE_NOTIFICATIONS_*`, `NOT_CONNECTED_USERS_NOTIFICATIONS_*`,
  `CLOUDFLARE_TOKEN`. Мы берём из этого списка ровно один ключ — `IS_DOCS_ENABLED` (развилка 11).
- **Caddy-эквивалент** (`src/caddy/install_panel.sh` → `/opt/remnawave/Caddyfile`): `@has_token_param` +
  `header +Set-Cookie`, `@unauthorized { not path /oauth2/*; not header Cookie *c1=c2* } handle { abort }`,
  `@oauth2_bad` по Referer, затем `reverse_proxy {$BACKEND_URL}`. Аналога `/api`-carve-out в докуме́нтах НЕТ
  (external-api.mdx — только про nginx) ⇒ развилка 7.
- **`manage_panel.sh`** (меню управления апстрима): start/stop/update/logs/`docker exec -it remnawave remnawave`
  + открытие-закрытие порта **8443** (`sed` вставляет `listen 8443 ssl;` в панельный server-блок +
  `ufw allow … 8443`, перепечатывает ссылку `?c1=c2`). 8443 существует ТОЛЬКО потому, что в single-box режиме
  :443 занят Xray, и **gate он не обходит** (тот же server-блок) ⇒ ортогонален `/api` и нам не нужен.
  Ценная деталь для нас — способ read-back пары:
  `grep -A 2 "map \$http_cookie \$auth_cookie" | grep -oP '~*\K\w+(?==)'`.
- **Пост-инсталл API апстрима** (`src/api/remnawave_api.sh`): регистрирует суперадмина
  (`POST /api/auth/register`), тянет `GET /api/keygen`, генерит REALITY-ключи, создаёт config-profile/node/host
  и **создаёт API-токен** (`POST /api/tokens`), после чего седит его в compose страницы подписок. Мы
  оставляем `REMNAWAVE_API_TOKEN` пустым (`panel_pipeline.py:296`) — закрытие этого разрыва независимо от
  плана D и здесь **вне scope**.
- **`https://wiki.egam.es/introduction/overview/#issues` — это НЕ список известных проблем**, а раздел «сообщите
  об issue» (три строки). Полезное на той странице: баннер «EDUCATIONAL EXAMPLE … NOT FOR PRODUCTION USE» и
  описание архитектуры «Xray на :443 → сокет → nginx».
- **НЕ ПРОВЕРЕНО (и не выдавать за факт):** (1) есть ли у Remnawave backend 2.x входящий webhook-приёмник —
  подтверждён только исходящий env-контракт; (2) отличается ли содержимое тега релиза от `main`;
  (3) отрендеренная навигация вики; (4) ни один из этих конфигов НЕ проверялся на живом боксе — приёмка
  eGames-варианта требует реальной установки.

## Критерии готовности плана D

- `PanelDeployRequest.panel_variant` = `plain` по умолчанию; **старые записи `panel_jobs_<id>` работают без
  изменений**, plain-путь байт-в-байт прежний (тест-набор `test_panel_deploy.py` зелёный без правок логики).
- eGames-вариант ставит панель с контейнерным `remnawave-nginx` (`nginx:1.28`, `network_mode: host`), единым
  редактируемым `/opt/remnawave/nginx.conf` и пофайловым `:ro`-монтированием сертов; `PANEL_STEP_LABELS`
  по-прежнему 8 и совпадает с фронт-зеркалом `PanelWidget.tsx:18-27`.
- Cookie-gate и `location ^~ /api/` включаются тумблерами (дефолт OFF), рендерятся ровно так, как в апстриме,
  `/api`-блок стоит РАНЬШЕ `location /` (проверено юнит-тестом по индексу подстроки); при `api_public`
  первичная установка пишет `IS_DOCS_ENABLED=false`, а UI подсказывает про «Переменные» для уже стоящей панели.
- Секрет gate-пары не попадает ни в task-лог, ни в `.env`, ни в localStorage; ссылка входа отдаётся только
  тихой ручкой `POST /api/panel/gate-link`; повторный reinstall прокси **сохраняет** уже выданную пару.
- В UI нигде не обещан «прокид вебхуков Remnawave»: публичные пути подписаны как carve-out для входящих
  колбэков сторонних сервисов, с явной оговоркой, что `WEBHOOK_*` панели — исходящие.
- eGames+Caddy и eGames+`target="subpage"` отвергаются с внятным 422 на бэкенде и до сабмита на фронте.
- `reverse_proxy` reinstall на eGames-панели перегенерирует `nginx.conf` и перезапускает контейнер (не трогая
  хостовый nginx/caddy); uninstall дополнительно останавливает `remnawave-nginx`; detect различает
  eGames-панель пробой `panel_nginx`.
- Мёртвый `cookie_gate` на ноде удалён (модель + тумблер + тест), **CLAUDE.md §9a исправлена**;
  `replace_domain.panel_replace_script` для plain-варианта правит и перезагружает
  `/etc/caddy/Caddyfile` + `/etc/nginx/conf.d/remnawave-panel.conf`.
- `cd backend && python -m pytest` и `npx --no-install tsc --noEmit` + `npm test` зелёные; CLAUDE.md §7c
  дополнена (`panel_variant`, контейнерный nginx, новая ручка, тумблеры) при реализации.
- **Приёмка на живом боксе обязательна** (конфиги нигде не проверялись): установка eGames-панели, вход по
  ссылке с gate, отказ 444 без куки, `curl` к `/api` с Bearer при `api_public`, смена домена.
