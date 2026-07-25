# Changelog

## 1.0.4 — Resilient backend DNS

- Domain backends use explicit local AdGuard, systemd-resolved, Cloudflare, Google and Quad9 fallbacks without depending on `/etc/resolv.conf`.
- HAProxy renderer revision bumped to v12 so newly published route revisions carry the resolver policy.
- Panel and Node Agent release versions aligned at 1.0.4.

## 1.0.2 — Node ordering and service control

- Added persistent drag-and-drop ordering for nodes and per-node routes.
- Added confirmed HAProxy stop/start control over the existing mTLS heartbeat channel.
- Fixed primary and disabled button contrast for custom accent themes.

## 1.0.1 — Initial release hotfix

- Reinstall skips HAProxy package replacement when the installed runtime is already current.
- Reinstall synchronizes the selected signed Agent release with Panel state.
- Agent accepts current short NodeFlow and legacy quota runtime object names.

## 1.0.0 — Initial release

- NodeFlow Panel: управление HAProxy-нодами, маршрутами, трафиком, квотами и UFW.
- Node Agent: исходящий mTLS-канал, безопасное применение конфигурации и signed updates с автооткатом.
- Bootstrap по SSH: root, пользователь с sudo, пароль или приватный SSH-ключ.
- HAProxy TCP/SNI: общие listener-frontend, IP/SNI/Any TCP маршруты, Unix socket и PROXY protocol.
- Наблюдаемость: RX/TX, соединения, TCP-сессии, backend health и журнал действий.
- Релизный комплект с Panel source, Node Agent binary, reverse-proxy примерами и контрольными суммами.
