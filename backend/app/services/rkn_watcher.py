"""RKN Watcher — единый bash-payload шага 4 пайплайна (+ проба состояния для метрик).

Заменяет прежний шаг TrafficGuard (`DonMatteoVPN/TrafficGuard-auto`): на ноду ставится
апстрим `Balbuto/RKN-Watcher`, закреплённый на КОММИТЕ с проверкой SHA256 трёх
исполняемых файлов, плюс НАШ доп. компонент (`EXTRA_SCRIPT`), который дотягивает списки
подсетей из второго ресурса. Итог: блок-листы берутся с обоих ресурсов, как требует владелец —

  * апстрим RKN Watcher:       CyberOK_Skipa (→ ipset TSPUIPS) + C24Be/AS_Network_List (→ GOVIPS)
  * доп. компонент (наш):      shadow-netlab/traffic-guard-lists → EXTRA_ANTISCAN/EXTRA_GOVNET

Источники берутся по правилу проекта «Mirror First»: сначала ЗЕРКАЛО
(`vitabled/mirror-rkn-watcher`, `vitabled/mirror-traffic-guard-lists`), апстрим — резерв;
архив дополнительно закреплён коммитом и проверкой SHA256.

Всё, что нужно ноде, собирается ЗДЕСЬ (`build_rkn_watcher_script`) и уходит одной строкой в
`SSHSession.run_script` (пайп в `bash -s`) — на нашей стороне никаких файлов не остаётся.

Тесты: `backend/tests/test_rkn_watcher.py`.
"""
from __future__ import annotations

import ipaddress
import re

# ── Закреплённый апстрим (правило NA «Mirror First») ───────────
# Коммит + SHA256: плавающая ветка меняла бы код, который запускается от root на каждой
# ноде, поэтому архив берётся по коммиту и каждый исполняемый файл сверяется с эталоном.
# Несовпадение = НИЧЕГО из RKN Watcher не применяется (см. _VERIFY: exit до конфига).
# Основной источник — ЗЕРКАЛО vitabled/mirror-rkn-watcher, апстрим Balbuto/RKN-Watcher
# остаётся резервом (прямых апстрим-URL в пайплайне быть не должно).
RKN_COMMIT = "558fc11a0792892927785e162359585d51972a6a"
RKN_TARBALL_MIRROR = (
    "https://codeload.github.com/vitabled/mirror-rkn-watcher/tar.gz/" + RKN_COMMIT
)
RKN_TARBALL_UPSTREAM = (
    "https://codeload.github.com/Balbuto/RKN-Watcher/tar.gz/" + RKN_COMMIT
)
RKN_TARBALL_URLS = (RKN_TARBALL_MIRROR, RKN_TARBALL_UPSTREAM)
RKN_SHA256 = {
    "rkn-watcher.sh": "b9f4b471746d6d2739a8f87753e9d81cd5705d3a8b5d72a4367c103522d9a143",
    "config_tool.py": "e1804e57be03ea9ff89b51f98727b308765f2ab83c0d710b8ceb27a0b351513c",
    "geoip_apply.py": "b3d63fc9815fa4933a9d6c653b311b9a79815638b047fc37efc0b3c24fc3718f",
}
RKN_DIR = "/opt/rkn-watcher"
RKN_EXTRA_DIR = "/opt/rkn-watcher-extra"

# Приватные сети + loopback: не блокируются никогда. Адрес панели (backend_ip) идёт ПЕРВЫМ.
RKN_PRIVATE_WHITELIST = ("172.16.0.0/12", "10.0.0.0/8", "192.168.0.0/16", "127.0.0.1/8")

# Наборы двух ресурсов + два набора доп. компонента.
RKN_SETS = ("TSPUIPS", "GOVIPS", "EXTRA_ANTISCAN", "EXTRA_GOVNET")
# Управляющие цепочки: TSPUBLOCK/GOVBLOCK — апстрим, RKN_EXTRA — доп. компонент.
RKN_CHAINS = ("TSPUBLOCK", "GOVBLOCK", "RKN_EXTRA")
# Артефакты ВЫТЕСНЕННОГО инструмента: любой из них на ноде = обмен не доведён до конца.
RKN_LEGACY_PATHS = (
    "/usr/local/bin/rknpidor",
    "/usr/local/bin/traffic-guard",
    "/opt/TrafficGuard-auto",
)


