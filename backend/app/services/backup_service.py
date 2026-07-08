"""Ф9 (wave1) — Remnawave backup / restore (distillium wrapper).

Pure SSH-script generators (no SSH, no network — unit-testable). They install and
drive a NON-INTERACTIVE backup/restore wrapper on the PANEL server, modelled on
distillium's `backup-restore.sh`
(https://github.com/distillium — interactive bash in /opt/rw-backup-restore):

  * PostgreSQL dump (`pg_dumpall -c`) + a tar of the whole `/opt/remnawave` →
    a bundle `remnawave_backup_<TS>.tar.gz`;
  * upload to Telegram / S3 / Google Drive (or keep local);
  * host **cron** schedule (auto-send);
  * a DESTRUCTIVE restore that clears the `remnawave-db-data` volume → gated
    behind an explicit confirm flag.

Security posture (mirrors panel_pipeline):
  * upload secrets (BOT_TOKEN / CHAT_ID / S3_* / GD_*) live ONLY in
    `/opt/rw-backup-restore/config.env` on the TARGET server (chmod 600) — never
    in our DB, never logged. `config_env_script` writes them through a SILENT,
    single-quoted heredoc; the caller pipes it via `SSHSession.get_script_output`
    so the values never reach a Task log.
  * every value is shell-safety-checked (`_shell_safe`) AND single-quoted in the
    file, so a hostile secret can't break out of the heredoc or inject a command
    when `config.env` is later `source`d by the wrapper.
  * NOTE: distillium (and this wrapper) does NOT back up TLS certificates.
"""

from __future__ import annotations

import re

# ── fixed paths (the wrapper is non-configurable — one install per server) ──
_BR_DIR = "/opt/rw-backup-restore"
_BR_SCRIPT = f"{_BR_DIR}/backup-restore.sh"
_BR_CONFIG = f"{_BR_DIR}/config.env"
_BR_BACKUPS = f"{_BR_DIR}/backups"
# Comment tag that marks OUR crontab line so setup can overwrite (not duplicate)
# its own entry and status can detect it.
_CRON_MARKER = "# node-assistant rw-backup"

UPLOAD_METHODS = ("telegram", "s3", "google_drive", "local")

# Characters that make a value unsafe inside a single-quoted shell context /
# a `source`d config.env: the single quote itself (breaks out), command
# substitution / expansion (`$` backtick), a backslash escape, and newlines
# (which could inject a new KEY= line or the heredoc terminator).
_UNSAFE_CHARS = frozenset("'`$\\\n\r")
# Cron schedule fields: digits, the field separators and wildcards only.
_CRON_RE = re.compile(r"^[0-9*,/ \t-]+$")


def _shell_safe(value: str) -> bool:
    return not any(c in _UNSAFE_CHARS for c in value)


