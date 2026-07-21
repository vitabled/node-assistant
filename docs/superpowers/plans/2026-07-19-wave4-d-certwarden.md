# Волна 4 · План D — E6: Certwarden (централизованный ACME)

> **Статус (2026-07-21):** ✅ Ф1-Ф3. Backend `services/certwarden.py`+`api/certwarden.py` (server deploy + наш
> pull-and-restart клиент; `test_certwarden.py` 7 зелёных). Frontend Settings→«Инфраструктура» (`settings/InfraTab.tsx`).
> Отклонение: тумблер `use_certwarden` в деплой-пайплайн НЕ вшивался (14-шаговый инвариант) — клиент ставится
> отдельной операцией `/client/install`. pytest+tsc зелёные; живой деплой сервера — build-ahead (нет Certwarden-сервера).

> eGames-вики. Один Certwarden-сервер управляет сертификатами всех нод; клиенты на нодах тянут серты и
> авто-рестартят Docker-контейнеры при обновлении. Placement — **выбор оператора** (бокс панели ИЛИ выделенный).
> Затрагивает: `services/certwarden.py` (новый), `api/certwarden.py` (новый), `models` (запрос), `frontend/rw/*`
> (управление), опц. интеграция в deploy-пайплайн ноды как альтернатива acme.sh.

## Контекст (как есть)

- Сейчас серты выдаются **per-node** (`step_ssl`, провайдеры cloudflare/letsencrypt/zerossl) + «Управление SSL»
  (Ф10). Каждый сервер настраивает ACME сам.
- Certwarden (из eGames-вики `/configuration/certwarden/`) — централизованный ACME-клиент: сервер + клиент-
  контейнеры на панели/нодах, тянут серты, **авто-рестарт контейнеров** после обновления; DNS-01 Cloudflare +
  Let's Encrypt.

## Развилки (закреплены)

- Placement Certwarden-сервера: **на боксе панели ИЛИ выделенный сервер** — оператор выбирает при развёртывании.
- Ноды/панель — клиенты; авто-рестарт контейнеров при renewal.

## Разведка (обязательно ПЕРЕД реализацией)

- **R1.** Официальный Certwarden: docker-образ(ы) сервера и клиента, compose, порты, как хранит/выдаёт серты,
  API для регистрации доменов/клиентов (см. eGames-доку + upstream repo). Задокументировать.
- **R2.** Как клиент на ноде авто-рестартит нужные контейнеры (hook/скрипт) — и как это увязать с нашими
  `remnanode`/`nginx` контейнерами.
- **R3.** Совместимость с текущим `step_ssl`: Certwarden — **альтернатива** per-node acme.sh; ноды на Certwarden
  не должны параллельно гонять acme (тумблер «использовать Certwarden» на деплое).

## Стратегия

Ф1 (backend: развёртывание сервера) → Ф2 (backend: клиент на ноде + интеграция в деплой) → Ф3 (frontend: управление).

---

### Ф1 — Развёртывание Certwarden-сервера → verify: pytest + docker

`services/certwarden.py`:
- `server_deploy_script(placement, domain, cf_token?/email, …)` — docker-compose Certwarden-сервера (R1) на
  выбранном боксе: **placement=`panel`** → на сервере панели (join `node-assistant`/панельной сети),
  **placement=`dedicated`** → отдельный сервер (свои SSH-креды, per-request). Reverse-proxy/SSL для UI сервера.
- `api/certwarden.py` (`/api/certwarden`, под `require_account`): `POST /server/deploy` (стрим-Task, creds
  per-request), `GET /server/status`. Реестр сервера per-account (`certwarden.json`: base_url/placement).
- verify: `docker compose config` сгенерированного compose; `test_certwarden.py` (генераторы скриптов,
  валидация placement).

---

### Ф2 — Клиент на ноде + интеграция в деплой → verify: smoke

- `client_install_script(server_url, node_domain, restart_targets)` — клиент-контейнер на ноде: тянет серт с
  сервера, ставит в `/etc/ssl/...` (те же пути, что ждёт `remnanode`), hook авто-рестарта контейнеров (R2).