# ── Доп. компонент (эталон: /root/work/rkn_swap/rkn-watcher-extra.sh) ──
# Раскладывается на ноду как /opt/rkn-watcher-extra/rkn-watcher-extra.sh; команды
# update | boot | status | uninstall — их дёргают юниты rkn-watcher-extra.{service,
# boot.service,timer}. Свои правила живут в ОТДЕЛЬНОЙ цепочке RKN_EXTRA: апстрим при
# каждом apply пересоздаёт TSPUBLOCK/GOVBLOCK, подмешивать туда свои правила нельзя.
EXTRA_SCRIPT = r'''#!/usr/bin/env bash
# rkn-watcher-extra — дополнительный набор подсетей для RKN Watcher.
#
# Зачем: RKN Watcher берёт списки из двух источников (CyberOK_Skipa CIDR → TSPUIPS,
# C24Be/AS_Network_List → GOVIPS). Владелец требует объединить оба ресурса, поэтому этот
# компонент дотягивает списки из shadow-netlab/traffic-guard-lists (то, что раньше тянул
# rknpidor/traffic-guard):
#   * antiscanner.list          → ipset EXTRA_ANTISCAN  (сканеры РКН/ТСПУ)
#   * government_networks.list  → ipset EXTRA_GOVNET    (сети госструктур РФ)
# Управляющие правила живут в собственной цепочке RKN_EXTRA (в отличие от TSPUBLOCK/GOVBLOCK
# апстрима, которые он пересоздаёт при каждом apply, — свои правила туда подмешивать нельзя).
#
# Команды: update | boot | status | uninstall   (по умолчанию update)
set -uo pipefail

VERSION="1.0.0"
CONF_DIR="/etc/rkn-watcher-extra"
CONF_FILE="$CONF_DIR/extra.conf"
DATA_DIR="/var/lib/rkn-watcher-extra"
LIST_DIR="$DATA_DIR/lists"
STATE_FILE="$DATA_DIR/ipset.state"
LOG_FILE="/var/log/rkn-watcher-extra/update.log"
CHAIN="RKN_EXTRA"
SET_SCAN="EXTRA_ANTISCAN"
SET_GOV="EXTRA_GOVNET"
# Списки берём с ЗЕРКАЛА (правило Mirror First в NA), апстрим shadow-netlab — резерв:
# источники пробуются по порядку, см. update_list().
LIST_SCAN_URLS=(
    "https://raw.githubusercontent.com/vitabled/mirror-traffic-guard-lists/main/public/antiscanner.list"
    "https://raw.githubusercontent.com/shadow-netlab/traffic-guard-lists/refs/heads/main/public/antiscanner.list"
)
LIST_GOV_URLS=(
    "https://raw.githubusercontent.com/vitabled/mirror-traffic-guard-lists/main/public/government_networks.list"
    "https://raw.githubusercontent.com/shadow-netlab/traffic-guard-lists/refs/heads/main/public/government_networks.list"
)

info() { echo "[extra] $*"; }
log() { mkdir -p "$(dirname "$LOG_FILE")"; echo "$(date -u +%FT%TZ) $*" >>"$LOG_FILE"; }

ensure_dirs() { mkdir -p "$CONF_DIR" "$DATA_DIR" "$LIST_DIR" "$(dirname "$LOG_FILE")"; }

load_conf() {
    ensure_dirs
    if [[ ! -f "$CONF_FILE" ]]; then
        cat >"$CONF_FILE" <<'EOF'
# Белый список источников, которым разрешено всё (никогда не блокируются).
# Через пробел: адреса и CIDR.
WHITELIST="82.22.174.72 144.31.203.24 172.16.0.0/12 10.0.0.0/8 192.168.0.0/16 127.0.0.1/8"
# Логировать сброшенные пакеты (rate-limited). y/n
LOG_DROPS="n"
# Что блокировать со сканерских/госсетей: syn — только новые TCP-соединения (по умолчанию), all — весь TCP.
MODE="syn"
EOF
    fi
    # shellcheck disable=SC1090
    . "$CONF_FILE"
    WHITELIST="${WHITELIST:-}"
    LOG_DROPS="${LOG_DROPS:-n}"
    MODE="${MODE:-syn}"
}

ensure_set() { ipset create "$1" hash:net family inet maxelem 2000000 -exist >/dev/null 2>&1 || true; }

set_count() { ipset list "$1" 2>/dev/null | awk -F': ' '/Number of entries/ {print $2; f=1} END {if (!f) print 0}'; }

fetch() {
    local url=$1 out=$2
    curl -fsSL --connect-timeout 10 --max-time 90 --retry 3 --retry-delay 2 "$url" -o "$out"
}

# Приводим произвольный список (CIDR-строки, ipset-файл, комментарии) к формату `add <set> <cidr> -exist`.
sanitize() {
    local in=$1 out=$2 setname=$3
    python3 - "$in" "$out" "$setname" <<'PY'
import ipaddress, re, sys
src, dst, name = sys.argv[1], sys.argv[2], sys.argv[3]
pat = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?)')
seen, out = set(), []
with open(src, errors='ignore') as fh:
    for line in fh:
        line = line.strip()
        if not line or line.startswith(('#', ';', '//')):
            continue
        m = pat.search(line)
        if not m:
            continue
        try:
            net = ipaddress.ip_network(m.group(1), strict=False)
        except ValueError:
            continue
        if not isinstance(net, ipaddress.IPv4Network):
            continue
        key = str(net)
        if key in seen:
            continue
        seen.add(key)
        out.append('add %s %s -exist' % (name, key))
open(dst, 'w').write('\n'.join(out) + '\n')
print(len(out))
PY
}

# Атомарная замена содержимого набора: временный набор → swap → destroy.
# ⚠️ `ipset restore` берёт ИМЯ НАБОРА ИЗ ФАЙЛА (строки `add <set> <cidr>`), поэтому перед
# заливкой во временный набор имя в файле переписываем. Без этого restore наполняет ЖИВОЙ набор,
# временный остаётся пустым, а swap уносит пустышку в живой набор (наступали: набор обнулялся).
swap_set() {
    local setname=$1 file=$2
    local tmp="${setname}_tmp"
    ipset destroy "$tmp" >/dev/null 2>&1 || true
    ipset create "$tmp" hash:net family inet maxelem 2000000 >/dev/null 2>&1 || return 1
    sed "s/^add [^ ]*/add ${tmp}/" "$file" >"${file}.for_${tmp}"
    if ! ipset restore -exist <"${file}.for_${tmp}" >/dev/null 2>&1; then
        rm -f "${file}.for_${tmp}"
        ipset destroy "$tmp" >/dev/null 2>&1 || true
        return 1
    fi
    rm -f "${file}.for_${tmp}"
    local cnt
    cnt=$(ipset list "$tmp" 2>/dev/null | awk -F': ' '/Number of entries/ {print $2}')
    if [[ -z "${cnt:-}" || "$cnt" == "0" ]]; then
        ipset destroy "$tmp" >/dev/null 2>&1 || true
        return 1
    fi
    ensure_set "$setname"
    if ! ipset swap "$tmp" "$setname" >/dev/null 2>&1; then
        ipset destroy "$tmp" >/dev/null 2>&1 || true
        return 1
    fi
    ipset destroy "$tmp" >/dev/null 2>&1 || true
    echo "$cnt"
}

update_list() {
    local fname=$1 setname=$2
    shift 2
    local raw="$LIST_DIR/${fname}.raw" clean="$LIST_DIR/${fname}.clean" url
    for url in "$@"; do
        if ! fetch "$url" "$raw"; then
            log "$fname: fetch failed ($url) — пробую резерв"
            continue
        fi
        local n
        n=$(sanitize "$raw" "$clean" "$setname")
        if [[ -z "${n:-}" || "$n" == "0" ]]; then
            log "$fname: sanitize produced 0 entries ($url) — пробую резерв"
            continue
        fi
        local swapped
        swapped=$(swap_set "$setname" "$clean") || { log "$fname: swap failed ($url)"; continue; }
        log "$fname: $swapped entries applied from $url"
        echo "$swapped"
        return 0
    done
    log "$fname: все источники недоступны — прежний набор сохранён"
    return 1
}

apply_rules() {
    local wl rule
    iptables -N "$CHAIN" >/dev/null 2>&1 || true
    iptables -F "$CHAIN" >/dev/null 2>&1 || true

    # 1) не трогаем уже установленные соединения
    iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    # 2) белый список (панель, наш хост, приватные сети) — никогда не блокируем
    for wl in $WHITELIST; do
        [[ -z "$wl" ]] && continue
        iptables -A "$CHAIN" -s "$wl" -j RETURN
    done
    # 3) сканерские / госсети: режем входящие соединения
    if [[ "$MODE" == "all" ]]; then
        rule="-m set --match-set %s src -j DROP"
    else
        rule="-p tcp --syn -m set --match-set %s src -j DROP"
    fi
    if [[ "$LOG_DROPS" == "y" ]]; then
        iptables -A "$CHAIN" -p tcp --syn -m set --match-set "$SET_SCAN" src \
            -m limit --limit 5/min --limit-burst 10 -j LOG --log-prefix "RKN-EXTRA: " --log-level 4
    fi
    # shellcheck disable=SC2059
    iptables -A "$CHAIN" $(printf -- "$rule" "$SET_SCAN")
    if [[ "$LOG_DROPS" == "y" ]]; then
        iptables -A "$CHAIN" -p tcp --syn -m set --match-set "$SET_GOV" src \
            -m limit --limit 5/min --limit-burst 10 -j LOG --log-prefix "RKN-GOVEXTRA: " --log-level 4
    fi
    # shellcheck disable=SC2059
    iptables -A "$CHAIN" $(printf -- "$rule" "$SET_GOV")
    iptables -A "$CHAIN" -j RETURN

    # правило-переход в INPUT: сначала убрать прежние, потом вставить на первое место
    while iptables -C INPUT -j "$CHAIN" >/dev/null 2>&1; do
        iptables -D INPUT -j "$CHAIN" >/dev/null 2>&1 || break
    done
    iptables -I INPUT 1 -j "$CHAIN"
    log "rules applied (mode=$MODE, whitelist=[$WHITELIST])"
}

save_state() {
    ensure_dirs
    {
        echo "# rkn-watcher-extra ipset state $(date -u +%FT%TZ)"
        ipset save "$SET_SCAN" 2>/dev/null || true
        ipset save "$SET_GOV" 2>/dev/null || true
    } >"$STATE_FILE"
}

restore_state() {
    [[ -f "$STATE_FILE" ]] || return 1
    ipset restore -exist <"$STATE_FILE" >/dev/null 2>&1
}

cmd_update() {
    load_conf
    ensure_dirs
    ensure_set "$SET_SCAN"
    ensure_set "$SET_GOV"
    local rc=0
    update_list "antiscanner" "$SET_SCAN" "${LIST_SCAN_URLS[@]}" || rc=1
    update_list "government_networks" "$SET_GOV" "${LIST_GOV_URLS[@]}" || rc=1
    apply_rules
    save_state
    return $rc
}

cmd_status() {
    load_conf
    echo "rkn-watcher-extra $VERSION"
    echo "  $SET_SCAN : $(set_count "$SET_SCAN")"
    echo "  $SET_GOV  : $(set_count "$SET_GOV")"
    echo "  цепочка $CHAIN: $(iptables -S "$CHAIN" 2>/dev/null | grep -c '^\-A') правил, переход в INPUT: $(iptables -S INPUT 2>/dev/null | grep -c "\-j $CHAIN")"
    echo "  режим: $MODE | лог сбросов: $LOG_DROPS"
    echo "  белый список: $WHITELIST"
    echo "  таймер: $(systemctl is-active rkn-watcher-extra.timer 2>/dev/null)/$(systemctl is-enabled rkn-watcher-extra.timer 2>/dev/null)"
}

cmd_uninstall() {
    while iptables -C INPUT -j "$CHAIN" >/dev/null 2>&1; do
        iptables -D INPUT -j "$CHAIN" >/dev/null 2>&1 || break
    done
    iptables -F "$CHAIN" >/dev/null 2>&1 || true
    iptables -X "$CHAIN" >/dev/null 2>&1 || true
    ipset destroy "$SET_SCAN" >/dev/null 2>&1 || true
    ipset destroy "$SET_GOV" >/dev/null 2>&1 || true
    systemctl disable --now rkn-watcher-extra.timer >/dev/null 2>&1 || true
    systemctl disable --now rkn-watcher-extra-boot.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/rkn-watcher-extra.timer /etc/systemd/system/rkn-watcher-extra.service \
          /etc/systemd/system/rkn-watcher-extra-boot.service
    systemctl daemon-reload >/dev/null 2>&1 || true
    rm -rf "$DATA_DIR"
    log "uninstalled"
    echo "[extra] удалено"
}

cmd_boot() {
    load_conf
    restore_state || true
    cmd_update || true
}

# Единая точка входа с блокировкой: boot-юнит и таймер/ручной update могут пересечься, а без
# flock два применения сразу оставляют дубли правил в RKN_EXTRA и дублирующий переход в INPUT.
with_lock() {
    ensure_dirs
    (
        flock -w 120 9 || { echo "[extra] не дождался блокировки — выход"; exit 99; }
        "$@"
    ) 9>"$DATA_DIR/extra.lock"
}

case "${1:-update}" in
    update) with_lock cmd_update ;;
    boot) with_lock cmd_boot ;;
    status) cmd_status ;;
    uninstall) cmd_uninstall ;;
    *) echo "usage: $0 {update|boot|status|uninstall}"; exit 2 ;;
esac
'''