# ── the wrapper script (installed once; driven by subcommands) ─────────────
# Plain string (NOT an f-string): every `$`, `${...}` and `$(...)` must survive
# verbatim into the installed file. Written through a single-quoted heredoc so
# nothing expands at install time either.
_WRAPPER = r"""#!/usr/bin/env bash
# node-assistant Remnawave backup/restore wrapper (distillium-style).
# Design mirrors distillium's backup-restore.sh: pg_dumpall -c + tar of
# /opt/remnawave -> bundle, upload to Telegram/S3/Google Drive, host-cron
# schedule, and a DESTRUCTIVE restore that clears the remnawave-db-data volume.
# Non-interactive: config comes from config.env (chmod 600, upload secrets),
# actions are subcommands: backup | restore --confirm [bundle] | status.
# NOTE: TLS certificates are NOT included in the backup bundle.
set -uo pipefail

BR_DIR="/opt/rw-backup-restore"
BACKUP_DIR="$BR_DIR/backups"
REMNA_DIR="/opt/remnawave"
DB_CONTAINER="remnawave-db"
DB_VOLUME="remnawave-db-data"

[ -f "$BR_DIR/config.env" ] && . "$BR_DIR/config.env"
UPLOAD_METHOD="${UPLOAD_METHOD:-local}"
RETAIN_BACKUPS_DAYS="${RETAIN_BACKUPS_DAYS:-7}"
DB_CONNECTION_TYPE="${DB_CONNECTION_TYPE:-docker}"

log() { echo "[rw-backup] $*"; }

_compose() {
    (cd "$REMNA_DIR" && (docker compose "$@" 2>/dev/null || docker-compose "$@" 2>/dev/null))
}

dump_db() {
    local out="$1"
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${DB_CONTAINER}$"; then
        docker exec "$DB_CONTAINER" pg_dumpall -c -U "${POSTGRES_USER:-postgres}" > "$out" \
            && log "дамп БД готов" || log "[warn] pg_dumpall завершился с ошибкой"
    else
        log "[warn] контейнер $DB_CONTAINER не найден — дамп БД пропущен"
        : > "$out"
    fi
}

upload() {
    local f="$1"
    case "$UPLOAD_METHOD" in
        telegram)
            curl -sf --max-time 120 -F chat_id="${CHAT_ID:-}" -F document=@"$f" \
                "https://api.telegram.org/bot${BOT_TOKEN:-}/sendDocument" >/dev/null \
                && log "загружено в Telegram" || log "[warn] загрузка в Telegram не удалась"
            ;;
        s3)
            if command -v aws >/dev/null 2>&1; then
                AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-}" AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-}" \
                AWS_DEFAULT_REGION="${S3_REGION:-us-east-1}" \
                    aws ${S3_ENDPOINT:+--endpoint-url "$S3_ENDPOINT"} \
                        s3 cp "$f" "s3://${S3_BUCKET:-}/$(basename "$f")" \
                    && log "загружено в S3" || log "[warn] загрузка в S3 не удалась"
            else
                log "[warn] awscli не установлен — загрузка в S3 пропущена"
            fi
            ;;
        google_drive)
            if command -v rclone >/dev/null 2>&1; then
                # Define an on-the-fly `drive:` remote entirely from env-vars
                # (RCLONE_CONFIG_DRIVE_*) so no rclone.conf is needed. GD_TOKEN is
                # the JSON rclone token — double-quoted so it expands (its value is
                # shell-safety-validated, so no injection risk).
                RCLONE_CONFIG_DRIVE_TYPE=drive RCLONE_CONFIG_DRIVE_TOKEN="$GD_TOKEN" \
                    rclone copy "$f" "drive:${GD_FOLDER_ID}" 2>/dev/null \
                    && log "загружено в Google Drive" || log "[warn] загрузка в Google Drive не удалась"
            else
                log "[warn] rclone не установлен — загрузка в Google Drive пропущена"
            fi
            ;;
        local|*) log "локальное хранение (без аплоада)" ;;
    esac
}

cmd_backup() {
    # 0077 → the DB dump / bundle are created 0600 (a full DB dump must not be
    # world-readable to a local user on the box); the backups dir stays 0700.
    umask 077
    mkdir -p "$BACKUP_DIR"
    local ts work bundle
    ts="$(date +%Y%m%d_%H%M%S)"
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    dump_db "$work/db.sql"
    tar czf "$work/remnawave.tar.gz" -C / opt/remnawave 2>/dev/null || log "[warn] tar /opt/remnawave частичный"
    bundle="$BACKUP_DIR/remnawave_backup_$ts.tar.gz"
    tar czf "$bundle" -C "$work" db.sql remnawave.tar.gz || { log "[err] не удалось создать бандл"; exit 1; }
    log "бэкап создан: $bundle"
    upload "$bundle"
    find "$BACKUP_DIR" -name 'remnawave_backup_*.tar.gz' -mtime +"$RETAIN_BACKUPS_DAYS" -delete 2>/dev/null || true
    log "готово."
}

cmd_restore() {
    if [ "${1:-}" != "--confirm" ]; then
        echo "restore требует подтверждения (--confirm)"
        exit 1
    fi
    local bundle work
    bundle="${2:-}"
    [ -n "$bundle" ] || bundle="$(ls -t "$BACKUP_DIR"/remnawave_backup_*.tar.gz 2>/dev/null | head -1)"
    if [ -z "$bundle" ] || [ ! -f "$bundle" ]; then
        echo "нет доступного бэкапа для восстановления"
        exit 1
    fi
    log "ВНИМАНИЕ: восстановление ДЕСТРУКТИВНО — том $DB_VOLUME будет очищен."
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    tar xzf "$bundle" -C "$work" || { log "[err] бандл повреждён"; exit 1; }
    _compose down || true
    docker volume rm "$DB_VOLUME" 2>/dev/null || true
    tar xzf "$work/remnawave.tar.gz" -C / 2>/dev/null || true
    _compose up -d || true
    if [ -s "$work/db.sql" ]; then
        log "ожидание готовности БД..."
        for i in $(seq 1 30); do
            docker exec "$DB_CONTAINER" pg_isready -U "${POSTGRES_USER:-postgres}" >/dev/null 2>&1 && break
            sleep 2
        done
        docker exec -i "$DB_CONTAINER" psql -U "${POSTGRES_USER:-postgres}" < "$work/db.sql" >/dev/null 2>&1 \
            && log "БД восстановлена" || log "[warn] восстановление БД завершилось с ошибкой"
    fi
    log "восстановление завершено."
}

case "${1:-}" in
    backup)  cmd_backup ;;
    restore) shift; cmd_restore "$@" ;;
    status)  echo "ok" ;;
    *) echo "usage: backup-restore.sh {backup|restore --confirm [bundle]|status}"; exit 1 ;;
esac
"""


