"""RKN Scanner list — центральный список адресов сканеров, собранный со ВСЕХ нод.

Зеркально разделу fail2ban (`services/f2b_list.py`), но поток данных обратный:
там список рождается в панели и разъезжается по нодам, здесь он рождается НА нодах
и собирается в панель.

  * источник истины — `accounts/<id>/rkn_scanners.json` (per-account, как все сторы);
  * наполняется он с нод: доп. компонент шага 4 (`services/rkn_watcher.py`, цепочка
    `RKN_EXTRA`) логирует попытки соединений из наборов TSPUIPS/GOVIPS/
    EXTRA_ANTISCAN/EXTRA_GOVNET с префиксом `RKNSCAN:`; `probe_script()` читает эти
    строки из `journalctl -k` и агрегирует их НА УЗЛЕ (IP ↔ порт ↔ число попыток) —
    по SSH едут десятки строк, а не мегабайты ядра;
  * `apply_script()` раздаёт центральный список обратно на ноды: ipset
    `RKN_SCANNERS_V4` (hash:net, maxelem 100000) + цепочка `RKN_SCANNERS` в INPUT.

Тесты: `backend/tests/test_rkn_scanners.py`.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shlex
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from app.services import accounts, ssh_auth
from app.services.ssh_manager import SSHSession

_LOCK = threading.Lock()
MAX_ENTRIES = 100_000          # ровно столько же, сколько maxelem у набора RKN_SCANNERS_V4
STORAGE_FILE = "rkn_scanners.json"
DEFAULT_SINCE_HOURS = 24
MAX_SINCE_HOURS = 720
PROBE_MARKER = "RKNSCAN:"

SET_NAME = "RKN_SCANNERS_V4"
CHAIN_NAME = "RKN_SCANNERS"
STATE_DIR = "/var/lib/rkn-scanners"
RESTORE_PATH = "/usr/local/sbin/rkn-scanners-restore.sh"
SET_MAXELEM = 100_000

# Не блокируем сами себя. Первые четыре сети — те же, что RKN_PRIVATE_WHITELIST в
# rkn_watcher (адрес панели и whitelist запроса там идут отдельно), плюс link-local.
SCANNERS_PRIVATE = (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
)

# Значения, которые запись может получить в поле `source`.
SOURCE_PROBE = "probe"
SOURCE_MANUAL = "manual"


# ── стор ───────────────────────────────────────────────────────

def _path(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / STORAGE_FILE


_STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> str:
    """UTC-метка фиксированной ширины: такие строки сортируются как время."""
    return datetime.now(timezone.utc).strftime(_STAMP_FMT)


def _as_stamp(value: Any) -> str:
    """Метка времени → наш формат (UTC, ISO, секунды).

    Панель шлёт `Date.now()` (число, миллисекунды) — приводим к общему виду, иначе
    строки разной формы несравнимы и `lastSeen` в списке «прыгает».
    """
    if value is None or value == "" or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value >= 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(_STAMP_FMT)
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:40]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime(_STAMP_FMT)


def validate_entry(raw: str) -> str:
    """IP или CIDR → нормализованная строка. ValueError на мусоре.

    Одиночный адрес, записанный как `1.2.3.4/32`, приводится к `1.2.3.4`: иначе один
    и тот же адрес лежал бы в списке дважды и дважды уезжал в набор.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("пустая строка")
    try:
        if "/" in s:
            net = ipaddress.ip_network(s, strict=False)
            if net.prefixlen == net.max_prefixlen:
                return str(net.network_address)
            return str(net)
        return str(ipaddress.ip_address(s))
    except ValueError:
        raise ValueError(f"Некорректный IP/CIDR: {s}") from None


def _clean_entries(entries: Iterable[str]) -> list[str]:
    """Валидация + дедуп с сохранением порядка. ValueError на мусоре/переполнении."""
    out: list[str] = []
    for entry in entries:
        value = validate_entry(entry)
        if value not in out:
            out.append(value)
    if len(out) > MAX_ENTRIES:
        raise ValueError(f"Не больше {MAX_ENTRIES} записей")
    return out


