---
name: ddos-protection
description: Use when hardening a server - DDoS, fail2ban, kernel, cloudflare.
version: 1.0.0
author: RZA
metadata:
  hermes:
    tags: [netops, security, ddos]
    category: devops
---

# Защита сервера от атак (DDoS, брутфорс)

Шпаргалка: `knowledge/base/protection.md` (база знаний netops). Инструменты: traffic-guard, nDPI, ban-vpn (репо).

## When to use
- Сервер под атакой / подготовка нового сервера / панель открыта наружу

## Базовый хардненинг (всегда)
```bash
# firewall: только нужное
ufw default deny incoming; ufw allow 22,80,443/tcp; ufw enable
# fail2ban: ssh + панели
apt install fail2ban; systemctl enable --now fail2ban
# ssh: запрет root-пароля
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
```

## Ядро / сеть (для DDoS)
```bash
# sysctl: защита от SYN-flood и spoofing
cat >> /etc/sysctl.d/90-anti-ddos.conf <<'EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.tcp_max_syn_backlog = 4096
net.ipv4.tcp_syn_retries = 2
net.core.somaxconn = 65535
EOF
sysctl --system
```

## Облако/сеть
- **Cloudflare прокси** для веб (как multiroller) — фильтрует L7, скрывает origin
- **traffic-guard** (dotX12) — умный блокировщик трафика, правила
- **nDPI** — глубокий анализ пакетов, детект аномалий
- Для L3/L4 — upstream-фильтрация у хостера (Selectel/Timeweb антиддос) + keepalived/anycast при необходимости

## Pitfalls
- fail2ban на панели за nginx — jail должен смотреть на nginx-логи, не ssh только
- rp_filter=1 может ломать VPN-трафик (wg/xray) — проверь после включения
- Не открывай панели (Remnawave/3x-ui) без пароля/файрвола — скан-боты ломают за минуты
- tcp_syncookies не лечит большие объёмы — только фильтр выше