- **Деплой ноды**: тумблер `use_certwarden` (в `DeployRequest`) — если включён, шаг 10 (`step_ssl`) **заменяется**
  установкой Certwarden-клиента (acme.sh не гоняем). Non-fatal? Нет — серт критичен, но ошибку показываем.
- «Управление SSL» — опция «через Certwarden» рядом с провайдерами.
- verify: smoke генераторов; ручной сценарий (сервер + клиент на тест-ноде, серт приезжает, контейнер
  рестартится).

---

### Ф3 — Frontend: управление Certwarden → verify: tsc + preview

- Раздел/блок «Certwarden» (в установке панели или отдельным пунктом): развернуть сервер (выбор placement +
  креды целевого бокса), статус, список доменов/клиентов. Тумблер `use_certwarden` в форме деплоя ноды.
- verify: `tsc`, preview — развернуть сервер, увидеть статус; нода с `use_certwarden` берёт серт централизованно.

## РАЗВЕДКА ВЫПОЛНЕНА (2026-07-19) — факты для реализации

- **Образы (ghcr, НЕ Docker Hub):** сервер `ghcr.io/gregtwallace/certwarden:latest`, клиент
  `ghcr.io/gregtwallace/certwarden-client:latest`. (Проект = бывш. LeGo CertHub; образ сервера включает acme.sh.)
- **Сервер (R1):** порты `4050` (HTTP UI/API), `4055` (HTTPS UI/API), `4060` (HTTP-01 challenge). Том
  `./data:/app/data` (sqlite + config.yaml + логи). TLS UI: либо внешний nginx/Caddy (сервер на `127.0.0.1:4050`),
  либо self-issued серт (`config.yaml::certificate_name` → HTTPS на 4055). eGames-вариант: за nginx на localhost.
- **Download API (R1) — для неинтерактивной выдачи на ноды:** `GET https://<server>/certwarden/api/v1/download/
  certificates/[Name]` (заголовок `X-API-Key: <certApiKey>`) → fullchain; `/privatekeys/[Name]`
  (`X-API-Key: <keyApiKey>`) → privkey. У серта и ключа — отдельные API-ключи. Регистрация/выпуск серта — через
  Web UI / внутренний REST (в публичных доках не детализирован — только download-API).
- **Клиент/авто-рестарт (R2):** два пути. (a) **Наш скрипт** на ноде: cron curl'ит два эндпоинта в
  `/etc/ssl/...` + `docker restart` сам (docker.sock CW-клиенту НЕ отдаём — предпочтительно по безопасности).
  (b) Официальный `certwarden-client`: env `CW_CLIENT_RESTART_DOCKER_CONTAINER0=<container>`,
  `CW_CLIENT_SERVER_ADDRESS`, `CW_CLIENT_CERT/KEY_NAME`+`_APIKEY`, `CW_CLIENT_AES_KEY_BASE64`, монтирует
  docker.sock + слушает push на `5055`, рестарт в окне `CW_CLIENT_FILE_UPDATE_TIME_START/_END`.
- **Челленджи (R3):** DNS-01 Cloudflare (`dns_01_cloudflare`, `api_token` c Zone:Edit — eGames использует это),
  HTTP-01 (`http_01_internal`, порт 4060), `dns_01_acme_sh` (любой провайдер acme.sh). Настройка в `config.yaml`.
- Мин. требования сервера в доках НЕ опубликованы (оценка ~128–256 MB RAM, Go+sqlite, лёгкий).
- Источники: github.com/gregtwallace/certwarden (+ docker-compose.yml), certwarden.com/docs/using_certificates/{api_calls,client}, config.example.yaml, wiki.egam.es/configuration/certwarden.

## Критерии готовности плана D

- Certwarden-сервер разворачивается на выбранном боксе; ноды-клиенты тянут серты + авто-рестарт.
- Тумблер `use_certwarden` заменяет per-node acme на деплое; «Управление SSL» знает про Certwarden.
- Разведка R1–R3 закрыта и записана в CLAUDE.md. `pytest` + `docker compose config` + preview + ручной smoke.