def install_script() -> str:
    """Idempotently install the wrapper into /opt/rw-backup-restore. Written via a
    single-quoted heredoc (no expansion). Contains NO secrets — safe for the
    normal (logged) SSH channel."""
    return (
        f"mkdir -p {_BR_BACKUPS}\n"
        # Backups hold a full DB dump → keep the dir private (not world-readable).
        f"chmod 700 {_BR_DIR} {_BR_BACKUPS} 2>/dev/null || true\n"
        # distillium origin noted in the wrapper header; we ship a self-contained
        # non-interactive wrapper so config/run/restore are scriptable over SSH.
        f"cat > {_BR_SCRIPT} <<'RWBR_EOF'\n" + _WRAPPER + "RWBR_EOF\n"
        f"chmod +x {_BR_SCRIPT}\n"
        # Upload CLIs — best-effort, non-fatal (telegram/local need neither).
        "command -v aws >/dev/null 2>&1 || apt-get install -y -qq awscli "
        '|| echo "[warn] awscli не установлен — метод S3 будет недоступен"\n'
        "command -v rclone >/dev/null 2>&1 || (curl -fsSL https://rclone.org/install.sh | bash) "
        '|| echo "[warn] rclone не установлен — метод Google Drive будет недоступен"\n'
        f'echo "[backup] backup-restore.sh установлен в {_BR_DIR}."\n'
    )


def config_env_script(cfg: dict) -> str:
    """Write /opt/rw-backup-restore/config.env (chmod 600, umask 077) with the
    upload method + its secrets + schedule/retention. SILENT — pipe via
    `get_script_output` so the secrets never hit a Task log.

    Each value is shell-safety-checked and single-quoted, so a `source config.env`
    in the wrapper can't execute an injected command. Raises ValueError on an
    unknown method or an unsafe value (defence in depth — the model validates too).
    """
    method = str(cfg.get("upload_method") or "local")
    if method not in UPLOAD_METHODS:
        raise ValueError(f"upload_method must be one of {UPLOAD_METHODS}")

    pairs: list[tuple[str, str]] = [("UPLOAD_METHOD", method)]
    if method == "telegram":
        pairs += [
            ("BOT_TOKEN", str(cfg.get("bot_token") or "")),
            ("CHAT_ID", str(cfg.get("chat_id") or "")),
        ]
    elif method == "s3":
        pairs += [
            ("S3_ACCESS_KEY", str(cfg.get("s3_access_key") or "")),
            ("S3_SECRET_KEY", str(cfg.get("s3_secret_key") or "")),
            ("S3_BUCKET", str(cfg.get("s3_bucket") or "")),
            ("S3_ENDPOINT", str(cfg.get("s3_endpoint") or "")),
            ("S3_REGION", str(cfg.get("s3_region") or "")),
        ]
    elif method == "google_drive":
        pairs += [
            ("GD_TOKEN", str(cfg.get("gd_token") or "")),
            ("GD_FOLDER_ID", str(cfg.get("gd_folder_id") or "")),
        ]
    pairs += [
        ("CRON_TIMES", str(cfg.get("cron_times") or "")),
        ("RETAIN_BACKUPS_DAYS", str(cfg.get("retain_days") or 7)),
        ("DB_CONNECTION_TYPE", str(cfg.get("db_connection_type") or "docker")),
    ]

    for key, val in pairs:
        if not _shell_safe(val):
            raise ValueError(f"Небезопасное значение для {key}")

    body = "".join(f"{k}='{v}'\n" for k, v in pairs)
    return (
        f"mkdir -p {_BR_DIR}\n"
        # umask in a subshell → the file is 0600 from creation (no world-readable
        # window). Single-quoted heredoc marker → nothing expands at write time.
        f"( umask 077; cat > {_BR_CONFIG} <<'RWCFG_EOF'\n" + body + "RWCFG_EOF\n"
        ")\n"
        f"chmod 600 {_BR_CONFIG}\n"
        'echo "[backup] config.env записан (секреты не выводятся)."\n'
        "echo __RWCFG_WRITTEN__\n"
    )


