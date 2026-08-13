"""SSL management — deploy (issue + install) a cert onto a live node.

The «Управление SSL» section (Ф10). Unlike the old renew-only flow, this ISSUES
a fresh per-FQDN cert with the chosen provider (cloudflare DNS-01 / letsencrypt
HTTP-01 / zerossl EAB), reusing the deploy pipeline's `build_ssl_script` so both
paths share one source of truth. If the node already has a valid cert for the
domain (openssl probe), it reports that and skips — unless `force` is set.

Sub-steps (streamed via the generic /ws/logs task, own labels — not the 13-step
deploy numbering): 1 connect + probe, 2 issue+install, 3 restart services.
"""
import base64
import asyncio
import io
import re
import zipfile

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.models.deploy import DeployCertRequest
from app.services.task_store import task_store, TaskStatus
from app.services.ssh_manager import SSHSession
from app.services import pipeline
from app.services.cloudflare import upsert_a_record

router = APIRouter(prefix="/api")

# FQDN allowlist — `domain` is interpolated into a remote file path, so restrict
# it to hostname chars (no shell/path metacharacters) before use.
_DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)
# Selectable cert files → (remote path template, download filename template).
_CERT_FILES = {
    "fullchain": ("/etc/ssl/certs/{d}_fullchain.pem", "{d}_fullchain.pem"),
    "key":       ("/etc/ssl/private/{d}.key",          "{d}.key"),
    "cert":      ("/etc/ssl/certs/{d}.crt",            "{d}.crt"),
}

# Labels sent via WebSocket — must match RENEW_STEPS in StepProgress.tsx
DEPLOY_STEP_LABELS = [
    "Подключение к серверу",
    "Выпуск и установка сертификата",
    "Перезапуск сервисов",
]
DEPLOY_TOTAL = len(DEPLOY_STEP_LABELS)


# ── Domain auto-scan (Wave-4 PR-3) ─────────────────────────────
# «Авто» рядом с полем домена: по SSH собираем ВСЕ домены сервера из nginx/
# apache server_name, certbot live, конфигов xray/remnanode и env масксайта.
# Скрипт read-only; пароль живёт только в SSH-сессии запроса.

class ScanDomainsRequest(BaseModel):
    ip: str
    ssh_port: int = 22
    ssh_user: str
    ssh_password: str


_SCAN_SCRIPT = r"""
echo '== nginx =='
grep -rhoE 'server_name[[:space:]]+[^;]+' /etc/nginx /etc/apache2 2>/dev/null
echo '== certbot =='
ls /etc/letsencrypt/live 2>/dev/null
echo '== xray =='
grep -rhoE '"(dest|serverName)"[[:space:]]*:[[:space:]]*"[^"]+"' \
  /usr/local/etc/xray /opt/remnanode /etc/xray 2>/dev/null
echo '== env =='
grep -hE '^(SELFSTEAL|SELF_STEAL|FAKE_SITE|MASK|DOMAIN|SERVER_NAME)=' \
  /opt/remnanode/.env 2>/dev/null
docker ps --format '{{.Names}}' 2>/dev/null | while read c; do \
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" 2>/dev/null; \
done | grep -E '^(SELFSTEAL|SELF_STEAL|FAKE_SITE|MASK|DOMAIN|SERVER_NAME)='
"""


def _parse_scan(out: str) -> list[dict]:
    """Разбор вывода _SCAN_SCRIPT → [{domain, sources}]. Чистая функция (тесты)."""
    domains: dict[str, set[str]] = {}

    def add(raw: str, source: str) -> None:
        d = raw.strip().strip('"').strip("'").lower().rstrip(".")
        if not d or d == "_" or d.startswith("*.") or d == "default_server":
            return
        if _DOMAIN_RE.match(d):
            domains.setdefault(d, set()).add(source)

    section = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("== "):
            section = line.strip("= ").strip()
            continue
        if not line:
            continue
        if section == "nginx":
            # "server_name a.com b.com" — имён может быть несколько
            for tok in re.split(r"[\s;,]+", line.split("server_name", 1)[-1]):
                add(tok, "nginx")
        elif section == "certbot":
            # каталоги live/<domain>[-0001]
            add(re.sub(r"-\d{4}$", "", line), "certbot")
        elif section == "xray":
            m = re.match(r'"(?:dest|serverName)"\s*:\s*"([^"]+)"', line)
            if m:
                for tok in m.group(1).split(","):
                    # dest часто "domain:port"
                    add(tok.strip().split(":")[0], "xray")
        elif section == "env":
            _, _, val = line.partition("=")
            v = re.sub(r"^https?://", "", val.strip()).split("/")[0]
            add(v, "env")
    return [{"domain": d, "sources": sorted(s)} for d, s in sorted(domains.items())]


