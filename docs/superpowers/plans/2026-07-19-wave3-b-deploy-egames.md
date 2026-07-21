# Волна 3 · План B — Деплой: Hysteria2-тумблер, вкладки eGames/Vanilla, заимствования eGames

> Пункты: 2a (тумблер Hysteria2), 2b (eGames/Vanilla), 2c/E1 (Docker-mirror), E3 (Torrent Blocker), E4 (cookie-gate).
> Затрагивает: `models/deploy.py`, `services/pipeline.py`, `api/node_ops.py`, `frontend/DeployForm.tsx`,
> `StepProgress.tsx`.

## Контекст (как есть)

- `DeployRequest.mode: Literal["remnanode","haproxy"]` ([models/deploy.py:9](../../backend/app/models/deploy.py)).
  В remnanode-режиме домен/email/cloudflare **обязательны** (`validate_by_mode`). Тумблеры: `install_warp`
  (дефолт False), `install_trafficguard`, `install_test_tools`, `optimize`. **`install_hysteria2` НЕТ** — шаг 14
  (`step_certbot_ssl`, лейбл «Hysteria2») в remnanode-режиме идёт всегда (если не в `skip_components`).
- Pipeline 14 шагов; mode-ветка после шага 9: haproxy → слот 10 + skip 11–14; иначе remnanode → 10–14.
  Шаг 12 `step_sni_masking` (маскировка), 13 `step_warp` (гейт `install_warp`), 14 `step_certbot_ssl`.
- `DeployForm.tsx`: горизонтальные вкладки режимов **Remnanode / HAProxy** ([DeployForm.tsx:476](../../frontend/src/components/DeployForm.tsx)),
  секция «Remnanode» ([:511](../../frontend/src/components/DeployForm.tsx)).
- `api/node_ops.py`: `Component = Literal["node_accelerator","trafficguard","test_tools","remnanode","masking",
  "warp","hysteria2","ssl","haproxy"]` + `_DETECT_SCRIPTS`/`_UNINSTALL_SCRIPTS`/reinstall.
- Официальная Vanilla-нода (docs.rw/install/remnawave-node): `remnawave/node:latest`, `network_mode: host`,
  `SECRET_KEY`, `NODE_PORT`, серты в `/var/lib/remnawave/configs/xray/ssl/`, **домен/маскировка НЕ требуются**
  (конфиг+TLS ноде отдаёт панель по SECRET_KEY-каналу). Firewall: `NODE_PORT` открыт только для IP панели.

## Развилки (закреплены)

- 2a: `install_hysteria2` (дефолт **True** — сохранить текущее поведение), гейтит шаг 14.
- 2b: под-вкладки Remnanode → `node_variant: Literal["egames","vanilla"]` (дефолт `egames`). Vanilla — офиц.
  установка, **жёстко без домена и маскировки** (skip 10/12); WARP/Hysteria2 остаются тумблерами; нода
  регистрируется в Remnawave как обычно.
- E1 `docker_mirror` (дефолт False) — для всех деплоев (нода+панель). E3 `install_torrent_blocker` (дефолт
  False) — для всех, новый управляемый компонент. E4 `cookie_gate` (дефолт False) — только eGames-нода.

## Стратегия

Ф1 (модель + форма) → Ф2 (pipeline: гейты, Vanilla-ветка, E1/E3/E4) → Ф3 (frontend вкладки+тумблеры) → Ф4
(node_ops: torrent_blocker как компонент).

---

### Ф1 — Модель + валидация → verify: pytest test_deploy

`models/deploy.py::DeployRequest`:
- `node_variant: Literal["egames","vanilla"] = "egames"` (значим только в remnanode-режиме).
- `install_hysteria2: bool = True`.
- `docker_mirror: bool = False`; `install_torrent_blocker: bool = False`; `cookie_gate: bool = False`.
- `validate_by_mode`: при `mode=="remnanode"` **и `node_variant=="vanilla"`** — домен/email/cloudflare
  **НЕ обязательны** (нет локального SSL/маскировки); `create_in_remnawave`/`remnanode_token` логика без
  изменений. При `node_variant=="egames"` — как сейчас (домен/email обязательны).
- **Крайний случай:** `install_hysteria2` в vanilla требует домен (certbot `-d $domain`). Решение: если
  `vanilla` + `install_hysteria2` + пустой домен → 422 «для Hysteria2 в режиме Vanilla укажите домен», ИЛИ
  фронт дизейблит Hysteria2-тумблер в Vanilla без домена (см. Ф3). Зафиксировать в тесте.
- `cookie_gate` разрешён только для eGames (в vanilla игнорируется — нет своего nginx).
- verify: `backend/tests/test_deploy.py` — vanilla без домена проходит; vanilla+hysteria2 без домена → 422;
  egames без домена → 422 (как сейчас).

---

### Ф2 — Pipeline → verify: test_pipeline_scripts + smoke индексов 1..14

`services/pipeline.py`:
1. **Гейт Hysteria2** (шаг 14): в remnanode-ветке оборачивать `step_certbot_ssl` в `if req.install_hysteria2
   and "hysteria2" not in skip → run; else _skip_component(task, 14, "hysteria2")`. Индексы 1..14 begin ровно
   один раз в обоих режимах — пере-проверить (grep `_begin_step`/`_skip_component`).
