NODEFLOW PANEL
==============

Файл:
  nodeflow-panel-source.tar.gz

Быстрая установка:

  sudo install -d -m 0750 /opt/nodeflow
  sudo tar -xzf nodeflow-panel-source.tar.gz -C /opt/nodeflow
  cd /opt/nodeflow
  sudo ./scripts/install-panel.sh panel.example.com https://panel.example.com 0.0.0.0

После этого обязательно настройте Nginx или Caddy по START-HERE.html.

Порты:
  80/tcp   — сертификат и редирект на HTTPS
  443/tcp  — браузерная Panel
  4200/tcp — прямой mTLS Agent → Panel
  8080/tcp — только 127.0.0.1, наружу не открывать

Готовые reverse proxy конфиги лежат в подпапке reverse-proxy.

ОБНОВЛЕНИЕ PANEL
----------------

Загрузите новый nodeflow-panel-source.tar.gz на сервер Panel любым способом:
WinSCP/SFTP, scp или файловым менеджером хостинга. Домашний компьютер может
работать на Windows, macOS или Linux — сам updater запускается на сервере.

На сервере Panel выполните:

  sudo rm -rf /tmp/nodeflow-update
  sudo install -d -m 0700 /tmp/nodeflow-update
  sudo tar -xzf /tmp/nodeflow-panel-source.tar.gz -C /tmp/nodeflow-update
  cd /tmp/nodeflow-update
  sudo ./scripts/update-panel.sh /opt/nodeflow

Не распаковывайте архив прямо поверх /opt/nodeflow. Скрипт сохраняет .env,
tls/ и pki/, проверяет свободное место, создаёт root-only backup исходников и
валидированный pg_dump, запускает миграции, health-check и проверку React-asset.
При провале он возвращает предыдущие исходники и application image. Дамп БД
остаётся в /var/backups/nodeflow; миграции БД автоматически не откатываются.

Перед обновлением проверьте:

  cd /opt/nodeflow
  sudo docker compose ps
  df -h /opt/nodeflow

После обновления:

  cd /opt/nodeflow
  sudo docker compose ps
  curl -fsS http://127.0.0.1:8080/healthz

Не удаляйте /opt/nodeflow/.env, /opt/nodeflow/tls и /opt/nodeflow/pki.