@router.post("/certs/scan-domains")
async def scan_domains(req: ScanDomainsRequest):
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        await ssh.connect()
        out = await ssh.get_script_output(_SCAN_SCRIPT, timeout=60)
    except Exception as exc:
        raise HTTPException(502, f"Сканирование не удалось: {exc}")
    finally:
        await ssh.close()
    return {"domains": _parse_scan(out)}


# ── ACME-статус и SelfSteal (Wave-5 PR-1, механики remnawave-reverse) ──────

_ACME_STATUS_SCRIPT = r"""
for f in /etc/ssl/certs/*_fullchain.pem; do
  [ -f "$f" ] || continue
  d=$(basename "$f" _fullchain.pem)
  end=$(openssl x509 -enddate -noout -in "$f" 2>/dev/null | cut -d= -f2-)
  ca=$(grep -i "^Le_API" "/root/.acme.sh/${d}_ecc/${d}.conf" 2>/dev/null | cut -d= -f2- | tr -d "'\" ")
  echo "CERT=$d|$end|$ca"
done
crontab -l 2>/dev/null | grep -q "acme.sh" && echo "CRON=1" || echo "CRON=0"
"""


def _parse_acme_status(out: str) -> dict:
    certs = []
    cron = False
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("CERT="):
            domain, _, rest = line[5:].partition("|")
            not_after, _, ca = rest.partition("|")
            certs.append({"domain": domain.strip(), "not_after": not_after.strip(),
                          "ca": ca.strip()})
        elif line == "CRON=1":
            cron = True
    return {"certs": certs, "renewal_cron": cron}


@router.post("/certs/acme-status")
async def acme_status(req: ScanDomainsRequest):
    """Сводка ACME на сервере: сертификаты (домен, истечение, CA) + cron продления."""
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        await ssh.connect()
        out = await ssh.get_script_output(_ACME_STATUS_SCRIPT, timeout=45)
    except Exception as exc:
        raise HTTPException(502, f"Опрос не удался: {exc}")
    finally:
        await ssh.close()
    return _parse_acme_status(out)


SELFSTEAL_STEP_LABELS = ["Смена маскировочного сайта"]


@router.post("/certs/selfsteal")
async def selfsteal_refresh(req: ScanDomainsRequest, background_tasks: BackgroundTasks):
    """Смена маскировочного (SelfSteal) шаблона на уже развёрнутой ноде —
    тот же masking-скрипт, что и в деплое, без полного редеплоя."""
    task = task_store.create(total_steps=len(SELFSTEAL_STEP_LABELS))
    background_tasks.add_task(_selfsteal_run, req, task.task_id)
    return {"task_id": task.task_id, "task_type": "selfsteal"}


async def _selfsteal_run(req: ScanDomainsRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"Подключение к {req.ip}:{req.ssh_port}...")
        await ssh.connect()
        task.add_log("Меняю маскировочный сайт (случайный шаблон + уникализация)...")
        await ssh.run_script(pipeline.masking_script(), task, timeout=240)
        task.finish(TaskStatus.SUCCESS)
    except Exception as exc:
        task.add_log(f"Ошибка: {exc}")
        task.finish(TaskStatus.FAILED)
    finally:
        await ssh.close()


@router.post("/certs/deploy")
async def deploy_cert(req: DeployCertRequest):
    task = task_store.create(total_steps=DEPLOY_TOTAL)
    # create_task (не BackgroundTasks) — иначе останавливать нечего (Wave-5 PR-4).
    loop_task = asyncio.create_task(_deploy(req, task.task_id))
    _cert_tasks[task.task_id] = loop_task
    loop_task.add_done_callback(lambda _: _cert_tasks.pop(task.task_id, None))
    return {"task_id": task.task_id, "task_type": "certs"}


# Реестр живых задач деплоя сертификатов — для остановки (Wave-5 PR-4).
_cert_tasks: dict[str, asyncio.Task] = {}


class StopRequest(BaseModel):
    task_id: str