def _valid_ip(value: Any) -> Optional[str]:
    try:
        return str(ipaddress.ip_address(str(value)))
    except (ValueError, TypeError):
        return None


def _as_port(value: Any) -> Optional[int]:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port <= 65535 else None


def _as_hits(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _as_text(value: Any, limit: int = 64) -> str:
    return str(value or "").strip()[:limit]


def normalize_record(raw: Any) -> Optional[dict]:
    """Запись стора из чего угодно (строка, словарь, запись пробы) или None.

    Молча выбрасывает нечитаемое: файл лежит на диске и мог быть отредактирован
    руками — уронить из-за этого весь раздел хуже, чем потерять одну запись.
    """
    if isinstance(raw, str):
        raw = {"ip": raw}
    if not isinstance(raw, dict):
        return None
    try:
        ip = validate_entry(str(raw.get("ip", "")))
    except ValueError:
        return None
    nodes: list[str] = []
    for node in raw.get("nodes") or []:
        value = _valid_ip(node)
        if value and value not in nodes:
            nodes.append(value)
    first = _as_stamp(raw.get("firstSeen"))
    last = _as_stamp(raw.get("lastSeen"))
    return {
        "ip": ip,
        "firstSeen": first,
        "lastSeen": last or first,
        "hits": _as_hits(raw.get("hits")),
        "nodes": nodes,
        "port": _as_port(raw.get("port")),
        "chain": _as_text(raw.get("chain"), 32) or None,
        "source": _as_text(raw.get("source"), 32) or SOURCE_PROBE,
    }


def load_document(account_id: Optional[str] = None) -> dict:
    """`{"updatedAt": ..., "entries": [...]}` — как файл лежит на диске (уже нормализованный)."""
    p = _path(account_id)
    raw: Any = {}
    try:
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if isinstance(raw, list):                 # файл-список: так выглядит ручная правка
        raw = {"entries": raw}
    if not isinstance(raw, dict):
        raw = {}
    entries: list[dict] = []
    seen: set[str] = set()
    for item in raw.get("entries") or []:
        record = normalize_record(item)
        if record and record["ip"] not in seen:
            seen.add(record["ip"])
            entries.append(record)
    return {"updatedAt": _as_stamp(raw.get("updatedAt")), "entries": entries}


def load(account_id: Optional[str] = None) -> list[dict]:
    return load_document(account_id)["entries"]


def _write(document: dict, account_id: Optional[str] = None) -> dict:
    p = _path(account_id)
    # `updatedAt` — метка последнего ИЗМЕНЕНИЯ списка: явная метка из документа,
    # иначе прежняя из файла, и только если файла ещё нет — сейчас.
    stamp = _as_stamp(document.get("updatedAt")) or load_document(account_id)["updatedAt"] or _now()
    out = {"updatedAt": stamp, "entries": document.get("entries") or []}
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _merge_record(old: Optional[dict], new: dict, now: str) -> dict:
    """Свести прежнюю запись с новой. Счётчики только растут, `firstSeen` не сдвигается."""
    if not old:
        return {
            "ip": new["ip"],
            "firstSeen": new.get("firstSeen") or now,
            "lastSeen": new.get("lastSeen") or now,
            "hits": _as_hits(new.get("hits")),
            "nodes": list(new.get("nodes") or []),
            "port": new.get("port"),
            "chain": new.get("chain"),
            "source": new.get("source") or SOURCE_PROBE,
        }
    merged = dict(old)
    # Метки одного формата (UTC, фиксированная ширина) — сравнение строк равно сравнению времени.
    merged["firstSeen"] = min(old["firstSeen"] or now, new.get("firstSeen") or now)
    merged["lastSeen"] = max(old["lastSeen"] or now, new.get("lastSeen") or now)
    merged["hits"] = max(old["hits"], _as_hits(new.get("hits")))
    nodes = list(old["nodes"])
    for node in new.get("nodes") or []:
        if node not in nodes:
            nodes.append(node)
    merged["nodes"] = nodes
    if new.get("port"):
        merged["port"] = new["port"]
    if new.get("chain"):
        merged["chain"] = new["chain"]
    if new.get("source"):
        merged["source"] = new["source"]
    return merged


def save(entries: list[Any], account_id: Optional[str] = None) -> list[dict]:
    """Валидация + дедуп + слияние с историей. ValueError на мусоре.

    Ручная правка приходит списком адресов: известные адреса сохраняют firstSeen,
    hits и nodes (счётчики — журнал наблюдений, а не поле для правки), новые
    получают source=manual.
    """
    previous = {record["ip"]: record for record in load(account_id)}
    now = _now()
    out: list[dict] = []
    seen: set[str] = set()
    for raw in entries:
        record = normalize_record(raw)
        if record is None:
            # Строку с мусором отдаём как ValueError — ручка отвечает 422, а не 500.
            if isinstance(raw, str):
                validate_entry(raw)
            raise ValueError(f"Некорректная запись: {str(raw)[:64]}")
        ip = record["ip"]
        if ip in seen:
            continue
        seen.add(ip)
        old = previous.get(ip)
        explicit = _as_text(raw.get("source"), 32) if isinstance(raw, dict) else ""
        # Происхождение не переписывается правкой: известный адрес остаётся «собранным
        # с ноды», новый (появившийся руками) помечается manual.
        record["source"] = explicit or (old["source"] if old else SOURCE_MANUAL)
        out.append(_merge_record(old, record, now))
    _write({"entries": out, "updatedAt": now}, account_id)
    return out


def clear(account_id: Optional[str] = None) -> dict:
    """Очистка центрального списка (файл остаётся, чтобы не терять права/путь)."""
    return _write({"entries": [], "updatedAt": _now()}, account_id)


def merge(entries: list[Any], node_ip: str, account_id: Optional[str] = None,
          source: str = SOURCE_PROBE) -> dict:
    """Влить наблюдения ОДНОЙ ноды в центральный список.

    `entries` — то, что вернула проба (словари вида `{"ip", "port", "chain", "hits"}`
    или просто адреса). Адрес, который уже есть в списке, не дублируется: у него
    обновляются lastSeen/nodes/port/chain и СУММИРУЮТСЯ hits.
    """
    node = _valid_ip(node_ip) or _as_text(node_ip, 64)
    current = load(account_id)
    by_ip = {record["ip"]: record for record in current}
    now = _now()
    added = updated = hits_added = 0
    for raw in entries:
        record = normalize_record(raw)
        if record is None:
            continue
        record["nodes"] = [node] if node else []
        record["source"] = source
        record["lastSeen"] = record.get("lastSeen") or now   # наблюдение сделано сейчас
        old = by_ip.get(record["ip"])
        before = old["hits"] if old else 0
        # Проба отдаёт hits ЗА СВОЙ ПЕРИОД: складываем с накопленным. `_merge_record`
        # держит счётчик монотонным (max), поэтому сумму считаем здесь.
        record["hits"] = before + _as_hits(record.get("hits"))
        merged = _merge_record(old, record, now)
        by_ip[merged["ip"]] = merged
        hits_added += merged["hits"] - before
        if old is None:
            added += 1
        else:
            updated += 1
    saved = _write(
        {"entries": list(by_ip.values()), "updatedAt": now if (added or updated) else None},
        account_id,
    )
    return {
        "added": added,
        "updated": updated,
        "hitsAdded": hits_added,
        "total": len(saved["entries"]),
    }


# ── проба ноды (чтение лога RKNSCAN:) ──────────────────────────

# Строка LOG-правила ядра: `RKNSCAN:antiscan IN=eth0 ... SRC=1.2.3.4 ... SPT=45678 ...`
_LOG_SRC_RE = re.compile(r"(?:^|\s)SRC=(\d{1,3}(?:\.\d{1,3}){3})(?:\s|$)")
_LOG_SPT_RE = re.compile(r"(?:^|\s)SPT=(\d{1,5})(?:\s|$)")
_LOG_CHAIN_RE = re.compile(r"RKNSCAN:([A-Za-z0-9_]+)")


def parse_log_line(line: str) -> Optional[dict]:
    """Одна строка лога ядра → `{"ip", "port", "chain"}` или None.

    Порт — SPT (порт ИСТОЧНИКА), как он записан в строке LOG; DPT в записи не
    сохраняем, потому что в модели один порт на адрес.
    """
    if not line or PROBE_MARKER not in line:
        return None
    src = _LOG_SRC_RE.search(line)
    if not src:
        return None
    ip = _valid_ip(src.group(1))
    if not ip:
        return None
    port_match = _LOG_SPT_RE.search(line)
    chain_match = _LOG_CHAIN_RE.search(line)
    return {
        "ip": ip,
        "port": _as_port(port_match.group(1)) if port_match else None,
        "chain": chain_match.group(1) if chain_match else None,
    }


def parse_probe_output(raw: str) -> list[dict]:
    """Вывод `probe_script()` → записи пробы.

    Проба отдаёт TSV `ip<TAB>port<TAB>chain<TAB>hits` (агрегация уже на узле), но
    разбираем и сырые строки journalctl: прогон скрипта руками или другой формат
    вывода не должны превращаться в пустой результат.
    """
    result: list[dict] = []
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) >= 3:
            ip = _valid_ip(fields[0].strip())
            if ip:
                result.append({
                    "ip": ip,
                    "port": _as_port(fields[1].strip()),
                    "chain": _as_text(fields[2], 32) or None,
                    "hits": _as_hits(fields[3]) if len(fields) > 3 else 0,
                })
                continue
        parsed = parse_log_line(line)
        if parsed:
            result.append(dict(parsed, hits=1))
    return result


