# Axottle-панель: как развёрнута (источник дизайна)

Дизайн снят **с живой панели Axottle v1.8.3**, а не с маркетингового сайта. Ниже — как она
поднимается с нуля (воспроизводимо), что в ней доступно без лицензии и где стена.

## 1. Что раздаёт вендор

Релиз `Axottle/axottle-releases` → `v1.8.3` (публикация 2026-09-22). Ассеты:

| Ассет | Размер | Что это |
|---|---|---|
| `axottle-release-v1.8.3-amd64.tar.gz` | 229 МБ | релиз-артефакт (бинарь + 168 SQL-миграций + установщик) |
| `…tar.gz.sha256` + `.sig` | — | контрольная сумма и подпись |
| `axottle-installer.sh` | 234 КБ | интерактивный установщик (systemd + PostgreSQL + reverse proxy) |

Внутри архива: `release/bin/axottle` — **один бинарник на 537 МБ**, фронт вшит внутрь.
`release/manifest.txt` прямо говорит:

```
backend_obfuscated=true
frontend_obfuscation_attempted=true
frontend_obfuscated=true
javascript_obfuscator_version=5.5.1
```

Docker-реестров нет — панель ставится бинарём и юнитом `axottle.service` в `/opt/axottle`
(порт по умолчанию `8080`, env-файл с `AXOTTLE_DB_URL` / `AXOTTLE_SECRET_KEY`).

## 2. Что сделано здесь (локальный стенд)

Полный установщик не нужен: у CLI есть команда `serve`, а БД закрывается контейнером.

```bash
# 1. Скачать и проверить релиз
cd /root/work/axottle_release
curl -sLO https://github.com/Axottle/axottle-releases/releases/download/v1.8.3/axottle-release-v1.8.3-amd64.tar.gz
sha256sum -c <(curl -sL …/axottle-release-v1.8.3-amd64.tar.gz.sha256)   # OK, сумма совпала
tar -xzf axottle-release-v1.8.3-amd64.tar.gz release/bin release/migrations

# 2. PostgreSQL под панель (хост-порт 5432 был свободен)
docker run -d --name axottle-pg -e POSTGRES_USER=axottle -e POSTGRES_PASSWORD=<pw> \
  -e POSTGRES_DB=axottle -p 127.0.0.1:5432:5432 postgres:17-alpine

# 3. Запуск панели (env — см. env.sh; 168 миграций применяются на старте)
AXOTTLE_HOST=127.0.0.1 AXOTTLE_PORT=8123 \
AXOTTLE_DATA_DIR=/root/work/axottle_live/data \
AXOTTLE_MIGRATIONS_DIR=…/release/migrations \
AXOTTLE_DB_URL="postgres://axottle:<pw>@127.0.0.1:5432/axottle?sslmode=disable" \
AXOTTLE_SECRET_KEY=<hex32> ./axottle serve
```

Итог запуска: `{"msg":"axottle ready","addr":"127.0.0.1:8123"}`, 168 миграций, поисковый
индекс 868 записей. **Живая панель: http://127.0.0.1:8123** (креды — `panel_admin.txt`).

⚠️ Порт по умолчанию `8099` брать нельзя: на этом хосте он занят чужим python-сервисом
(`aiohttp`), а `axottle` падает с `bind: address already in use` **после** применения миграций —
то есть БД остаётся мигрированной, а панель не поднимается. Диагностика: `ss -lntp | grep <port>`.

## 3. Где стена: лицензия

Мастер первичной настройки — 3 шага: **Администратор → Лицензия → Готово**.

1. Шаг «Администратор» проходится (аккаунт создан, `/api/v1/setup/status` → `admin_created:true`).
2. Шаг «Лицензия» требует **платный лицензионный ключ**: *«Введите действующий лицензионный
   ключ, чтобы завершить настройку панели. Активация обязательна.»* Без ключа
   `setup_complete` не выставляется, и вход в панель закрыт.

Дизайн от этого не страдает: **фронт панели отдаётся целиком и без авторизации** — CSS и JS
всех маршрутов лежат в `/_next/static/**` и скачиваются анонимно. Поэтому:

* **токены, компонентные классы, шрифты, шкалы — из настоящего CSS панели** (255 КБ, 2 чанка);
* **рецепты компонентов** — из живого DOM визарда + строковых таблиц JS-чанков;
* **скриншоты** — реальный рендер визарда во всех сочетаниях осей (режим × палитра × стиль).

Не хватает только внутренних экранов (дашборд/ноды/таблицы) — они за лицензией. Их разметка
в ассетах есть, но отрендерить без ключа нельзя; это честно помечено как пробел.

## 4. Что снято с панели

| Артефакт | Файл | Размер |
|---|---|---|
| Токены, палитры, стили, классы, шкалы | `design/tokens.json` | 60 КБ |
| Сырые CSS-чанки панели (1:1) | `design/panel1.css`, `panel2.css` | 214 + 41 КБ |
| Скрипт извлечения (воспроизводимо) | `extract_design.py` | — |
| Скриншоты живого рендера | `design/screenshots/*.png` | 5 шт. |
| Рецепты классов из JS-чанков | `design/panel_recipes.txt` | — |

## 5. Повторный прогон

```bash
# панель должна быть живой на 127.0.0.1:8123
python3 /root/work/axottle_live/extract_design.py http://127.0.0.1:8123 /root/work/axottle_live/panel_raw
```

Скрипт сам находит чанки (в т.ч. второй, который грузится лениво), разбирает токены по
селекторам, отделяет палитры друг от друга и перезаписывает `tokens.json`.

## 6. Снос стенда

```bash
docker rm -f axottle-pg
rm -rf /root/work/axottle_live/data /root/work/axottle_release/unpacked/release/bin
```
Бинарь занимает 537 МБ — при нехватке места удалять первым.
