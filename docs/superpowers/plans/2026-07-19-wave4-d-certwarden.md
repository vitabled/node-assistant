# Волна 4 · План D — E6: Certwarden (централизованный ACME)

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

## Критерии готовности плана D

- Certwarden-сервер разворачивается на выбранном боксе; ноды-клиенты тянут серты + авто-рестарт.
- Тумблер `use_certwarden` заменяет per-node acme на деплое; «Управление SSL» знает про Certwarden.
- Разведка R1–R3 закрыта и записана в CLAUDE.md. `pytest` + `docker compose config` + preview + ручной smoke.