_PROBE = r'''set -uo pipefail
HOURS=__HOURS__
MAX_LINES=__MAX_LINES__
if ! command -v journalctl >/dev/null 2>&1; then
    echo "[rkn-scanners] journalctl не найден — читать нечего" >&2
    exit 0
fi
# Логирует доп. компонент шага 4 (цепочка RKN_EXTRA) — префикс RKNSCAN:.
# Агрегируем ЗДЕСЬ: на узле строк лога тысячи, а по SSH должны уехать десятки.
journalctl -k --since "-${HOURS}h" --no-pager 2>/dev/null \
  | grep -a 'RKNSCAN:' \
  | awk '
    {
        chain = ""; src = ""; spt = "";
        if (match($0, /RKNSCAN:[A-Za-z0-9_]+/)) chain = substr($0, RSTART + 8, RLENGTH - 8)
        if (match($0, /SRC=[0-9][0-9.]*/))     src   = substr($0, RSTART + 4, RLENGTH - 4)
        if (match($0, /SPT=[0-9][0-9]*/))      spt   = substr($0, RSTART + 4, RLENGTH - 4)
        if (src == "") next
        hits[src "\t" spt "\t" chain]++
    }
    END {
        for (key in hits) print key "\t" hits[key]
    }
  ' \
  | sort | head -n "$MAX_LINES" || true
exit 0
'''