# ── Секции payload'а ───────────────────────────────────────────
# Плейсхолдеры __X__ вместо f-строк: в скрипте много bash-скобок и awk-программ, а
# подстановка через .replace() исключает конфликт с `{}` (см. CLAUDE.md §6).

_SETUP = r'''set -uo pipefail
RKN_COMMIT="__RKN_COMMIT__"
# Зеркало первым, апстрим — резерв (Mirror First).
RKN_TARBALL_URLS="__RKN_TARBALL_URLS__"
RKN_EXTRA_DIR="__RKN_EXTRA_DIR__"
BACKEND_IP="__BACKEND_IP__"
WHITELIST="__WHITELIST__"
RKN_RESULT=OK

rkn_fail() { echo "  ПРОБЛЕМА: $*"; RKN_RESULT=CHECK; }

# Адрес панели обязан стоять в INPUT ДО любых DROP-цепочек, иначе следующий заход
# деплоя упирается в собственный блок-лист. Идемпотентно: снимаем прежнее правило и
# вставляем первым.
rkn_whitelist() {
    [ -n "$1" ] || return 0
    while iptables -C INPUT -s "$1" -j ACCEPT -m comment --comment 'deploy-panel-whitelist' >/dev/null 2>&1; do
        iptables -D INPUT -s "$1" -j ACCEPT -m comment --comment 'deploy-panel-whitelist' >/dev/null 2>&1 || break
    done
    iptables -I INPUT 1 -s "$1" -j ACCEPT -m comment --comment 'deploy-panel-whitelist' >/dev/null 2>&1
}
'''

