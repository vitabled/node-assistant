# node-assistant: деплой нод Remnawave (egames/vanilla) — детали из кода

Проверено по локальному чекауту `/opt/agent-cli/node-installer`
(backend/app/services/pipeline.py, remnawave_client.py, node_ops.py), август 2026.
Полная дока проекта — `knowledge/node-assistant.md`.

## egames-вариант (`step_remnanode` / `_render_remnanode_files`)

Compose `/opt/remnanode/docker-compose.yml`, ОБА сервиса `network_mode: host`:

- `remnawave-nginx` (nginx:1.28) — маскировочный сайт + XHTTP.
  **Слушает ТОЛЬКО unix-сокет** `listen unix:/dev/shm/nginx.sock ssl proxy_protocol` —
  у nginx НЕТ TCP-порта! Публичный 443 держит Xray: Reality `dest` → `unix:/dev/shm/nginx.sock`
  (потому и `proxy_protocol`), XHTTP: `location $path → grpc_pass unix:/dev/shm/xrxh.socket`.
  Серт — acme.sh per-FQDN, монтируется в nginx через `/etc/letsencrypt/live/<domaincert>/`
  (симлинк на `/etc/ssl/certs/<domaincert>_fullchain.pem`).
- `remnanode` (remnawave/node:latest) — только `NODE_PORT` + `SECRET_KEY`, `cap_add: NET_ADMIN`.

Control-API ноды торчит напрямую на **TCP `NODE_PORT`** (дефолт 2222) в host-сети.
Nginx в канале панель→нода НЕ участвует.

## vanilla-вариант (`step_remnanode_vanilla` / `_VANILLA_COMPOSE_TPL`)

Официальный `remnawave/node`, host-сеть, только `NODE_PORT` + `SECRET_KEY`, без
nginx/маскировки/локального SSL — конфиг Xray приходит от панели.

## Создание ноды в панели (pipeline.py ~2030, `RemnavaveClient.create_node`)

- `address = req.ip` — **голый IP**, не домен; `port = req.remnanode_port` (2222).
- `SECRET_KEY` берётся с панели через `GET /api/keygen` — **один ключ на всю панель**
  (не per-node); панель сама хранит его и использует как mTLS-клиент.
- Хосты/инбаунды/сквады создаются отдельными шагами; `activePluginUuid` — один плагин на ноду.
- Переустановка remnanode через `/api/node/step` при отсутствии сохранённого токена
  пере-фетчит ключ: `_fetch_node_secret_key` → `GET /api/keygen` (нужны Настройки→Remnawave:
  `panel_url` + `api_token`).

## Питфоллы

- **SSH ↔ NODE_PORT конфликт:** дефолты node-assistant `change_ssh_port=true`,
  `new_ssh_port=2222` и `remnanode_port=2222` совпадают. Если SSH сел на 2222 — remnanode
  не поднимется (EADDRINUSE) либо панель стучится не в тот TLS-эндпоинт. Проверка:
  `ss -tlnp | grep -E ':(22|2222)\b'`.
- **`write EPROTO … alert handshake failure` (alert 40) на стороне панели** — диагноз и фиксы
  в SKILL.md («Диагностика»). Частое: на `NODE_PORT` отвечает не mTLS-сервер ноды
  (nginx/CF/другой сервис) либо устаревший `SECRET_KEY` после пересоздания панели.
- Актуальный ключ: `docker exec -it remnawave cli` → «Get SECRET_KEY for Node».
- У ноды в логах есть маркер `Expected SNI: <uuid>.<…>.app` — SNI серта её mTLS-сервера;
  если панель не ходит напрямую на IP:порт, хендшейк падает именно с alert 40.