@router.post("/certs/stop")
async def stop_cert_deploy(req: StopRequest):
    loop_task = _cert_tasks.get(req.task_id)
    if loop_task is not None and not loop_task.done():
        loop_task.cancel()
        task_store.request_cancel(req.task_id)
        return {"ok": True}
    # Задача на воркере (или handle уже утерян): флаг в сторе — воркер сам
    # отменит свою asyncio-задачу, когда увидит флаг.
    t = task_store.get(req.task_id)
    if t and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        task_store.request_cancel(req.task_id)
        return {"ok": True}
    raise HTTPException(404, "Задача не найдена или уже завершена")


# ── перенос сертификатов между серверами (Wave-5 PR-4) ─────────
# Backend читает файлы домена с сервера A (память) и раскладывает на сервере B
# по тем же путям + letsencrypt-симлинки + reload nginx. Креды обоих серверов —
# per-request, нигде не сохраняются.

class CredsBody(BaseModel):
    ip: str
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)


class TransferBody(BaseModel):
    source: CredsBody
    target: CredsBody
    domains: list[str] = Field(..., min_length=1)

    @field_validator("domains")
    @classmethod
    def _v_domains(cls, v: list[str]) -> list[str]:
        out = []
        for d in v:
            d = d.strip().lower()
            if not _DOMAIN_RE.fullmatch(d):
                raise ValueError(f"Некорректный домен: {d}")
            out.append(d)
        return out


TRANSFER_STEP_LABELS = ["Чтение с сервера-источника", "Запись на целевой сервер", "Перезапуск nginx"]

# Файлы домена, которые переносим: установленные пути + acme.sh-хранилище
# (чтобы на новом сервере продолжило работать автопродление).
def _transfer_paths(domain: str) -> list[str]:
    return [
        f"/etc/ssl/certs/{domain}_fullchain.pem",
        f"/etc/ssl/certs/{domain}.crt",
        f"/etc/ssl/private/{domain}.key",
    ]


def _read_bundle_script(domain: str) -> str:
    files = " ".join(_transfer_paths(domain))
    return f"""\
T=$(mktemp /tmp/nai-cert-XXXXXXXX.tar.gz)
tar czf "$T" -C / $(for f in {files}; do [ -f "$f" ] && echo "${{f#/}}"; done) \
  -C / $( [ -d /root/.acme.sh/{domain}_ecc ] && echo "root/.acme.sh/{domain}_ecc" ) 2>/dev/null
if [ -s "$T" ]; then echo __OK__; base64 -w0 "$T"; else echo __EMPTY__; fi
rm -f "$T"
"""


def _write_bundle_script(domain: str, b64: str) -> str:
    return f"""\
set -e
T=$(mktemp /tmp/nai-cert-XXXXXXXX.tar.gz)
base64 -d > "$T" <<'NAI_B64'
{b64}
NAI_B64
tar xzf "$T" -C /
rm -f "$T"
chmod 600 /etc/ssl/private/{domain}.key 2>/dev/null || true
mkdir -p /etc/letsencrypt/live/{domain}
[ -f /etc/ssl/certs/{domain}_fullchain.pem ] && ln -sf /etc/ssl/certs/{domain}_fullchain.pem /etc/letsencrypt/live/{domain}/fullchain.pem || true
[ -f /etc/ssl/private/{domain}.key ] && ln -sf /etc/ssl/private/{domain}.key /etc/letsencrypt/live/{domain}/privkey.pem || true
echo "WROTE={domain}"
"""


@router.post("/certs/transfer")
async def transfer_certs(body: TransferBody, background_tasks: BackgroundTasks):
    task = task_store.create(total_steps=len(TRANSFER_STEP_LABELS))
    background_tasks.add_task(_transfer_run, body, task.task_id)
    return {"task_id": task.task_id, "task_type": "certs-transfer"}