_DEPS = r'''echo "[rkn] Зависимости (iptables ipset curl ca-certificates python3 util-linux)..."
__APT_WAIT__
apt-get update -qq >/dev/null 2>&1 || true
__APT_INSTALL__ >/dev/null 2>&1 || true
for _c in iptables ipset curl python3; do
    command -v "$_c" >/dev/null 2>&1 || { rkn_fail "не установилось: $_c"; echo "RKN_SWAP_RESULT=CHECK"; exit 0; }
done
'''

# Правило-вайтлист панели — до любых DROP-цепочек (требование шага).
_WHITELIST_RULE = r'''echo "[rkn] Вайтлист панели в INPUT (до применения блок-листов)..."
if [ -n "$BACKEND_IP" ]; then
    rkn_whitelist "$BACKEND_IP" && echo "  [whitelist] $BACKEND_IP разрешён (deploy-panel-whitelist)"
else
    echo "  ВНИМАНИЕ: IP панели не определён — правила whitelist не будет"
fi
'''

_LEGACY = r'''echo "[rkn] Снятие легаси (rknpidor / TrafficGuard-auto / antiscan)..."
for _u in antiscan-aggregate.timer antiscan-aggregate.service antiscan-ipset-restore.service antiscan-move-rules.service; do
    systemctl is-active "$_u" >/dev/null 2>&1 && systemctl stop "$_u" >/dev/null 2>&1
    systemctl is-enabled "$_u" >/dev/null 2>&1 && systemctl disable "$_u" >/dev/null 2>&1
done
rm -f /etc/systemd/system/antiscan-* >/dev/null 2>&1 || true
systemctl daemon-reload >/dev/null 2>&1 || true

rm -f /usr/local/bin/antiscan-aggregate-logs.sh /etc/rsyslog.d/10-iptables-scanners.conf \
      /etc/logrotate.d/iptables-scanners /usr/local/bin/rknpidor /usr/local/bin/traffic-guard \
      /opt/trafficguard-manager.sh /opt/trafficguard-manual.list >/dev/null 2>&1 || true
systemctl is-active rsyslog >/dev/null 2>&1 && systemctl restart rsyslog >/dev/null 2>&1
rm -rf /opt/TrafficGuard-auto >/dev/null 2>&1 || true
# /opt/deb_scripts сносим ТОЛЬКО если он принадлежит traffic-guard (там бывают чужие скрипты).
if [ -d /opt/deb_scripts ] && grep -rqiE 'traffic.?guard|rkn|antiscan' /opt/deb_scripts >/dev/null 2>&1; then
    rm -rf /opt/deb_scripts
fi

# Цепочки/наборы старого инструмента + правила na-ctguard прежнего шага NA.
for _ch in SCANNERS SCANNERS-BLOCK na-ctguard; do
    while iptables -C INPUT -j "$_ch" >/dev/null 2>&1; do
        iptables -D INPUT -j "$_ch" >/dev/null 2>&1 || break
    done
    iptables -F "$_ch" >/dev/null 2>&1 || true
    iptables -X "$_ch" >/dev/null 2>&1 || true
done
while iptables -S INPUT 2>/dev/null | grep -q 'na-ctguard'; do
    _rule=$(iptables -S INPUT 2>/dev/null | grep 'na-ctguard' | head -1 | sed 's/^-A INPUT //')
    [ -n "$_rule" ] || break
    iptables -D INPUT $_rule >/dev/null 2>&1 || break
done
for _s in SCANNERS-BLOCK-V4 SCANNERS-BLOCK-V6; do
    ipset destroy "$_s" >/dev/null 2>&1 || true
done

# Сторонние apt-источники speedtest/Ookla ломают `apt-get update` внутри установщика
# апстрима (проверено на noble) — снимаем заранее.
rm -f /etc/apt/sources.list.d/*ookla* /etc/apt/sources.list.d/*speedtest* >/dev/null 2>&1 || true
echo "[rkn] Легаси снято."
'''

