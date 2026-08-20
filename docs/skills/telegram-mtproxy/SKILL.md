---
name: telegram-mtproxy
description: Use when deploying MTProxy/telemt for Telegram - docker, TLS, config.
version: 1.0.0
author: RZA
metadata:
  hermes:
    tags: [netops, telegram, mtproxy]
    category: devops
---

# MTProxy / telemt для Telegram

Шпаргалка: `knowledge/base/telegram_proxy.md` (база знаний netops). Репо: telemt, MTProxyMax, tg-ws-proxy-Manager.

## When to use
- Развернуть прокси для Telegram (клиенты в РФ/обход), MTProxy-протокол

## telemt (Rust, рекомендован)
```bash
# docker (4q4r/telemt-docker, multi-arch)
docker run -d --name telemt --restart unless-stopped -p 443:443 -e SECRET=<secret> 4q4r/telemt
# секрет: TELEGRAM_PROXY_SECRET=$(head -c 16 /dev/urandom | xxd -ps)
# клиентская ссылка: tg://proxy?server=IP&port=443&secret=<secret>
```
- Быстрый (Tokio), стабильный, малый расход памяти
- Проверка: `curl -s http://IP:443` должен вернуть HTTP-ответ (или telegram ping)

## MTProxyMax / официальный MTProxy
```bash
# MTProxyMax (shell-установщик)
git clone https://github.com/SamNet-dev/MTProxyMax && cd MTProxyMax && ./install.sh
```
- С TLS-сертификатом, fake-TLS, userbot-режим
- OpenWrt: tg-ws-proxy-Manager (StressOzz) — WS-прокси на роутере

## Pitfalls
- MTProxy требует секрет 32 hex символа, первым символом 0xee/0xdd для fake TLS
- Порт лучше 443 (не блокируется в РФ каналах)
- Один прокси обслуживает ~2-5K юзеров, для большего — несколько/балансировка
- Не открывай без файрвола — MTProxy-сканеры ищут открытые порты
