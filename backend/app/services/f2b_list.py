"""Fail2Ban list (Wave-5 PR-2): per-account список IP/CIDR для бана.

Источник истины — список в панели (`accounts/<id>/f2b_list.json`). При деплое
(нода/панель/SSL) backend синхронизирует его на сервере:
  * каждый адрес из списка → `fail2ban-client set sshd banip`;
  * адреса, которыми мы банили раньше, но которых в списке больше нет → unbanip
    (своя прошлая запись лежит в /etc/fail2ban/nai-banlist.txt — чужие баны,
    выставленные самим fail2ban, не трогаем);
  * файл + @reboot-строка в crontab делают баны персистентными после рестарта
    fail2ban/сервера.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shlex
import threading
from pathlib import Path
from typing import Optional

from app.services import accounts, ssh_auth
from app.services.ssh_manager import SSHSession

_LOCK = threading.Lock()
MAX_ENTRIES = 500


def _path(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "f2b_list.json"


def load(account_id: Optional[str] = None) -> list[str]:
    p = _path(account_id)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return [x for x in data if isinstance(x, str)] if isinstance(data, list) else []
    except Exception:
        pass
    return []


def validate_entry(raw: str) -> str:
    """IP или CIDR → нормализованная строка. ValueError на мусоре."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("пустая строка")
    try:
        if "/" in s:
            return str(ipaddress.ip_network(s, strict=False))
        return str(ipaddress.ip_address(s))
    except ValueError:
        raise ValueError(f"Некорректный IP/CIDR: {s}") from None


def save(entries: list[str], account_id: Optional[str] = None) -> list[str]:
    """Валидация + дедуп; возвращает сохранённый список. ValueError на мусоре."""
    out: list[str] = []
    for e in entries:
        v = validate_entry(e)
        if v not in out:
            out.append(v)
    if len(out) > MAX_ENTRIES:
        raise ValueError(f"Не больше {MAX_ENTRIES} записей")
    p = _path(account_id)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


_BANLIST_FILE = "/etc/fail2ban/nai-banlist.txt"
_CRON_MARK = "nai-f2b-banlist"


def sync_script(entries: list[str], jails: Optional[list[str]] = None) -> str:
    """Bash-синхронизация: баним список, разбаниваем своё снятое, персистентим.
    Идемпотентна — безопасна на каждом деплое."""
    quoted = "\n".join(shlex.quote(e) for e in entries)
    jail_names = jails or ["sshd"]
    ban_commands = "\n  ".join(
        f"fail2ban-client set {shlex.quote(jail)} banip \"$ip\" >/dev/null 2>&1 || true"
        for jail in jail_names
    )
    unban_commands = "\n    ".join(
        f"fail2ban-client set {shlex.quote(jail)} unbanip \"$old\" >/dev/null 2>&1 || true"
        for jail in jail_names
    )
    reboot_commands = " ".join(
        f"fail2ban-client set {shlex.quote(jail)} banip \\\"\\$ip\\\";"
        for jail in jail_names
    )
    return f"""\
set -u
if ! command -v fail2ban-client >/dev/null 2>&1; then
  echo "[f2b-list] fail2ban не установлен — пропускаю"
  exit 0
fi
# 1. Сохраняем текущий список (источник истины — в панели)
cat > {_BANLIST_FILE} << 'NAIEOF'
{quoted}
NAIEOF

# 2. Баним всё из списка (повторный banip безвреден)
while IFS= read -r ip; do
  [ -n "$ip" ] || continue
  {ban_commands}
done < {_BANLIST_FILE}

# 3. Разбаниваем то, что мы банили раньше, но чего в списке больше нет
if [ -f {_BANLIST_FILE}.prev ]; then
  while IFS= read -r old; do
    [ -n "$old" ] || continue
    grep -qxF "$old" {_BANLIST_FILE} || (
      {unban_commands}
    )
  done < {_BANLIST_FILE}.prev
fi
cp {_BANLIST_FILE} {_BANLIST_FILE}.prev

# 4. Персистентность: повторное применение после рестарта fail2ban/сервера
(crontab -l 2>/dev/null | grep -v '{_CRON_MARK}'; echo "@reboot sleep 20 && while IFS= read -r ip; do [ -n \\"\\$ip\\" ] && {reboot_commands}; done < {_BANLIST_FILE} # {_CRON_MARK}") | crontab - 2>/dev/null || true
echo "[f2b-list] применено записей: $(grep -c . {_BANLIST_FILE} 2>/dev/null || echo 0)"
"""


def parse_banned_output(raw: str) -> list[str]:
    """Extract IP addresses from fail2ban-client's version-dependent output."""
    result: list[str] = []
    for token in re.split(r"[\s,\[\]()'\"]+", raw or ""):
        try:
            address = str(ipaddress.ip_address(token))
        except ValueError:
            continue
        if address not in result:
            result.append(address)
    return result


async def collect_node(req, jails: list[str]) -> list[str]:
    """Read active bans from requested jails without changing the central list."""
    ssh = None
    try:
        ssh = SSHSession(
            req.ip, req.ssh_port, req.ssh_user, **await ssh_auth.resolve(req)
        )
        await ssh.connect()
        ips: list[str] = []
        for jail in jails:
            output = await ssh.get_output(
                f"fail2ban-client get {shlex.quote(jail)} banned 2>/dev/null || true"
            )
            for address in parse_banned_output(output):
                if address not in ips:
                    ips.append(address)
        return ips
    finally:
        if ssh is not None:
            await ssh.close()


async def apply_node(req, entries: list[str], jails: list[str]) -> dict[str, int]:
    """Apply the central list and only unban addresses recorded by NAI earlier."""
    ssh = None
    try:
        ssh = SSHSession(
            req.ip, req.ssh_port, req.ssh_user, **await ssh_auth.resolve(req)
        )
        await ssh.connect()
        current_by_jail = []
        for jail in jails:
            output = await ssh.get_output(
                f"fail2ban-client get {shlex.quote(jail)} banned 2>/dev/null || true"
            )
            current_by_jail.append(set(parse_banned_output(output)))
        previous = set(parse_banned_output(await ssh.get_output(
            f"cat {_BANLIST_FILE}.prev 2>/dev/null || true"
        )))
        desired = set(entries)
        skipped = sum(
            1 for entry in entries if all(entry in current for current in current_by_jail)
        )
        await ssh.get_script_output(sync_script(entries, jails))
        return {
            "applied": len(entries) - skipped,
            "unbanned": len(previous - desired),
            "skipped": skipped,
        }
    finally:
        if ssh is not None:
            await ssh.close()