def probe_script(since_hours: int = DEFAULT_SINCE_HOURS, max_lines: int = 5000) -> str:
    """Read-only проба ноды: TSV `ip, порт, цепочка, число попыток` за последние N часов."""
    hours = _check_hours(since_hours)
    return _PROBE.replace("__HOURS__", str(hours)).replace(
        "__MAX_LINES__", str(max(1, min(int(max_lines), 50_000)))
    )


def _check_hours(since_hours: Any) -> int:
    try:
        hours = int(since_hours)
    except (TypeError, ValueError):
        raise ValueError("since_hours должен быть числом") from None
    if not 1 <= hours <= MAX_SINCE_HOURS:
        raise ValueError(f"since_hours: от 1 до {MAX_SINCE_HOURS}")
    return hours


# ── применение списка на ноде ──────────────────────────────────

_RESTORE = r'''#!/bin/sh
# rkn-scanners-restore — возвращает ipset __SET__ и цепочку __CHAIN__ после
# перезагрузки (состояние набора лежит в __DIR__/ipset.state).
# Идемпотентен: вызывается из crontab @reboot, повторный прогон безвреден.
set -u
RKNSC_SET="__SET__"
RKNSC_CHAIN="__CHAIN__"
RKNSC_STATE="__DIR__/ipset.state"
RKNSC_PRIVATE="__PRIVATE__"
ipset create "$RKNSC_SET" hash:net family inet maxelem __MAXELEM__ -exist >/dev/null 2>&1 || exit 0
if [ -f "$RKNSC_STATE" ]; then
    ipset restore -exist < "$RKNSC_STATE" >/dev/null 2>&1 || true
fi
iptables -N "$RKNSC_CHAIN" >/dev/null 2>&1 || true
iptables -F "$RKNSC_CHAIN" >/dev/null 2>&1 || true
iptables -A "$RKNSC_CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for _w in $RKNSC_PRIVATE; do
    iptables -A "$RKNSC_CHAIN" -s "$_w" -j RETURN
done
iptables -A "$RKNSC_CHAIN" -p tcp --syn -m set --match-set "$RKNSC_SET" src -j DROP
iptables -A "$RKNSC_CHAIN" -j RETURN
while iptables -C INPUT -j "$RKNSC_CHAIN" >/dev/null 2>&1; do
    iptables -D INPUT -j "$RKNSC_CHAIN" >/dev/null 2>&1 || break
done
iptables -I INPUT 1 -j "$RKNSC_CHAIN" >/dev/null 2>&1 || true
echo "[rkn-scanners] $RKNSC_CHAIN восстановлена, записей: $(ipset list "$RKNSC_SET" 2>/dev/null | awk -F': ' '/Number of entries/ {print $2}')"
'''


