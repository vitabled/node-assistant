# Волна 4 · План E — E7: визард смены домена (панель + нода)

> **Статус (2026-07-21):** ✅ Ф1-Ф3. Backend `services/replace_domain.py`+`api/replace_domain.py` (POST /node,/panel,
> переиспользуют build_ssl_script; `test_replace_domain.py` 7 зелёных). Frontend `rw/ReplaceDomainModal.tsx` +
> кнопки в `DeployCard` (нода) и `PanelManageModal` (панель). pytest+tsc зелёные; живой smoke — build-ahead.

> eGames-вики (`/configuration/how-to-replace-a-domain/`). Мастер смены домена: новый серт → правка
> `.env`/`docker-compose.yml`/`nginx.conf` → рестарт. Для **панели и ноды**.
> Затрагивает: `services/replace_domain.py` (новый), `api/replace_domain.py` (новый), `frontend/rw/*` (панель) +
> «Управление SSL»/`DeployCard` (нода). Переиспользует `pipeline.build_ssl_script`.

## Контекст (как есть)

- Серты выдаются per-FQDN (`build_ssl_script`, извлечён из `step_ssl` — CLAUDE.md §2 Ф10). Отдельного флоу
  «сменить домен» нет — оператор пересоздаёт вручную.
- Смена домена (eGames-дока): новый серт в `/etc/letsencrypt/live/<domain>/`, правка `FRONT_END_DOMAIN`/
  `SUB_PUBLIC_DOMAIN` в `.env`, путей в `docker-compose.yml`, домена в `nginx.conf`, рестарт; для panel+node —
  обновить адрес ноды.

## Развилки (закреплены)

- Визард для **панели и ноды** (оба сценария).

## Стратегия

Ф1 (backend: смена домена ноды) → Ф2 (backend: смена домена панели) → Ф3 (frontend: визард).

---

### Ф1 — Смена домена ноды → verify: pytest + smoke

`services/replace_domain.py` + `api/replace_domain.py` (`/api/replace-domain`, под `require_account`):
- `POST /node` (`ReplaceDomainNodeRequest`: SSH-креды per-request, old_domain?, new_domain, cert_provider/
  email/cf_token) — стрим-Task:
  1. Выпустить новый серт на `new_domain` (переиспользовать `build_ssl_script` + `ssl_needs_cf_dns`).
  2. Заменить домен в `remnanode` `docker-compose.yml`/`nginx.conf` (пути серта, `server_name`, cert-мост) —
     **awk-replace** (scoped, как cert-мост в §6), идемпотентно.
  3. Обновить домен в Remnawave-хосте (если создавали) через API (опц.).
  4. `docker compose down && up -d`, verify running.
- Валидация `new_domain` — FQDN allowlist (shell-safety, как в моделях).
- verify: `test_replace_domain.py` — генераторы awk-замен (не ломают nginx-вары), FQDN-валидация; ручной smoke.

---

### Ф2 — Смена домена панели → verify: pytest + smoke

- `POST /panel` (`ReplaceDomainPanelRequest`: SSH-креды панели per-request, new_panel_domain и/или
  new_sub_domain) — стрим-Task:
  1. Новый серт (caddy — авто; nginx — `build_ssl_script`).
  2. Правка панельного `.env` (`FRONT_END_DOMAIN`/`SUB_PUBLIC_DOMAIN`), `docker-compose.yml`, reverse-proxy
     конфига (Caddyfile/nginx) — через тихий канал записи (переиспользовать `/api/panel/env/write`-механику).
  3. Рестарт панели (+ sub-page, если задет sub_domain).
- verify: `test_replace_domain.py` — правки env/compose/caddy; ручной smoke.

---

### Ф3 — Frontend: визард → verify: tsc + preview

- **Нода**: кнопка «Сменить домен» в `DeployCard`/«Управление SSL» → модалка-визард (новый домен + провайдер) →
  `POST /api/replace-domain/node`, стрим в `OpStreamModal`.
- **Панель**: кнопка «Сменить домен» в `PanelManageModal` → визард → `POST /api/replace-domain/panel`, стрим.
- Двойной confirm (домен — необратимая правка прод-конфига); показать, что именно поменяется.
- verify: `tsc`, preview обоих визардов.

## Критерии готовности плана E

- Визард меняет домен ноды и панели: новый серт + правки env/compose/nginx + рестарт, идемпотентно, с confirm.
- `pytest` (test_replace_domain) + preview + ручной smoke на тест-боксе.