# Скачивание + проверка SHA256. Любая проблема → RKN_SWAP_RESULT=CHECK и exit ДО конфига,
# установки и доп. списков: непроверенный код от root на ноде не запускается.
_VERIFY = r'''rkn_verify_sources() {
    local dir="$1" fail=0 pair f want got
    for pair in __SHA_PAIRS__; do
        f="${pair%%:*}"; want="${pair#*:}"
        got=$(sha256sum "$dir/$f" 2>/dev/null | awk '{print $1}')
        if [ "$got" = "$want" ]; then
            echo "  SHA256 ок: $f"
        else
            echo "  SHA256 НЕ СОВПАЛ: $f"
            echo "    ожидали:  $want"
            echo "    получили: ${got:-нет файла}"
            fail=1
        fi
    done
    [ "$fail" = "0" ]
}
echo "[rkn] Апстрим RKN-Watcher, коммит ${RKN_COMMIT:0:8}: скачиваю и сверяю SHA256..."
RKN_WORK=$(mktemp -d /tmp/rkn-swap.XXXXXX)
SRC="$RKN_WORK/src"
mkdir -p "$SRC"
_rkn_got=0
for _url in $RKN_TARBALL_URLS; do
    if curl -fsSL --connect-timeout 15 --max-time 180 "$_url" -o "$RKN_WORK/rknw.tar.gz"; then
        echo "  архив получен: $_url"
        _rkn_got=1
        break
    fi
    echo "  не получилось с $_url — пробую резервный источник"
done
if [ "$_rkn_got" != "1" ]; then
    echo "  ПРОБЛЕМА: архив не скачался ни с зеркала, ни с апстрима"
    echo "RKN_SWAP_RESULT=CHECK"
    exit 0
fi
if ! tar xzf "$RKN_WORK/rknw.tar.gz" -C "$SRC" --strip-components=1; then
    echo "  ПРОБЛЕМА: архив не распаковался"
    echo "RKN_SWAP_RESULT=CHECK"
    exit 0
fi
if ! rkn_verify_sources "$SRC"; then
    echo "  Контрольные суммы не совпали — НИЧЕГО из RKN Watcher не применяю (ни конфига, ни установки, ни доп. списков)."
    echo "RKN_SWAP_RESULT=CHECK"
    exit 0
fi
chmod +x "$SRC/rkn-watcher.sh" "$SRC/installer.sh" >/dev/null 2>&1 || true
'''

