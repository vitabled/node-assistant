---
name: singbox-configs
description: Use when configuring sing-box - inbound/outbound, TUN, routing, RF.
version: 1.0.0
author: RZA
metadata:
  hermes:
    tags: [netops, singbox, vpn, proxy]
    category: devops
---

# sing-box: конфигурация

Полная шпаргалка: `knowledge/base/singbox.md` (база знаний netops). Детали протоколов в `knowledge/base/obfuscation.md`.

## When to use
- Настроить sing-box на сервере/клиенте (VLESS, Hysteria2, TUN, роутинг)
- Разобрать чужой конфиг, диагностика `journalctl -u sing-box`

## Быстрый старт (сервер, VLESS)
```json
{
  "log": {"level": "info"},
  "inbounds": [{"type": "vless", "tag": "vl-in", "listen": "0.0.0.0", "listen_port": 443,
    "users": [{"uuid": "UUID"}], "tls": {"enabled": true, "server_name": "example.com",
    "certificate_path": "/etc/sing-box/cert.pem", "key_path": "/etc/sing-box/key.pem"}}],
  "outbounds": [{"type": "direct", "tag": "direct"}]
}
```
Проверка: `sing-box check -c config.json` (или `-D /etc/sing-box`).

## TUN + DNS + route (клиент)
```json
{
  "inbounds": [{"type": "tun", "tag": "tun-in", "address": ["10.0.0.1/30"], "auto_route": true, "strict_route": true}],
  "dns": {"servers": [{"tag": "local", "address": "223.5.5.5"}, {"tag": "remote", "address": "https://1.1.1.1/dns-query"}]},
  "route": {"rules": [{"rule_set": ["geosite-category-ru"]}]},
  "rule_sets": [{"type": "remote", "tag": "geosite-category-ru", "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ru.srs"}]
}
```
- rule-set `.srs` (двоичный) vs `.json` — используй .srs для remote.
- РФ-категории: `geosite-category-ru`, `geoip-ru` (SagerNet/sing-geoip).

## Pitfalls
- `sing-box check` обязателен перед рестартом — битый конфиг роняет сервис
- TUN требует `auto_route: true` + root; на Android (SFA) свои шаблоны
- Порты/протоколы сверяй с базой: shadowtls/hysteria2/tuic/naive/anytls/wireguard — раздел протоколов в singbox.md