def restore_script() -> str:
    """Скрипт восстановления состояния — кладётся на ноду рядом с применением."""
    return (_RESTORE
            .replace("__SET__", SET_NAME)
            .replace("__CHAIN__", CHAIN_NAME)
            .replace("__DIR__", STATE_DIR)
            .replace("__PRIVATE__", " ".join(SCANNERS_PRIVATE))
            .replace("__MAXELEM__", str(SET_MAXELEM)))


_APPLY = r'''set -uo pipefail
RKNSC_SET="__SET__"
RKNSC_CHAIN="__CHAIN__"
RKNSC_DIR="__DIR__"
RKNSC_ENTRIES="$RKNSC_DIR/entries.list"
RKNSC_STATE="$RKNSC_DIR/ipset.state"
RKNSC_RESTORE="__RESTORE_PATH__"
RKNSC_PRIVATE="__PRIVATE__"
RKNSC_RESULT=OK
mkdir -p "$RKNSC_DIR"

rknsc_fail() { echo "  ПРОБЛЕМА: $*"; RKNSC_RESULT=CHECK; }

# Центральный список панели: адреса уже провалидированы на нашей стороне (IP/CIDR),
# heredoc с закрытым разделителем — подстановок нет.
cat > "$RKNSC_ENTRIES" << 'RKNSC_ENTRIES_EOF'
__ENTRIES__
RKNSC_ENTRIES_EOF

# 1. Набор: hash:net, maxelem __MAXELEM__. Заливка ОДНИМ `ipset restore` (по одной
# записи было бы 100k форков), состояние — из строк вида `add <set> <cidr> -exist`.
rknsc_set() {
    ipset create "$RKNSC_SET" hash:net family inet maxelem __MAXELEM__ -exist >/dev/null 2>&1 \
        || { rknsc_fail "не создался набор $RKNSC_SET (нет ipset?)"; return 1; }
    # ⚠️ `ipset restore` берёт ИМЯ НАБОРА ИЗ ФАЙЛА — переписываем его на целевой набор
    # (та же ловушка, что ловили в rkn-watcher-extra: иначе заливка уходит не туда).
    awk -v s="$RKNSC_SET" 'NF { print "add " s " " $1 " -exist" }' "$RKNSC_ENTRIES" \
        > "$RKNSC_DIR/restore.list"
    ipset flush "$RKNSC_SET" >/dev/null 2>&1 || true
    if [ -s "$RKNSC_DIR/restore.list" ]; then
        ipset restore -exist < "$RKNSC_DIR/restore.list" \
            || { rknsc_fail "список не залился в $RKNSC_SET"; return 1; }
    fi
    return 0
}

# 2. Цепочка: установленные соединения и приватные сети — RETURN, новые TCP-соединения
# из набора — DROP. Всё остальное RETURN, поэтому чужие правила INPUT (в т.ч.
# deploy-panel-whitelist) продолжают работать: наш переход в INPUT ничего не срезает.
rknsc_rules() {
    iptables -N "$RKNSC_CHAIN" >/dev/null 2>&1 || true
    iptables -F "$RKNSC_CHAIN" >/dev/null 2>&1 || true
    iptables -A "$RKNSC_CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    for _w in $RKNSC_PRIVATE; do
        iptables -A "$RKNSC_CHAIN" -s "$_w" -j RETURN
    done
    iptables -A "$RKNSC_CHAIN" -p tcp --syn -m set --match-set "$RKNSC_SET" src -j DROP
    iptables -A "$RKNSC_CHAIN" -j RETURN
    while iptables -C INPUT -j "$RKNSC_CHAIN" >/dev/null 2>&1; do
        iptables -D INPUT -j "$RKNSC_CHAIN" >/dev/null 2>&1 || break
    done
    iptables -I INPUT 1 -j "$RKNSC_CHAIN" >/dev/null 2>&1 \
        || rknsc_fail "не удалось вставить переход в INPUT"
}

# 3. Сохранение состояния (ipset save) + восстановление после перезагрузки:
# скрипт + строка @reboot в crontab (маркер rkn-scanners-restore — повторный
# прогон не плодит дублей, как в разделе fail2ban).
rknsc_persist() {
    ipset save "$RKNSC_SET" > "$RKNSC_STATE" 2>/dev/null \
        || rknsc_fail "не сохранилось состояние набора"
    cat > "$RKNSC_RESTORE" << 'RKNSC_RESTORE_EOF'
__RESTORE__
RKNSC_RESTORE_EOF
    chmod 0755 "$RKNSC_RESTORE" >/dev/null 2>&1 || true
    (crontab -l 2>/dev/null | grep -v 'rkn-scanners-restore'; \
     echo "@reboot sleep 30 $RKNSC_RESTORE >/dev/null 2>&1 # rkn-scanners-restore") \
        | crontab - 2>/dev/null || true
}

rknsc_set
rknsc_rules
rknsc_persist

RKNSC_COUNT=$(ipset list "$RKNSC_SET" 2>/dev/null | awk -F': ' '/Number of entries/ {print $2}' | tail -1)
RKNSC_RULES=$(iptables -S "$RKNSC_CHAIN" 2>/dev/null | grep -c '^-A' || true)
RKNSC_INPUT=$(iptables -S INPUT 2>/dev/null | grep -c -- "-j $RKNSC_CHAIN" || true)
echo "[rkn-scanners] записей в $RKNSC_SET: ${RKNSC_COUNT:-0}; правил в $RKNSC_CHAIN: ${RKNSC_RULES:-0}; переходов в INPUT: ${RKNSC_INPUT:-0}"
[ "${RKNSC_COUNT:-0}" != "0" ] || echo "  список пуст — набор очищен, блокировать нечего"
if [ "$RKNSC_RESULT" = "OK" ]; then
    echo "RKN_SCANNERS_RESULT=OK"
else
    echo "RKN_SCANNERS_RESULT=CHECK"
fi
echo "RKN_SCANNERS_COUNT=${RKNSC_COUNT:-0}"
'''