_CONFIG = r'''echo "[rkn] Предзаполняю /etc/rkn-watcher (установщик не должен ничего спрашивать)..."
mkdir -p /etc/rkn-watcher
printf 'FILTER_PORTS="all"\nLOG_RST="n"\nAUTO_UPDATE="y"\nENABLE_TSPUBLOCK="y"\nENABLE_GOVIPS="y"\n' >/etc/rkn-watcher/settings.conf
# "enabled": false ОБЯЗАТЕЛЬНО: GeoIP-allowlist по странам отрезал бы клиентов не из
# выбранных стран (панель и владелец в их числе). В ips — панель, whitelist запроса и приватные сети.
python3 - "__WHITELIST__" >/etc/rkn-watcher/whitelist.json <<'RKN_WL_PY'
import json, sys
ips = [x for x in sys.argv[1].split() if x]
print(json.dumps({"enabled": False, "countries": [], "ips": ips, "ports": []}, indent=2))
RKN_WL_PY
printf '{\n  "ips": [],\n  "ports": []\n}\n' >/etc/rkn-watcher/blacklist.json
echo "[rkn] whitelist.json: $(python3 -c 'import json; d=json.load(open("/etc/rkn-watcher/whitelist.json")); print(len(d["ips"]), "ips, enabled", d["enabled"])')"
'''

_INSTALL = r'''echo "[rkn] Установка апстрима (неинтерактивно; зависимости поставлены — RKN_SKIP_DEP_INSTALL=1)..."
if (cd "$SRC" && printf 'y\n' | RKN_SKIP_DEP_INSTALL=1 bash ./rkn-watcher.sh install); then
    /opt/rkn-watcher/rkn-watcher.sh apply --quiet >/dev/null 2>&1 || rkn_fail "apply --quiet вернул ошибку"
    systemctl enable rkn-watcher-update.timer >/dev/null 2>&1 || rkn_fail "не удалось включить rkn-watcher-update.timer"
    echo "  таймер апстрима: $(systemctl is-active rkn-watcher-update.timer 2>/dev/null)/$(systemctl is-enabled rkn-watcher-update.timer 2>/dev/null)"
else
    rkn_fail "rkn-watcher.sh install завершился с ошибкой"
fi
'''

_EXTRA = r'''echo "[rkn] Доп. списки подсетей (shadow-netlab/traffic-guard-lists → EXTRA_ANTISCAN/EXTRA_GOVNET)..."
mkdir -p "$RKN_EXTRA_DIR"
cat >"$RKN_EXTRA_DIR/rkn-watcher-extra.sh" <<'RKN_EXTRA_BODY'
__EXTRA_SCRIPT__
RKN_EXTRA_BODY
chmod 0755 "$RKN_EXTRA_DIR/rkn-watcher-extra.sh"

cat >/etc/systemd/system/rkn-watcher-extra.service <<'RKN_UNIT'
[Unit]
Description=RKN Watcher Extra — дополнительные списки подсетей (обновление)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/rkn-watcher-extra/rkn-watcher-extra.sh update
StandardOutput=journal
StandardError=journal
RKN_UNIT

cat >/etc/systemd/system/rkn-watcher-extra-boot.service <<'RKN_UNIT'
[Unit]
Description=RKN Watcher Extra — восстановление правил при загрузке
After=network-online.target iptables.service
Wants=network-online.target
Before=rkn-watcher-extra.timer

[Service]
Type=oneshot
ExecStart=/opt/rkn-watcher-extra/rkn-watcher-extra.sh boot
StandardOutput=journal
StandardError=journal
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RKN_UNIT

cat >/etc/systemd/system/rkn-watcher-extra.timer <<'RKN_UNIT'
[Unit]
Description=RKN Watcher Extra — таймер обновления списков
Requires=rkn-watcher-extra.service

[Timer]
OnBootSec=3min
OnUnitActiveSec=6h
AccuracySec=1min
Persistent=true
Unit=rkn-watcher-extra.service

[Install]
WantedBy=timers.target
RKN_UNIT

mkdir -p /etc/rkn-watcher-extra
cat >/etc/rkn-watcher-extra/extra.conf <<'RKN_EXTRA_CONF'
WHITELIST="__WHITELIST__"
LOG_DROPS="n"
MODE="syn"
RKN_EXTRA_CONF

systemctl daemon-reload >/dev/null 2>&1 || true
# enable БЕЗ --now: первичный update делаем вручную ниже, иначе boot-юнит и он
# пересекутся и наплодят дубли правил в RKN_EXTRA (наступали на это).
systemctl enable rkn-watcher-extra-boot.service rkn-watcher-extra.timer >/dev/null 2>&1 \
    || rkn_fail "не удалось включить юниты доп. компонента"
echo "[rkn] Первичное обновление доп. списков (вручную)..."
"$RKN_EXTRA_DIR/rkn-watcher-extra.sh" update || rkn_fail "первичное обновление доп. списков не прошло"
systemctl start rkn-watcher-extra.timer >/dev/null 2>&1 || rkn_fail "не удалось запустить rkn-watcher-extra.timer"
netfilter-persistent save >/dev/null 2>&1 || iptables-save >/etc/iptables/rules.v4 2>/dev/null || true
'''

