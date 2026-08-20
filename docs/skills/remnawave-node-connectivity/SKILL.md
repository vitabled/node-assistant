---
name: remnawave-node-connectivity
description: "Remnawave node won't connect to panel: EPROTO/alert 40."
---

# Remnawave: канал панель↔нода и деплой нод

Класс задач: **нода не подключается к панели / не поднимается / TLS-ошибки на control-канале**,
а также деплой нод через **node-assistant** (egames/vanilla). Про конфиги Xray/Reality/Caddy —
см. `remnawave-xray`; этот скилл про транспорт панель→нода и жизненный цикл ноды.

## Анатомия control-канала (проверено по коду, авг 2026)

- Связь строго **панель → нода**: панель — TLS-клиент, нода — HTTPS-сервер на `NODE_PORT`
  (дефолт 2222). Нода наружу к панели НЕ ходит.
- Транспорт: **mTLS** (`minVersion TLSv1.3`, `rejectUnauthorized`) + поверх **JWT RS256**.
- `SECRET_KEY` — base64-JSON `{caCertPem, jwtPublicKey, nodeCertPem, nodeKeyPem}`.
  **Один ключ на всю панель** (`GET /api/keygen`), не per-node. После `cli reset certs` /
  пересоздания панели ключ меняется → ВСЕ старые ноды отваливаются с alert 40, пока не
  перевоткнёшь актуальный.
- В логах ноды при старте: `Expected SNI: <uuid>.<…>.app` — SNI серта её mTLS-сервера.
  Любой TLS-терминатор между панелью и нодой ломает хендшейк.
- Адрес ноды в панели: **голый `IP:NODE_PORT`** (node-assistant пишет именно так).
  Домен в адресе ноды не нужен; Cloudflare-proxy (оранжевое облако) или nginx перед портом
  → alert 40.

## Диагностика: нода не подключается к панели

| Симптом | Причина | Фикс |
|---|---|---|
| нода `disconnected`, Xray не стартует | панель не достучалась до `NODE_PORT` | firewall ноды: открыть `NODE_PORT` для IP панели; `docker logs remnanode` |
| `write EPROTO … ssl3_read_bytes … ssl/tls alert handshake failure` (alert 40) в логах **панели** | TLS-пир на `NODE_PORT` отверг mTLS: на порту не тот эндпоинт (SSH занял порт / nginx / CF перед адресом) ИЛИ пара сертов в `SECRET_KEY` устарела после пересоздания панели | на ноде `ss -tlnp | grep -E ':(22|2222)\b'` — кто на порту; адрес ноды = голый `IP:порт` без CF; перевоткнуть свежий `SECRET_KEY` + `docker compose up -d --force-recreate` |
| mTLS/JWT ошибки в логах ноды | битый/чужой `SECRET_KEY` | панель CLI → «Get SECRET_KEY for Node» → вставить в compose ноды |
| `Expected SNI: …` виден, но панель не может подключиться | на порту отвечает не mTLS-сервер ноды (nginx/CF/другой сервис) | убрать посредников с `NODE_PORT`, панель должна ходить напрямую на IP ноды |
| рассинхрон времени → handshake fail | часы разъехались | NTP на ноде и панели |
| `write EPROTO … alert 40` в логах панели, нода при этом стартует чисто (Node v3.3.0 banner, Xray работает) | **рассинхрон мажоров панель/нода**: панель 2.x + нода 3.x — нода сменила формат mTLS/сертов канала, панель 2.x не проходит хендшейк | свести мажоры: панель 2.x ↔ нода 2.x (напр. `remnawave/node:2.8.0` при панели 2.7.4); панель 3.x ↔ нода 3.x. После смены версии перевоткнуть `SECRET_KEY`. Проверить: `docker exec remnanode node -v` не поможет — версия ноды = тег образа |

## Деплой через node-assistant (egames/vanilla)

- **egames**: compose `/opt/remnanode/docker-compose.yml` — `remnanode` (host-сеть, `NODE_PORT`+`SECRET_KEY`,
  cap NET_ADMIN) + `remnawave-nginx` (host-сеть, маскировка). **Nginx слушает ТОЛЬКО unix-сокет**
  `/dev/shm/nginx.sock ssl proxy_protocol` — у него нет TCP-порта; публичный 443 держит Xray
  (Reality dest → unix-сокет). Control-API ноды торчит на TCP `NODE_PORT` напрямую.
- **vanilla**: официальный `remnawave/node`, host-сеть, только `NODE_PORT`+`SECRET_KEY`, без nginx/маскировки.
- node-assistant создаёт ноду: `address = IP`, `port = remnanode_port` (2222), ключ из `GET /api/keygen`.
- Детали из кода (шаблоны compose/nginx, автосоздание хостов, reinstall) → `references/node-assistant-deploy.md`.

## Питфоллы

1. **SSH ↔ NODE_PORT конфликт:** дефолты node-assistant `change_ssh_port=true`, `new_ssh_port=2222`
   и `remnanode_port=2222` совпадают. SSH на 2222 = remnanode не стартует (EADDRINUSE) или панель
   стучится в SSH. Всегда проверять `ss -tlnp`.
2. **SECRET_KEY меняется при пересоздании панели** — перевоткнуть во все ноды.
3. **Домен/CF в адресе ноды** — ломает mTLS (alert 40). Только IP:порт (или DNS-only домен).
4. В egames nginx НЕ участвует в канале панель↔нода — не искать проблему в нём.
5. **Мажоры панели и ноды обязаны совпадать** (2.x↔2.x, 3.x↔3.x): реальный кейс — панель 2.7.4 +
   нода 3.3.0 → alert 40 в логах панели, починено понижением ноды до 2.8.0. При апгрейде панели
   на 3.x поднимать и ноды до 3.x. Версия ноды = тег образа (`remnawave/node:2.8.0` / `:3`), не env.

## Диагностические команды

```bash
# нода
ss -tlnp | grep -E ':(22|2222)\b'        # кто слушает NODE_PORT (не SSH ли?)
docker logs remnanode --tail 100 -t      # Expected SNI, mTLS/JWT ошибки
grep SECRET_KEY /opt/remnanode/docker-compose.yml | cut -c1-30   # сравнить с панелью

# панель
docker logs remnawave --tail 200 | grep -i -B3 -A3 'EPROTO\|handshake'
docker exec -it remnawave cli            # → Get SECRET_KEY for Node (актуальный ключ)
```