def apply_script(entries: list[str]) -> str:
    """Bash-применение центрального списка на ноде. Идемпотентно — безопасно на каждом прогоне.

    Порядок: список → набор (flush + restore) → цепочка `RKN_SCANNERS` в INPUT →
    `ipset save` + скрипт восстановления с @reboot.
    """
    cleaned = _clean_entries(entries)
    # Экранирование не нужно (адреса уже провалидированы), но heredoc всё равно
    # закрытый — на случай, если валидатор когда-нибудь ослабят.
    payload = _APPLY
    for placeholder, value in (
        ("__SET__", SET_NAME),
        ("__CHAIN__", CHAIN_NAME),
        ("__DIR__", STATE_DIR),
        ("__RESTORE_PATH__", RESTORE_PATH),
        ("__PRIVATE__", " ".join(SCANNERS_PRIVATE)),
        ("__MAXELEM__", str(SET_MAXELEM)),
        ("__ENTRIES__", "\n".join(shlex.quote(e) for e in cleaned)),
        ("__RESTORE__", restore_script().rstrip("\n")),
    ):
        payload = payload.replace(placeholder, value)
    return payload


_APPLY_COUNT_RE = re.compile(r"^RKN_SCANNERS_COUNT=(\d+)", re.MULTILINE)
_APPLY_RESULT_RE = re.compile(r"^RKN_SCANNERS_RESULT=(OK|CHECK)", re.MULTILINE)