_REPORT = r'''echo ""
echo "[rkn] Проверка по факту:"
_RKN_TSPU=$(ipset list TSPUIPS 2>/dev/null | awk -F': ' '/Number of entries/ {print $2}')
_RKN_GOV=$(ipset list GOVIPS 2>/dev/null | awk -F': ' '/Number of entries/ {print $2}')
_RKN_SCAN=$(ipset list EXTRA_ANTISCAN 2>/dev/null | awk -F': ' '/Number of entries/ {print $2}')
_RKN_GOVNET=$(ipset list EXTRA_GOVNET 2>/dev/null | awk -F': ' '/Number of entries/ {print $2}')
for pair in "TSPUIPS:$_RKN_TSPU" "GOVIPS:$_RKN_GOV" "EXTRA_ANTISCAN:$_RKN_SCAN" "EXTRA_GOVNET:$_RKN_GOVNET"; do
    _name="${pair%%:*}"; _val="${pair#*:}"
    if [ -n "$_val" ] && [ "$_val" != "0" ]; then
        echo "  набор $_name: $_val записей"
    else
        rkn_fail "набор $_name пуст или отсутствует"
    fi
done
for _ch in TSPUBLOCK GOVBLOCK RKN_EXTRA; do
    _rules=$(iptables -S "$_ch" 2>/dev/null | grep -c '^-A')
    if [ "${_rules:-0}" -gt 0 ]; then
        echo "  цепочка $_ch: $_rules правил, переход в INPUT: $(iptables -S INPUT 2>/dev/null | grep -c -- "-j $_ch")"
    else
        rkn_fail "цепочка $_ch пуста"
    fi
done
# Наши адреса не должны попасть под блокировку: сверяем белый список со всеми наборами.
python3 - "__WHITELIST__" <<'RKN_OVERLAP_PY'
import ipaddress, subprocess, sys
wl = []
for token in sys.argv[1].split():
    try:
        wl.append(ipaddress.ip_network(token, strict=False))
    except ValueError:
        pass
bad = []
for name in ('TSPUIPS', 'GOVIPS', 'EXTRA_ANTISCAN', 'EXTRA_GOVNET'):
    out = subprocess.run(['ipset', 'list', name], capture_output=True, text=True).stdout
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(('Name', 'Type', 'Revision', 'Header', 'Size',
                                        'References', 'Members', 'Number')):
            continue
        try:
            net = ipaddress.ip_network(line, strict=False)
        except ValueError:
            continue
        for w in wl:
            if net.overlaps(w):
                bad.append((name, str(net), str(w)))
for name, net, w in bad:
    print('  ВНИМАНИЕ: %s содержит %s — пересекается с белым списком %s' % (name, net, w))
print('  белый список: пересечений со списками блокировок нет' if not bad
      else '  ПРОБЛЕМА: белый список пересекается со списками блокировок')
sys.exit(1 if bad else 0)
RKN_OVERLAP_PY
[ "$?" = "0" ] || rkn_fail "белый список пересекается со списками блокировок"

# Вайтлист — снова первым в INPUT: и апстрим, и доп. компонент вставляют свои переходы перед ним.
rkn_whitelist "$BACKEND_IP"
echo ""
/opt/rkn-watcher/rkn-watcher.sh status 2>/dev/null | sed -n '1,14p' || true
echo ""
echo "[rkn] юниты: rkn-watcher-update.timer=$(systemctl is-active rkn-watcher-update.timer 2>/dev/null)/$(systemctl is-enabled rkn-watcher-update.timer 2>/dev/null) rkn-watcher-extra.timer=$(systemctl is-active rkn-watcher-extra.timer 2>/dev/null)/$(systemctl is-enabled rkn-watcher-extra.timer 2>/dev/null)"
echo "[rkn] легаси: rknpidor=$([ -e /usr/local/bin/rknpidor ] && echo да || echo нет) traffic-guard=$([ -e /usr/local/bin/traffic-guard ] && echo да || echo нет) SCANNERS-BLOCK=$(ipset list -n 2>/dev/null | grep -c 'SCANNERS-BLOCK') antiscan-юниты=$(systemctl list-unit-files --no-pager 2>/dev/null | grep -c '^antiscan')"
if [ "$RKN_RESULT" = "OK" ]; then
    echo "RKN_SWAP_RESULT=OK"
else
    echo "RKN_SWAP_RESULT=CHECK"
fi
echo "RKN_SWAP_COUNTS tspu=${_RKN_TSPU:-0} govips=${_RKN_GOV:-0} antiscan=${_RKN_SCAN:-0} govnet=${_RKN_GOVNET:-0}"
echo "[rkn] Готово: RKN Watcher + доп. списки подсетей."
rm -rf "$RKN_WORK" >/dev/null 2>&1 || true
'''


def _whitelist(backend_ip: str, whitelist_ips: str = "") -> str:
    """Строка белого списка: панель → whitelist из запроса деплоя → приватные сети.

    Токены из ЗАПРОСА валидируются (`ipaddress`) и нормализуются — дальше они уходят в
    root-run bash (whitelist.json апстрима и extra.conf доп. компонента), где мусор или
    чужая инъекция недопустимы; одиночный адрес остаётся без `/32`. Константы приватных
    сетей подставляются КАК ЕСТЬ (они наши и записаны в эталонном виде, напр. `127.0.0.1/8`).
    """
    tokens = [backend_ip or ""] + re.split(r"[,\s]+", whitelist_ips or "")
    out: list[str] = []
    for token in tokens:
        token = (token or "").strip()
        if not token:
            continue
        try:
            net = ipaddress.ip_network(token, strict=False)
        except ValueError:
            continue
        if net.version != 4:
            continue
        text = str(net)
        if text.endswith("/32"):
            text = text[:-3]
        if text not in out:
            out.append(text)
    for token in RKN_PRIVATE_WHITELIST:
        if token not in out:
            out.append(token)
    return " ".join(out)


