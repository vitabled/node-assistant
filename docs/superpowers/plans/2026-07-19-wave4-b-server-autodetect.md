# Волна 4 · План B — Автодетект настроек существующего сервера

> Новая фича. При добавлении существующего сервера — детектить не только КОМПОНЕНТЫ (уже есть), но и текущие
> ЗНАЧЕНИЯ настроек, совпадающие с полями формы установки, и предзаполнять форму.
> Затрагивает: `api/node_ops.py` (`/api/node/detect`, `NodeDetectRequest`, `_DETECT_SCRIPTS`, `_parse_detect`),
> `frontend/DeployDashboard.tsx` («Существующий сервер» модалка), `DeployForm.tsx` (prefill).

## Контекст (как есть)

- «Существующий сервер» (Ф5 wave1): `POST /api/node/detect` (`NodeDetectRequest`, creds-per-request) — одна SSH
  сессия гоняет read-only пробы `_DETECT_SCRIPTS` per `Component`, `_parse_detect` → `{results:{component:
  present|absent|unknown}}`. Фронт (`DeployDashboard.tsx`) показывает чеклист (present → pre-checked как skip),
  затем деплой с `DeployRequest.skip_components`.
- Форма деплоя (`DeployForm.tsx`) для нового сервера **не передаёт `initial`** (иначе settings-overlay
  пропускается) — prefill сейчас только из настроек-дефолтов.

## Развилки (закреплены)

- Расширить detect: читать текущие ЗНАЧЕНИЯ (SSH-порт, remnanode_port/xhttp_path/token, открытые порты UFW,
  домен/серт, наличие WARP/оптимизаций) → фронт **предзаполняет форму** + помечает совпавшие компоненты «skip».

## Стратегия

Ф1 (backend: detect значений) → Ф2 (frontend: предзаполнение формы).

---

### Ф1 — Backend: детект значений → verify: pytest test_node_detect

`api/node_ops.py`:
- Добавить в detect **read-only пробы значений** (в той же SSH-сессии, silent — `ssh.get_output`, без Task):
  - **SSH-порт**: активный `Port` из `sshd -T | grep -i '^port'` (или `ss -tlnp` на sshd).
  - **remnanode**: из `/opt/remnanode/docker-compose.yml` / `.env` — `NODE_PORT`/`remnanode_port`, `SECRET_KEY`
    (токен — **не логировать**, отдать флаг `has_token`, не сам токен), XHTTP-путь из nginx-конфига (`location
    $path`), домен из серт-путей `/etc/ssl/certs/*_fullchain.pem`.
  - **UFW**: открытые порты (`ufw status` парсинг).
  - **Серт/домен**: `openssl x509 -enddate` для найденного домена (переиспользовать `_cert_expiry`).
  - **WARP/оптимизации**: наличие (`wg show warp`, sysctl-маркеры) — уже частично в `_DETECT_SCRIPTS`.
- Ответ detect расширить: `{results:{component:…}, settings:{ssh_port, remnanode_port, xhttp_path, has_token,
  domain, open_ports, install_warp, optimize, …}}`. Значения best-effort; не распознал → поле отсутствует/`null`.
- **Секреты не утекают**: сам `SECRET_KEY`/токен НЕ возвращаем (только `has_token`); приватные данные — нет.
- verify: `backend/tests/test_node_detect.py` — `_parse_detect` значений (порт/xhttp/домен/порты); отсутствие
  токена в ответе; connect-fail → 502.

---

### Ф2 — Frontend: предзаполнение формы → verify: tsc + preview

`DeployDashboard.tsx` («Существующий сервер»):
- После detect — маппить `settings` в поля формы деплоя и открыть форму **с предзаполнением** (передать эти
  значения как `preset`, НЕ как `initial`, чтобы settings-overlay для незаданных полей всё равно отработал —
  либо аккуратно смёржить: detected > preset-defaults). Совпавшие компоненты — pre-checked skip (как сейчас).
- Поля, которые detect распознал, визуально пометить «обнаружено на сервере» (чтобы оператор видел, что
  подставилось). `has_token=true` → не требовать ввод токена (compose уже есть) / показать «токен уже на сервере».
- Крайние случаи: конфликт detected vs дефолт настроек — приоритет detected; оператор может переопределить.
- verify: `tsc`, preview — детект существующего сервера подставляет SSH-порт/remnanode_port/xhttp/домен/порты,
  помечает компоненты skip, деплой стартует с корректными значениями.

## Критерии готовности плана B

- Detect возвращает и компоненты, и значения настроек; секреты (токен) не утекают.
- Форма добавления существующего сервера предзаполняется обнаруженными значениями + skip совпавших компонентов.
- `pytest` (test_node_detect) + `tsc` + preview.
