# Волна 4 · План C — E5: Prometheus-метрики Remnawave на дэшборде

> **Статус (2026-07-21):** ✅ Ф1 (backend `services/panel_metrics.py` парсер+summarize+scrape, `POST /api/panel/metrics`,
> `test_panel_metrics.py` 7 тестов) + ✅ Ф2 (frontend `PanelManageModal` «Статистика» → блок «Метрики панели»).
> R1/R2 закрыты по докам (нет живой панели). Проверено: unit-тесты + endpoint вживую на VPS-ноде без панели → 404.
> **Полная проверка плиток с живыми данными — при развёрнутой панели (pending).**

> eGames-вики. Лёгкий скрейп метрик панели (порт 3001, basic-auth) и вывод нескольких показателей нагрузки —
> без Grafana/VictoriaMetrics-стека.
> Затрагивает: `services/panel_metrics.py` (новый), `api/panel_deploy.py`/новый роут, `frontend` дэшборд/статистика.

## Контекст (как есть)

- Панель разворачивается node-assistant (`panel_pipeline`, `panel_jobs_<id>` в localStorage; SSH-креды не хранятся).
- Панель отдаёт **Prometheus-метрики на порту 3001**, basic-auth `METRICS_USER`/`METRICS_PASS` (генерятся
  server-side, `METRICS_PASS` ∈ `_PROTECTED_ENV_KEYS`); обычно bind на 127.0.0.1.
- Чтение env панели уже есть: `/api/panel/env/read` (Ф8), маскировка секретов.

## Развилки (закреплены)

- Лёгкий вариант: скрейп метрик и вывод нескольких показателей; без тяжёлого стека.

## Разведка

- **R1.** Точные имена метрик Remnawave на :3001 (кол-во онлайн-юзеров, нагрузка нод, трафик) — снять с реального
  `/metrics` и задокументировать в CLAUDE.md.
- **R2.** Как достучаться до :3001: он на 127.0.0.1 панели → скрейпить **по SSH** (`curl -u user:pass
  127.0.0.1:3001/metrics`, silent) с кредами панели per-request. Подтвердить, что порт не проброшен наружу.

## Стратегия

Ф1 (backend: скрейп+парс) → Ф2 (frontend: вывод).

---

### Ф1 — Backend: скрейп метрик → verify: pytest

`services/panel_metrics.py`:
- `fetch_panel_metrics(ssh_creds)` — по SSH на бокс панели: прочитать `METRICS_USER`/`METRICS_PASS` из
  `/opt/remnawave/.env` (silent), `curl -fsS -u user:pass http://127.0.0.1:3001/metrics` (silent, креды не в
  логах/argv — через stdin/env, как секреты в §6), распарсить Prometheus-text (stdlib-парсер: строки
  `name{labels} value`) в набор нужных gauges (R1).
- `api` роут `POST /api/panel/metrics` (под `require_account`): тело = SSH-креды панели (per-request) → вернуть
  распарсенные показатели. Ошибка/недоступно → 502 с коротким сообщением, без утечки креда.
- verify: `backend/tests/test_panel_metrics.py` — парсер Prometheus-text на фикстуре; отсутствие креда в ответе.

---

### Ф2 — Frontend: вывод показателей → verify: preview

- На `PanelDashboard`/`PanelManageModal` (или в разделе статистики) — блок «Метрики панели»: несколько плиток
  (онлайн-юзеры, нагрузка, трафик — по R1), обновление по кнопке/интервалу; SSH-креды берутся из `panel_jobs`
  (per-request, как остальные panel-операции).
- verify: `tsc`, preview — плитки метрик заполняются с реальной панели.

## Критерии готовности плана C

- Метрики :3001 скрейпятся по SSH с basic-auth (креды не логируются) и выводятся на UI. `pytest` + preview.
- R1/R2 закрыты и записаны в CLAUDE.md.
