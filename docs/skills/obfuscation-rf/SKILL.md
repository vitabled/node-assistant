---
name: obfuscation-rf
description: Use when bypassing RU blocking - dnstt, olcrtc, WDTT, whitelists, CDN.
version: 1.0.0
author: RZA
metadata:
  hermes:
    tags: [netops, obfuscation, rf, vpn]
    category: devops
---

# Обход блокировок РФ (белые списки)

Полная шпаргалка: `knowledge/base/obfuscation.md` (база знаний netops). Протоколы в деталях: xray.md (XHTTP/Reality), singbox.md.

## When to use
- Настроить обход для клиента/сервера под РФ (белые списки, DPI)
- Выбор протокола под задачу: скрытность vs скорость

## Карта инструментов (из репо и чатов)
| Инструмент | Тип | Когда |
|---|---|---|
| **Xray XHTTP/Splithttp** | HTTP-транспорт через CDN (Cloudflare) | Лучший баланс скрытность/скорость, обходит белые списки |
| **XTLS Reality/Vision** | TLS-имитация | Сервер без домена, против DPI |
| **dnstt** | DNS-туннель (DoH/DoT) | Только DNS открыт — максимум скрытность, медленно |
| **olcrtc** (bb22) | Туннель через WebRTC-медиасервисы (Google Meet) | Официальные сервисы не блокируют |
| **WDTT-Plus** | WireGuard через DTLS-медиарелеи VK TURN | Скрытный WG |
| **shadowtls/hysteria2** | Обфускация TLS/QUIC | Запасной слой |
| **whitelist-bypass / Turnel (SRTP)** | WebRTC/прочие туннели | Нишевые |

## Общие шаги
1. Проверь, что блокируется: `curl -I https://target`, LowderPlay/cheburcheck
2. Домен-прикрытие: подбери чистый домен (sni-pinger, RealiTLScanner)
3. Разверни серверный inbound (xray XHTTP через nginx+CF или reality)
4. На клиенте: подписка/конфиг, тест на РФ IP
5. Свежие геоданные блокировок: runetfreedom/russia-blocked-geosite + geoip

## Pitfalls
- Белые списки РФ блокируют по SNI/IP — всегда нужен маскировочный TLS
- CF flexible vs full — для XHTTP через Cloudflare нужен правильный режим
- dnstt/olcrtc — низкая скорость, только для крайних случаев
- Проверяй после каждого изменения на реальном РФ-канале