def _sha_pairs() -> str:
    """`файл:дайджест` через пробел — вход для bash-цикла проверки."""
    return " ".join(f"{name}:{digest}" for name, digest in RKN_SHA256.items())


def build_rkn_watcher_script(backend_ip: str, whitelist_ips: str = "") -> str:
    """Единый bash-payload шага 4 (уходит на ноду пайпом в `bash -s`).

    Порядок: (а) зависимости → (б) правило-вайтлист панели → (в) скачивание + SHA256
    (при несовпадении — `RKN_SWAP_RESULT=CHECK` и выход, НИЧЕГО не применяется: легаси не
    снимается, конфиг не пишется, установщик не запускается) → (г) снятие легаси → (д)
    предзаполнение конфига + установка апстрима → (е) наш доп. компонент со списками
    второго ресурса → (ж) проверка по факту и `RKN_SWAP_RESULT`/`RKN_SWAP_COUNTS`.

    Правило-вайтлист панели ставится ДО любых DROP-цепочек и переставляется в начало
    INPUT в финале (`deploy-panel-whitelist`).
    """
    # Ленивый импорт: pipeline импортирует этот модуль (шаг 4), поэтому общий сниппет
    # apt берём здесь, а не на уровне модуля — иначе получился бы цикл.
    from app.services.pipeline import _APT_WAIT, _apt_install

    whitelist = _whitelist(backend_ip, whitelist_ips)
    payload = "\n".join([
        _SETUP,
        _DEPS,
        _WHITELIST_RULE,
        _VERIFY,
        _LEGACY,
        _CONFIG,
        _INSTALL,
        _EXTRA,
        _REPORT,
    ])
    replacements = {
        "__RKN_COMMIT__": RKN_COMMIT,
        "__RKN_TARBALL_URLS__": " ".join(RKN_TARBALL_URLS),
        "__RKN_EXTRA_DIR__": RKN_EXTRA_DIR,
        "__BACKEND_IP__": (backend_ip or "").strip(),
        "__WHITELIST__": whitelist,
        "__SHA_PAIRS__": _sha_pairs(),
        "__APT_WAIT__": _APT_WAIT.rstrip("\n"),
        "__APT_INSTALL__": _apt_install(
            "iptables", "ipset", "curl", "ca-certificates", "python3", "util-linux"
        ),
        "__EXTRA_SCRIPT__": EXTRA_SCRIPT.rstrip("\n"),
    }
    for placeholder, value in replacements.items():
        payload = payload.replace(placeholder, value)
    return payload


# ── Проба состояния (метрики карточки ноды + проверка шага) ─────
_STATE_PROBE = r'''_rkn_active=0
systemctl is-active rkn-watcher-update.timer 2>/dev/null | grep -qx active && _rkn_active=1
iptables -S TSPUBLOCK >/dev/null 2>&1 && _rkn_active=1
_rkn_entries=0
for _rkn_set in TSPUIPS GOVIPS EXTRA_ANTISCAN EXTRA_GOVNET; do
    _rkn_n=$(ipset list "$_rkn_set" 2>/dev/null | awk -F': ' '/Number of entries/ {print $2}')
    case "${_rkn_n:-}" in ''|*[!0-9]*) ;; *) _rkn_entries=$((_rkn_entries + _rkn_n)) ;; esac
done
_rkn_legacy=0
[ -e /usr/local/bin/rknpidor ] && _rkn_legacy=1
[ -e /usr/local/bin/traffic-guard ] && _rkn_legacy=1
[ -d /opt/TrafficGuard-auto ] && _rkn_legacy=1
ipset list -n 2>/dev/null | grep -q 'SCANNERS-BLOCK' && _rkn_legacy=1
echo "RKN_ACTIVE=$_rkn_active"
echo "RKN_ENTRIES=$_rkn_entries"
echo "RKN_LEGACY=$_rkn_legacy"
'''

_RE_MARKER = re.compile(r"^(RKN_ACTIVE|RKN_ENTRIES|RKN_LEGACY)=(\d+)", re.MULTILINE)


def state_probe() -> str:
    """Read-only проба ноды: активен ли RKN Watcher, сколько записей, остался ли легаси."""
    return _STATE_PROBE


def parse_state(out: str) -> tuple[int, int, int]:
    """`(active, entries, legacy)` из вывода `state_probe()`.

    Неразбираемый вывод (нода без RKN Watcher, обрыв SSH) → нули: карточка должна
    показать пустую метрику, а не упасть.
    """
    values = {m.group(1): int(m.group(2)) for m in _RE_MARKER.finditer(out or "")}
    return (
        1 if values.get("RKN_ACTIVE") else 0,
        values.get("RKN_ENTRIES", 0),
        1 if values.get("RKN_LEGACY") else 0,
    )
