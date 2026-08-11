# Обходы БС + цветовая маркировка нод — дизайн

Дата: 2026-08-11. Статус: утверждён в Q&A (инструменты-B, цвета-B).

## Группа «Обходы БС» (сайдбар)

Новая группа между «Remnawave» и «HAPROXY»… нет — между «Управление» и
«Статистика»? Нет: после «Remnawave», перед «HAPROXY». Два раздела:
- `obhod-regru` — «REGRU хостинг» (домен привилегий `panel`: смена домена
  ноды через существующий `/api/replace-domain/node` с SSH-кредами);
- `obhod-beeline` — «Beeline CDN` (домен `configs`: правка хостов панели).

## REGRU хостинг (инструмент)

Схема обхода: домен размещён на reg.ru (белый для DPI хостинг/регистратор),
нода отвечает на него. Инструмент = гайд + мастер привязки:
1. Гайд-карточка: заказать домен/хостинг в reg.ru → A-запись на IP ноды →
   вернуться сюда.
2. Форма: выбор ноды (deploy_jobs, креды на месте), старый домен
   (автоподстановка), новый reg.ru-домен → `POST /api/replace-domain/node`
   (существующий, panel.execute) → прогресс задачи через useTaskStream
   (терминал, как у certs).

## Beeline CDN (инструмент)

Схема: CDN-ресурс в ЛК Beeline CDN с origin = домен/IP ноды; клиенты ходят на
CDN-домен. У ноды/хоста SNI и Host должны стать CDN-доменом.
1. Гайд-карточка: создать CDN-ресурс (origin = домен ноды) → получить
   CDN-домен/CNAME → вписать ниже.
2. Backend: `GET /api/obhod/hosts` (список хостов панели для пикера) и
   `POST /api/obhod/beeline/apply {host_uuids, sni, host}` →
   `PATCH /api/hosts` (новый метод клиента `update_host`; поля sni/host
   подтверждены в UpdateHostRequestDto). Маппинг: configs.view/edit.
3. Форма: мультиселект хостов, поле CDN-домена (одно на оба поля), применить.

## Цветовая маркировка нод

- Пресеты (8, читаемые на всех скинах): red/orange/yellow/green/cyan/blue/
  violet/magenta + «сброс».
- DeployCard: кнопка-палитра → попап пресетов. Цвет хранится в записи
  deploy_jobs (`color`, localStorage). Стиль: левая кромка 3px + тинт фона 8%.
- Dashboard (uptime-строки нод): тот же цвет по совпадению domain/IP из
  deploy_jobs — левая кромка строки. Общий helper `utils/nodeColors.ts`
  (пресеты, чтение карты domain/ip → color).

## Тесты

- pytest: update_host пробрасывает PATCH с полями; beeline/apply по
  нескольким хостам; маппинг привилегий (гейт-тест маршрутов).
- vitest: REGRU-форма (автоподстановка, запуск replace), Beeline-форма
  (выбор хостов, body), цветовой пикер (сохранение в localStorage),
  dashboard-кромка по цвету.