async def _transfer_run(body: TransferBody, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    src = SSHSession(body.source.ip, body.source.ssh_port, body.source.ssh_user, body.source.ssh_password)
    dst = SSHSession(body.target.ip, body.target.ssh_port, body.target.ssh_user, body.target.ssh_password)
    try:
        task.set_step(1, TaskStatus.RUNNING)
        task.add_log(f"Источник: {body.source.ip}, цель: {body.target.ip}, доменов: {len(body.domains)}")
        await src.connect()
        await dst.connect()
        task.add_log("Оба сервера подключены.")
        bundles: dict[str, str] = {}
        for d in body.domains:
            out = await src.get_script_output(_read_bundle_script(d), timeout=60)
            if "__OK__" not in out:
                raise RuntimeError(f"на источнике нет файлов сертификата для {d}")
            bundles[d] = out.split("__OK__", 1)[1].strip()
            task.add_log(f"Прочитан бандл {d} ({len(bundles[d]) // 1024} КиБ в base64).")

        task.set_step(2, TaskStatus.RUNNING)
        for d, b64 in bundles.items():
            out = await dst.get_script_output(_write_bundle_script(d, b64), timeout=60)
            if f"WROTE={d}" not in out:
                raise RuntimeError(f"запись на целевой сервер не подтверждена для {d}: {out[-200:]}")
            task.add_log(f"Записан {d} (пути /etc/ssl + acme.sh + letsencrypt-симлинки).")

        task.set_step(3, TaskStatus.RUNNING)
        reload_out = await dst.get_output(
            "systemctl reload nginx 2>/dev/null && echo RELOADED || echo NO_NGINX")
        task.add_log(f"nginx: {reload_out or 'не ответил'}")
        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Перенос завершён.\x1b[0m")
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await src.close()
        await dst.close()


async def _deploy(req: DeployCertRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    try:
        # ── Step 1: connect + probe existing cert ─────────────
        task.set_step(1, TaskStatus.RUNNING)
        _log_step(task, 1, DEPLOY_STEP_LABELS[0])
        task.add_log(f"Подключение к {req.ip}:{req.ssh_port} как {req.ssh_user}...")
        await ssh.connect()
        os_info = await ssh.get_output(
            "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'"
        )
        task.add_log(f"\x1b[32mПодключено. ОС: {os_info or 'unknown'}\x1b[0m")

        existing = await _probe_cert(ssh, req.domain)
        if existing and not req.force:
            task.add_log(
                f"\x1b[1;33m[SSL] Сертификат для {req.domain} уже установлен "
                f"(истекает: {existing}). Передеплой пропущен — включите "
                f"«Принудительно», чтобы переустановить.\x1b[0m"
            )
            task.set_step(DEPLOY_TOTAL, TaskStatus.RUNNING)
            task.finish(TaskStatus.SUCCESS)
            return
        if existing:
            task.add_log(f"\x1b[33m[SSL] Сертификат уже есть (истекает {existing}) — принудительный передеплой.\x1b[0m")

        # ── Step 2: issue + install via the chosen provider ────
        task.set_step(2, TaskStatus.RUNNING)
        _log_step(task, 2, DEPLOY_STEP_LABELS[1])
        if pipeline.ssl_needs_cf_dns(req.cert_provider):
            task.add_log(f"[CF] Обновляю A-запись {req.domain} → {req.ip}...")
            await upsert_a_record(req.cf_api_key or "", req.domain, req.ip)
            task.add_log(f"\x1b[32m[CF] A-запись обновлена.\x1b[0m")
        else:
            task.add_log(
                f"\x1b[33m[SSL] Провайдер '{req.cert_provider}' использует HTTP-01 (порт 80). "
                f"Убедитесь, что {req.domain} уже указывает на {req.ip}.\x1b[0m"
            )
        script = pipeline.build_ssl_script(
            req.domain, req.email, req.cf_api_key or "", req.cert_provider
        )
        await ssh.run_script(script, task, timeout=360)

        # ── Step 3: restart services that use the cert ─────────
        task.set_step(3, TaskStatus.RUNNING)
        _log_step(task, 3, DEPLOY_STEP_LABELS[2])
        await _restart_services(ssh, task)

        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Сертификат задеплоен успешно!\x1b[0m")

    except asyncio.CancelledError:
        task.add_log("\n\x1b[1;33m■ Деплой остановлен пользователем.\x1b[0m")
        task.finish(TaskStatus.FAILED, "остановлено пользователем")
        raise
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
    finally:
        await ssh.close()


# ── helpers ───────────────────────────────────────────────────

def _log_step(task, index: int, label: str) -> None:
    task.add_log(f"\n\x1b[36m{'─' * 56}\x1b[0m")
    task.add_log(f"\x1b[1;36m[{index}/{DEPLOY_TOTAL}] {label}\x1b[0m")
    task.add_log(f"\x1b[36m{'─' * 56}\x1b[0m")


async def _probe_cert(ssh: SSHSession, domain: str) -> str | None:
    """Return the installed cert's notAfter string for `domain`, or None if no
    valid cert is present (same path step_ssl installs to). Never raises."""
    script = (
        f'CERT="/etc/ssl/certs/{domain}_fullchain.pem"; '
        'if [ -s "$CERT" ]; then '
        'openssl x509 -enddate -noout -in "$CERT" 2>/dev/null | cut -d= -f2; '
        'fi'
    )
    try:
        out = (await ssh.get_output(script)).strip()
        return out or None
    except Exception:
        return None


async def _restart_services(ssh: SSHSession, task) -> None:
    """Detect running VPN/proxy services that use TLS certs and restart them."""
    candidates = [
        "nginx", "hysteria-server", "hysteria2", "remnawave-node",
        "xray", "sing-box", "v2ray",
    ]
    restart_script = ""
    for svc in candidates:
        restart_script += f"""\
if systemctl is-enabled {svc} &>/dev/null 2>&1 || systemctl is-active {svc} &>/dev/null 2>&1; then
    echo -n "  {svc}: "
    systemctl restart {svc} 2>&1 && echo "restarted" || echo "WARN: restart failed"
fi
"""
    full_script = f"""\
echo "Detecting running services..."
{restart_script}
echo ""
echo "Service summary:"
systemctl list-units --type=service --state=running \\
    | grep -E "nginx|hysteria|remna|xray|sing-box|v2ray" || echo "(none matched)"
"""
    await ssh.run_script(full_script, task, timeout=60)


# ── cert download (Ф8) ────────────────────────────────────────

class DownloadCertRequest(BaseModel):
    """Read a node's installed cert files over SSH and stream them back. SSH creds
    are per-request/transient (never persisted). `files` ⊆ {fullchain, key}."""
    ip: str
    ssh_user: str = "root"
    ssh_password: str
    ssh_port: int = 22
    domain: str
    files: list[str] = []

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        # fullmatch (not match): `$` alone would accept a trailing newline.
        if not _DOMAIN_RE.fullmatch(v):
            raise ValueError("Invalid domain (hostname expected)")
        return v


async def _read_remote_file(ssh: SSHSession, path: str) -> bytes | None:
    """base64-read a remote file SILENTLY (get_output logs nothing — the private
    key must never hit a task log). Returns None if the file is absent/empty."""
    # `path` is safe: domain is FQDN-validated, the rest is a fixed literal.
    # Cap the read at 8 MiB (certs are KB) so a stray huge file at the cert path
    # can't OOM the backend buffering all stdout.
    script = f'F="{path}"; if [ -s "$F" ]; then echo __OK__; head -c 8388608 "$F" | base64; else echo __MISSING__; fi'
    out = await ssh.get_output(script)
    if "__OK__" not in out:
        return None
    b64 = out.split("__OK__", 1)[1].strip()
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


@router.post("/certs/download")
async def download_cert(req: DownloadCertRequest):
    # "bundle" (Wave-5 PR-4) = все присутствующие файлы сертификата одним zip.
    if "bundle" in req.files:
        sel = list(_CERT_FILES)
    else:
        sel = [f for f in req.files if f in _CERT_FILES]
    if not sel:
        raise HTTPException(422, "Не выбраны файлы для скачивания")

    bundle = "bundle" in req.files
    ssh = SSHSession(req.ip, req.ssh_port, req.ssh_user, req.ssh_password)
    collected: list[tuple[str, bytes]] = []
    missing: list[str] = []
    try:
        await ssh.connect()
        for f in sel:
            path_tpl, name_tpl = _CERT_FILES[f]
            data = await _read_remote_file(ssh, path_tpl.format(d=req.domain))
            name = name_tpl.format(d=req.domain)
            if data is None:
                if bundle:
                    missing.append(name)
                    continue
                raise HTTPException(404, f"Сертификат не найден на ноде: {name}")
            collected.append((name, data))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Не удалось прочитать сертификаты по SSH: {str(exc)[:200]}")
    finally:
        await ssh.close()

    if not collected:
        raise HTTPException(404, f"Сертификат {req.domain} не найден на ноде")
    # Single file → return it directly; multiple → zip them.
    if len(collected) == 1 and not bundle:
        name, data = collected[0]
        return Response(
            content=data, media_type="application/x-pem-file",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in collected:
            zf.writestr(name, data)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{req.domain}-certs.zip"'},
    )