def parse_apply_output(raw: str) -> Optional[int]:
    """Число записей в наборе по итогу применения (None — маркера в выводе нет)."""
    match = _APPLY_COUNT_RE.search(raw or "")
    return int(match.group(1)) if match else None


def apply_failed(raw: str) -> bool:
    """`RKN_SCANNERS_RESULT=CHECK` в выводе: набор/правила применились не полностью."""
    match = _APPLY_RESULT_RE.search(raw or "")
    return bool(match) and match.group(1) == "CHECK"


# ── работа с нодой по SSH ──────────────────────────────────────

async def collect_node(req: Any, since_hours: int = DEFAULT_SINCE_HOURS) -> list[dict]:
    """Собрать адреса сканеров с ОДНОЙ ноды, не меняя центральный список."""
    hours = _check_hours(since_hours)
    ssh = None
    try:
        ssh = SSHSession(
            req.ip, req.ssh_port, req.ssh_user, **await ssh_auth.resolve(req)
        )
        await ssh.connect()
        output = await ssh.get_output(probe_script(hours))
        return parse_probe_output(output)
    finally:
        if ssh is not None:
            await ssh.close()


async def apply_node(req: Any, entries: list[str]) -> dict:
    """Применить центральный список на ОДНОЙ ноде."""
    cleaned = _clean_entries(entries)          # ValueError на мусоре → ручка отвечает 422
    ssh = None
    try:
        ssh = SSHSession(
            req.ip, req.ssh_port, req.ssh_user, **await ssh_auth.resolve(req)
        )
        await ssh.connect()
        script = apply_script(cleaned)
        output = await ssh.get_script_output(script)
        return {
            "applied": len(cleaned),
            "inSet": parse_apply_output(output),
            "ok": not apply_failed(output),
        }
    finally:
        if ssh is not None:
            await ssh.close()