2. **Vanilla-ветка** (внутри remnanode, `req.node_variant=="vanilla"`):
   - Шаг 10 (SSL) → `_skip_component(task, 10, "SSL")` (нет домена).
   - Шаг 11 (remnanode): вместо текущего `step_remnanode` (nginx+remnanode+локальный серт-мост) — **офиц.
     установка**: `/opt/remnanode/docker-compose.yml` с `remnawave/node:latest`, `network_mode: host`,
     `SECRET_KEY` (из формы или `GET /api/keygen`), `NODE_PORT=remnanode_port`; `docker compose up -d`; проверка
     `remnanode` running. Firewall: `NODE_PORT` только для IP панели (backend IP уже известен из шага 1). Вынести
     в `step_remnanode_vanilla` или ветку внутри `step_remnanode` по `req.node_variant`.
   - Шаг 12 (masking) → `_skip_component(task, 12, "masking")`.
   - Шаги 13/14 — как обычно, по тумблерам.
   - eGames-ветка (`node_variant=="egames"`) — текущее поведение без изменений.
3. **E1 Docker-mirror** (`docker_mirror`): в шаге 2 (и в panel_pipeline перед `docker compose up`) —
   идемпотентно записать `/etc/docker/daemon.json` с `registry-mirrors` (`mirror.gcr.io`,
   `dockerhub.timeweb.cloud`) + включить IPv6-фикс (по eGames-доке), `systemctl restart docker`. Только если
   `docker_mirror`. Скрипт-генератор `_docker_mirror_script()` + юнит в test_pipeline_scripts.
4. **E3 Torrent Blocker** (`install_torrent_blocker`): новый шаг/под-шаг (в обоих режимах, non-fatal try/except)
   — установка `Xray Torrent Blocker` (iptables/routing-правило блокировки BitTorrent). **РАЗВЕДКА:** уточнить
   источник (репозиторий из eGames `resources`) и способ установки; если это routing-правило Xray — оно на
   стороне ноды. Реализовать как **отдельный компонент** (Ф4), вызываемый из пайплайна. Разместить БЕЗ сдвига
   индексов 1..14: либо внутри группы «Оптимизация ОС» как под-шаг существующего слота, либо non-step действие
   после remnanode (уточнить — не ломать 14-индексную карту `STEP_LABELS`/`DEPLOY_STEPS`/`STEP_GROUPS`).
5. **E4 cookie-gate** (`cookie_gate`, только eGames): в nginx-конфиг ноды (шаг 11 eGames) добавить
   `map $http_cookie $auth_cookie` — отдавать 200 только с правильной cookie, иначе прятать хост. Сгенерить
   случайную cookie-пару, вернуть её оператору в лог (как login-URL suffix). Скрипт-генератор + юнит.
   - verify: `backend/tests/test_pipeline_scripts.py` — новые генераторы (`_docker_mirror_script`,
     torrent-blocker, cookie-gate) собираются на глобальном pydantic; smoke индексов.

---

### Ф3 — Frontend: под-вкладки + тумблеры → verify: tsc + preview

`DeployForm.tsx`:
- Внутри секции «Remnanode» (видна только в remnanode-режиме) — **горизонтальные под-вкладки eGames / Vanilla**
  (`.seg`, как вкладки режимов), пишут `form.node_variant`.
- **Vanilla**: скрыть/выключить секцию «Домен и SSL» и поля домена/email/cert_provider/cloudflare (не обязательны);
  оставить тумблеры WARP и Hysteria2 (Hysteria2 дизейблить, если домен пуст — с подсказкой). Cookie-gate **не**
  показывать. eGames: всё как сейчас + чекбокс «Cookie-gate (скрыть хост)» (E4).
- В секции «Оптимизация ОС» (обе вкладки/режимы) добавить чекбоксы **«Docker registry-mirror (РФ)»** (E1) и
  **«Xray Torrent Blocker»** (E3).
- `validateForm` (экспортируется, есть `DeployForm.test.tsx`): для vanilla снять требование домена/email;
  vanilla+hysteria2 без домена → ошибка на домене. `FORM_DEFAULT` + `FormData` дополнить новыми полями.
- `StepProgress.tsx`: если torrent-blocker/cookie-gate/docker-mirror реализованы как под-шаги — отразить в
  группах; если non-step — не трогать 14-карту.
- verify: `DeployForm.test.tsx` (валидация vanilla/egames), `tsc --noEmit`, preview обеих под-вкладок.

---

### Ф4 — node_ops: Torrent Blocker как управляемый компонент → verify: test_node_detect/ops

`api/node_ops.py`:
- Добавить `Component` `"torrent_blocker"` + метку в `_LABELS`; `_DETECT_SCRIPTS["torrent_blocker"]` (проба
  наличия правила/сервиса), `_reinstall`-ветка (переиспользует пайплайн-скрипт установки), `_UNINSTALL_SCRIPTS
  ["torrent_blocker"]` (снять правила/сервис, идемпотентно `-C || -A` при переустановке).
- `manageableComponents` (frontend `DeployCard`) — добавить Torrent Blocker в список (reinstall/uninstall) при
  `savedForm.install_torrent_blocker`.
- `skip_components` / `/api/node/detect` — новый компонент участвует в detect-чеклисте «существующего сервера».
- verify: `backend/tests/test_node_detect.py` + ops-тесты — detect/uninstall для torrent_blocker.

## Критерии готовности плана B

- Тумблер «Установить Hysteria2» реально гейтит шаг 14; выключен → шаг помечается skip.
- Vanilla ставит ноду по офиц. доке без домена/маскировки, регистрирует в Remnawave; WARP/Hysteria2 доступны.
- Docker-mirror/Torrent Blocker/cookie-gate работают по своим тумблерам; индексы шагов 1..14 не разъехались.
- `pytest` (test_deploy/test_pipeline_scripts/test_node_detect), `tsc`, preview.
