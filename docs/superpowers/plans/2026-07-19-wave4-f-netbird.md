# Волна 4 · План F — E8: self-hosted Netbird mesh-оверлей панель↔ноды

> **Статус (2026-07-21):** ✅ Ф1-Ф3. Backend `services/netbird.py`+`api/netbird.py` (control-plane deploy via
> getting-started.sh, PAT в Fernet-волте, setup-key через management API, agent join с `--disable-*-routes`;
> `test_netbird.py` 7 зелёных). Frontend Settings→«Инфраструктура» (`settings/InfraTab.tsx`).
> Отклонение: тумблер `join_netbird` в деплой-пайплайн НЕ вшивался (14-шаговый инвариант) — нода подключается
> отдельной операцией `/agent/join`. pytest+tsc зелёные; живой деплой CP — build-ahead (нет Netbird-сервера).

> eGames-вики (`/configuration/netbird/`). Приватная WireGuard-mesh между панелью и нодами для control-трафика
> вместо публичного IP/SSH. **Self-hosted control plane** (свой Netbird, без облака).
> Затрагивает: `services/netbird.py` (новый), `api/netbird.py` (новый), `frontend/rw/*` (управление), опц.
> интеграция в деплой (агент на ноде + IP-маппинг в Remnawave).

## Контекст (как есть)

- Панель↔ноды связаны напрямую по публичным IP/SSH; NODE_PORT открыт только для IP панели (firewall).
- Netbird (eGames-дока): WireGuard mesh-оверлей, setup-keys для авто-регистрации пиров, IP-маппинг в
  интерфейсе Remnawave; firewall для inter-peer связи.

## Развилки (закреплены)

- **Self-hosted control plane** — разворачиваем свой Netbird (management + signal + coturn + dashboard),
  без зависимости от Netbird SaaS. Агенты на нодах и панели.

## Разведка (обязательно ПЕРЕД реализацией)

- **R1.** Официальный self-hosted Netbird: docker-compose стека (management/signal/coturn/dashboard/zitadel-IdP?),
  порты, TLS, минимальные требования; способ выпустить **setup-key** программно (API management) для авто-
  регистрации агентов. Задокументировать (это самый тяжёлый внешний компонент волны).
- **R2.** Как агент на ноде поднимается неинтерактивно (`netbird up --setup-key … --management-url …`) и как
  получить оверлейный IP пира для IP-маппинга в Remnawave.
- **R3.** Влияние на маршрутизацию/SSH: оверлей — только для control-трафика; НЕ перехватывать дефолт-роут
  (как урок WARP `Table=off`, §6) — иначе потеря SSH.

## Стратегия

Ф1 (backend: control plane) → Ф2 (backend: агенты на нодах/панели + setup-keys) → Ф3 (frontend: управление).

---

### Ф1 — Развёртывание Netbird control plane → verify: pytest + docker

`services/netbird.py` + `api/netbird.py` (`/api/netbird`, под `require_account`):
- `control_plane_deploy_script(domain, …)` — docker-compose self-hosted Netbird (R1) на выбранном боксе
  (выделенный сервер, свои SSH-креды per-request; TLS для dashboard/management). Реестр per-account
  (`netbird.json`: management_url, admin-креды/токен в Fernet-волте — как MCP/rules секреты, module-scoped).
- `POST /control-plane/deploy` (стрим-Task), `GET /control-plane/status`.
- verify: `docker compose config`; `test_netbird.py` (генераторы, валидация домена, шифрование секрета at-rest).

---

### Ф2 — Агенты на нодах/панели + setup-keys → verify: smoke

- `create_setup_key()` — через management API (R1) выпустить одноразовый/многоразовый setup-key.
- `agent_install_script(management_url, setup_key)` — неинтерактивная установка агента (R2), `netbird up` с
  `--setup-key`/`--management-url`; **без перехвата дефолт-роута** (R3). Вернуть оверлейный IP пира.
- **Деплой ноды**: тумблер `join_netbird` — если включён, после установки ноды поднять агента и (опц.) прописать
  оверлейный IP ноды в Remnawave (IP-маппинг); firewall — разрешить inter-peer.
- verify: smoke генераторов; ручной сценарий (control plane + агент на тест-ноде, пир виден, SSH жив).

---

### Ф3 — Frontend: управление Netbird → verify: tsc + preview

- Раздел/блок «Netbird»: развернуть control plane (выбор бокса + креды), статус, список пиров (панель+ноды),
  выпуск setup-key. Тумблер `join_netbird` в форме деплоя ноды.
- verify: `tsc`, preview — развернуть control plane, увидеть пиров, нода с `join_netbird` появляется в mesh.

## РАЗВЕДКА ВЫПОЛНЕНА (2026-07-19) — факты для реализации

- **Стек (Quickstart, рекомендуемый):** `netbirdio/netbird-server:latest` (комбо management+signal+relay+coturn,
  **встроенный Dex — внешний IdP НЕ нужен**) + `netbirdio/dashboard:latest` + `netbirdio/reverse-proxy:latest` +
  `traefik:v3.6`. Разворот скриптом `getting-started.sh`
  (`github.com/netbirdio/netbird/releases/latest/download/getting-started.sh`), env `NETBIRD_DOMAIN`,
  `NETBIRD_LETSENCRYPT_EMAIL`. Порты **TCP 80/443, UDP 3478** (+опц. UDP 51820). Нужен FQDN + DNS A-запись; TLS
  авто через Traefik/LE. Мин: 1 CPU / 2 GB, Docker + jq. (Zitadel-путь устарел — НЕ использовать.)
- **Setup-key (R1):** `POST https://<domain>/api/setup-keys`, заголовок `Authorization: Token <PAT>`, тело
  `{"name":"node-assistant","type":"reusable","expires_in":31536000,"auto_groups":[],"usage_limit":0}` → ответ
  поле **`key`**. PAT создаётся под **service user** в Dashboard → хранить Fernet-encrypted (vault §8).
- **Агент на ноде (R2):** `curl -fsSL https://pkgs.netbird.io/install.sh | sh` →
  `netbird up --setup-key <KEY> --management-url https://<domain>:443 --disable-client-routes
  --disable-server-routes`. Overlay-IP: `netbird status --json | jq -r '.netbirdIp'` (сеть 100.64.0.0/10).
- **R3 (не потерять SSH):** по умолчанию NetBird безопасен (маршрутизирует только overlay, НЕ 0.0.0.0/0). Флаги
  **`--disable-client-routes --disable-server-routes` ОБЯЗАТЕЛЬНЫ** (аналог WARP `Table=off`) — не принимать
  push-маршруты/exit-node. (⚠️ issue #3984 — проверить `--disable-server-routes` на конкретной версии агента.)
- Источники: docs.netbird.io/selfhosted/selfhosted-quickstart, /api/resources/setup-keys, /how-to/access-netbird-public-api, wiki.egam.es/configuration/netbird.

## Критерии готовности плана F

- Self-hosted Netbird control plane разворачивается; агенты на нодах/панели подключаются по setup-key **без
  потери SSH** (без перехвата дефолт-роута).
- Тумблер `join_netbird` на деплое; оверлейные IP видны/маппятся.
- Разведка R1–R3 закрыта и записана в CLAUDE.md. `pytest` + `docker compose config` + preview + ручной smoke.