def setup_cron_script(cron_times: str) -> str:
    """Install/refresh a host crontab entry (marker-tagged) that runs the wrapper's
    `backup` on schedule. Overwrites OUR previous line (grep -vF the marker), never
    duplicates. `cron_times` is a 5-field cron schedule; charset-validated."""
    cron_times = (cron_times or "").strip()
    if not _CRON_RE.fullmatch(cron_times):
        raise ValueError("Недопустимое расписание cron")
    line = (
        f"{cron_times} {_BR_SCRIPT} backup >> {_BR_DIR}/backup.log 2>&1 {_CRON_MARKER}"
    )
    return (
        'TMPCRON="$(mktemp)"\n'
        f'crontab -l 2>/dev/null | grep -vF "{_CRON_MARKER}" > "$TMPCRON" || true\n'
        f'echo "{line}" >> "$TMPCRON"\n'
        'crontab "$TMPCRON"\n'
        'rm -f "$TMPCRON"\n'
        f'echo "[cron] Авто-бэкап настроен: {cron_times}"\n'
    )


def run_backup_script() -> str:
    """Run a backup right now (invokes the installed wrapper)."""
    return (
        f"if [ ! -x {_BR_SCRIPT} ]; then\n"
        '  echo "backup-restore не установлен — сначала выполните настройку"; exit 1\n'
        "fi\n"
        f"{_BR_SCRIPT} backup\n"
    )


def restore_script(confirm: bool) -> str:
    """DESTRUCTIVE restore of the latest bundle. Generates the REAL script only when
    `confirm` is True; otherwise a stub that refuses (exit 1) — so a missing confirm
    can never clear the DB volume."""
    if not confirm:
        return 'echo "restore требует подтверждения"; exit 1\n'
    return (
        f"if [ ! -x {_BR_SCRIPT} ]; then\n"
        '  echo "backup-restore не установлен — сначала выполните настройку"; exit 1\n'
        "fi\n"
        'echo "[ВНИМАНИЕ] Восстановление ДЕСТРУКТИВНО: том remnawave-db-data будет очищен."\n'
        f"{_BR_SCRIPT} restore --confirm\n"
    )


def status_script() -> str:
    """Report install / cron / config state + the most recent backups. Marker-lined
    output (RWBK_*) for the API to parse. Read-only; no secrets."""
    return (
        f'echo "RWBK_INSTALLED=$( [ -x {_BR_SCRIPT} ] && echo yes || echo no )"\n'
        f'echo "RWBK_CONFIG=$( [ -f {_BR_CONFIG} ] && echo yes || echo no )"\n'
        f"echo \"RWBK_CRON=$( crontab -l 2>/dev/null | grep -qF '{_CRON_MARKER}' && echo yes || echo no )\"\n"
        'echo "RWBK_BACKUPS_START"\n'
        f"ls -t {_BR_BACKUPS}/remnawave_backup_*.tar.gz 2>/dev/null | head -20 | while read -r f; do\n"
        '  echo "$(basename "$f")|$(stat -c %s "$f" 2>/dev/null || echo 0)|$(stat -c %Y "$f" 2>/dev/null || echo 0)"\n'
        "done\n"
        'echo "RWBK_BACKUPS_END"\n'
    )
