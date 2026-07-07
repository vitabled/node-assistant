"""
8-step deployment pipeline.
Each step is an async function; any exception stops the pipeline immediately.

Non-interactive strategy for third-party repos:
  - Pass DEBIAN_FRONTEND=noninteractive + relevant env vars.
  - Redirect stdin from /dev/null to kill any read() prompts.
  - Where scripts are confirmed interactive (Reshala TUI), bypass them entirely
    and re-implement the exact commands they would have run.
  - Provide a manual fallback for Hysteria2 in case the install script
    doesn't support headless operation.
"""
import asyncio
import base64
import ipaddress
import os
import secrets
from typing import Optional

from app.models.deploy import DeployRequest
from app.services.ssh_manager import SSHSession
from app.services.cloudflare import upsert_a_record
from app.services.task_store import Task, TaskStatus, STEP_LABELS
from app.services.backend_ip import get_backend_ip


# ── Crypto helpers ────────────────────────────────────────────

def _generate_x25519_privkey() -> str:
    """Generate a random X25519 private key, base64-encoded (Xray Reality format)."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    key = X25519PrivateKey.generate()
    return base64.b64encode(key.private_bytes_raw()).decode()


def _generate_short_id() -> str:
    """Generate 8 random bytes as a 16-char hex string (openssl rand -hex 8 equivalent)."""
    return secrets.token_hex(8)


# ──────────────────────────────────────────────────────────────
# Shared bash snippets injected into every script block
# ──────────────────────────────────────────────────────────────

# Waits up to 150 s for dpkg/apt locks, then kills unattended-upgrades.
# Required on freshly-provisioned Ubuntu servers.
_APT_WAIT = """\
_wait_apt() {
    local i=0
    while fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock \
                /var/cache/apt/archives/lock >/dev/null 2>&1; do
        i=$((i+1))
        [ $i -gt 30 ] && { echo "[apt] lock timeout"; break; }
        echo "[apt] lock held, waiting 5s… ($i/30)"
        sleep 5
    done
    pkill -9 -f unattended-upgrade 2>/dev/null || true
    sleep 1
}
export DEBIAN_FRONTEND=noninteractive
_wait_apt
"""


def _apt_install(*pkgs: str) -> str:
    """Return a one-liner apt-get install with lock-safe flags."""
    pkg_list = " ".join(pkgs)
    return (
        f"apt-get install -y -o Dpkg::Options::='--force-confdef' "
        f"-o Dpkg::Options::='--force-confold' {pkg_list}"
    )


def _begin_step(task: Task, index: int, label: Optional[str] = None) -> None:
    task.set_step(index, TaskStatus.RUNNING)
    label = label or STEP_LABELS[index - 1]
    task.add_log(f"\n\x1b[36m{'─' * 56}\x1b[0m")
    task.add_log(f"\x1b[1;36m[{index}/{task.total_steps}] {label}\x1b[0m")
    task.add_log(f"\x1b[36m{'─' * 56}\x1b[0m")


def _skip_component(task: Task, index: int, comp: str, label: Optional[str] = None) -> None:
    """Mark a manageable step as begun but skipped because the component is
    already installed (the "add existing server" flow's skip_components). Mirrors
    the existing install_vnstat=false skip pattern: still advances the progress
    bar so the step shows as done, without running the install work."""
    _begin_step(task, index, label)
    task.add_log(f"\x1b[90m[{comp}] Пропущено — уже установлено (skip_components).\x1b[0m")


def _effective_open_ports(req: "DeployRequest") -> str:
    """User-specified UFW/accelerator ports, plus the HAProxy relay source port
    in haproxy mode (so the host firewall passes transit traffic)."""
    ports = [p.strip() for p in req.open_ports.split(",") if p.strip()]
    if req.mode == "haproxy":
        sp = str(req.haproxy_source_port)
        if sp not in ports:
            ports.append(sp)
    return ",".join(ports)


# ──────────────────────────────────────────────────────────────
# Step 1 – Connect (handled in run_pipeline)
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# Step 2 – TrafficGuard
# ──────────────────────────────────────────────────────────────

def _trafficguard_fallback(backend_ip: str) -> str:
    """Fallback iptables rules when TrafficGuard install.sh is absent/failing.
    Whitelist backend_ip FIRST so the DROP rules never block us."""
    whitelist_rule = (
        f"iptables -I INPUT 1 -s {backend_ip} -j ACCEPT -m comment "
        f"--comment 'deploy-panel-whitelist'"
        if backend_ip else ""
    )
    save_rules = "netfilter-persistent save 2>/dev/null || iptables-save > /etc/iptables/rules.v4"
    return f"""\
# -- TrafficGuard fallback: manual scan-protection rules --
{_APT_WAIT}
{_apt_install("iptables", "iptables-persistent", "netfilter-persistent")}

# Whitelist deploy panel BEFORE any DROP rules
{whitelist_rule}

# Rules are added idempotently (`iptables -C … || iptables -A …`) so a re-run
# (e.g. re-deploying an existing server) never duplicates them.
# Drop NULL packets
iptables -C INPUT -p tcp --tcp-flags ALL NONE -j DROP 2>/dev/null \\
    || iptables -A INPUT -p tcp --tcp-flags ALL NONE -j DROP
# Drop SYN floods
iptables -C INPUT -p tcp ! --syn -m state --state NEW -j DROP 2>/dev/null \\
    || iptables -A INPUT -p tcp ! --syn -m state --state NEW -j DROP
# Drop XMAS packets
iptables -C INPUT -p tcp --tcp-flags ALL ALL -j DROP 2>/dev/null \\
    || iptables -A INPUT -p tcp --tcp-flags ALL ALL -j DROP
# Rate-limit new SSH connections (10/min)
iptables -C INPUT -p tcp --dport 22 -m state --state NEW \\
    -m recent --set --name SSH_SCAN 2>/dev/null \\
    || iptables -A INPUT -p tcp --dport 22 -m state --state NEW \\
        -m recent --set --name SSH_SCAN
iptables -C INPUT -p tcp --dport 22 -m state --state NEW \\
    -m recent --update --seconds 60 --hitcount 10 --name SSH_SCAN -j DROP 2>/dev/null \\
    || iptables -A INPUT -p tcp --dport 22 -m state --state NEW \\
        -m recent --update --seconds 60 --hitcount 10 --name SSH_SCAN -j DROP

# Persist rules
{save_rules}
echo "[TrafficGuard fallback] iptables rules applied."
"""


# ──────────────────────────────────────────────────────────────
# Step 3 – Node Accelerator (OS performance tuning)
# Non-interactive reimplementation of github.com/jestivald/node-accelerator
# ──────────────────────────────────────────────────────────────

async def step_node_accelerator(
    ssh: SSHSession,
    task: Task,
    req: "DeployRequest",
) -> None:
    """Step 3 — run jestivald's node-accelerator (base tuning + protect.sh).

    Variable mapping (per spec):
      SSH_PORT   = new_ssh_port if change_ssh_port else current_ssh_port
      TCP_PORTS  = UDP_PORTS = open_ports (the user's comma-separated list)
      NODE_PORT  = remnanode_port
      REMNAWAVE_URL / REMNAWAVE_TOKEN = panel URL / API token from settings
    """
    _begin_step(task, 3)

    if not req.optimize:
        task.add_log("\x1b[90m[skip] Оптимизация ОС отключена.\x1b[0m")
        return

    from app.services import storage as _storage
    from app.models.settings import AppSettings

    cfg = AppSettings(**_storage.load_settings()).remnawave
    panel_url = cfg.panel_url or ""
    api_token = cfg.api_token or ""

    ssh_port  = req.new_ssh_port if req.change_ssh_port else req.current_ssh_port
    all_ports = _effective_open_ports(req)  # includes HAProxy source port in haproxy mode
    node_port = req.remnanode_port

    task.add_log(
        f"\x1b[90m[accelerator] SSH_PORT={ssh_port} PORTS={all_ports} "
        f"NODE_PORT={node_port} CDN={'on' if req.behind_cdn else 'off'}\x1b[0m"
    )

    # ── 1. Base optimization ──────────────────────────────────
    base_script = f"""\
{_APT_WAIT}
echo "[accelerator] Базовая оптимизация (install.sh all)..."
curl -fsSL https://raw.githubusercontent.com/jestivald/node-accelerator/main/install.sh | bash -s all
"""
    await ssh.run_script(base_script, task, timeout=300)

    # ── 2. Protection + Remnawave integration via protect.sh ──
    protect_script = f"""\
{_APT_WAIT}
{_apt_install("git")}
cd /opt
rm -rf node-accelerator
git clone https://github.com/jestivald/node-accelerator.git
cd node-accelerator
echo "[accelerator] Запуск protect.sh..."
SSH_PORT={ssh_port} TCP_PORTS="{all_ports}" UDP_PORTS="{all_ports}" NODE_PORT={node_port} \\
    REMNAWAVE_URL="{panel_url}" REMNAWAVE_TOKEN="{api_token}" \\
    REMNAWAVE_NONINTERACTIVE=1 \\
    bash scripts/protect.sh
"""
    await ssh.run_script(protect_script, task, timeout=300)

    # ── 3. Conditional CDN protection (na-ctguard) ────────────
    if req.behind_cdn:
        task.add_log("\x1b[36m[accelerator] Нода за CDN — настройка na-ctguard...\x1b[0m")
        cdn_script = """\
cd /opt/node-accelerator
echo "[ctguard] Включение na-ctguard..."
ENABLE_CTGUARD=1 REMNAWAVE_NONINTERACTIVE=1 bash scripts/protect.sh
echo "[ctguard] Журнал na-ctguard:"
journalctl -t na-ctguard --no-pager 2>/dev/null | tail -50 || true
echo "[ctguard] Включение enforce-режима..."
ENABLE_CTGUARD=1 NA_CTG_ENFORCE=1 REMNAWAVE_NONINTERACTIVE=1 bash scripts/protect.sh
systemctl stop na-fw-safety.timer 2>/dev/null || true
echo "[ctguard] na-ctguard настроен."
"""
        await ssh.run_script(cdn_script, task, timeout=300)

    task.add_log("\x1b[32m[accelerator] Node Accelerator завершён.\x1b[0m")


async def step_traffic_guard(ssh: SSHSession, task: Task, backend_ip: str) -> None:
    _begin_step(task, 4)

    await ssh.run(_apt_install("git", "curl"), task, check=False)

    clone_cmd = (
        "git clone --depth 1 https://github.com/DonMatteoVPN/TrafficGuard-auto "
        "/opt/TrafficGuard-auto 2>/dev/null "
        "|| git -C /opt/TrafficGuard-auto pull --ff-only 2>/dev/null || true"
    )
    await ssh.run(clone_cmd, task, check=False)

    probe = await ssh.get_output(
        "ls /opt/TrafficGuard-auto/*.sh 2>/dev/null | head -3 || echo 'NOT_FOUND'"
    )
    if "NOT_FOUND" in probe or not probe:
        task.add_log("\x1b[33m[TrafficGuard] No install script found — using fallback rules.\x1b[0m")
        await ssh.run_script(_trafficguard_fallback(backend_ip), task, check=False)
        return

    install_script = f"""\
{_APT_WAIT}
cd /opt/TrafficGuard-auto
AUTO=1 NONINTERACTIVE=1 DEBIAN_FRONTEND=noninteractive \
    bash install.sh </dev/null 2>&1 || true
"""
    rc = await ssh.run_script(install_script, task, check=False)
    if rc != 0:
        task.add_log("\x1b[33m[TrafficGuard] install.sh returned non-zero — applying fallback rules.\x1b[0m")
        await ssh.run_script(_trafficguard_fallback(backend_ip), task, check=False)
    elif backend_ip:
        # install.sh succeeded — still need to whitelist backend IP in iptables
        whitelist_script = f"""\
iptables -I INPUT 1 -s {backend_ip} -j ACCEPT -m comment \\
    --comment 'deploy-panel-whitelist' 2>/dev/null || true
netfilter-persistent save 2>/dev/null \\
    || iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
echo "[TrafficGuard] backend IP {backend_ip} whitelisted."
"""
        await ssh.run_script(whitelist_script, task, check=False)

    task.add_log("\x1b[32m[TrafficGuard] done.\x1b[0m")


# ──────────────────────────────────────────────────────────────
# Step 5 – Test toolkit (iperf3 + speedtest CLI + xray-core)
# Shared installer from test_tools.py (Ф1 wave1) so the node can run the
# speed-test probes (Ф2). Optional + NON-FATAL: gated on install_test_tools,
# and any install failure logs a warning without failing the deploy.
# Runs in BOTH modes (remnanode and haproxy — before the mode branch).
# ──────────────────────────────────────────────────────────────

async def step_test_tools(ssh: SSHSession, task: Task, req: "DeployRequest") -> None:
    _begin_step(task, 5)

    if not req.install_test_tools:
        task.add_log(
            "\x1b[90m[test-tools] Пропущено по настройке (install_test_tools=false).\x1b[0m"
        )
        return

    from app.services.test_tools import test_tools_install_script

    try:
        await ssh.run_script(test_tools_install_script(), task, check=False, timeout=300)
        task.add_log("\x1b[32m[test-tools] Инструменты тестирования установлены.\x1b[0m")
    except Exception as exc:
        task.add_log(
            f"\x1b[33m[ПРЕДУПРЕЖДЕНИЕ] Установка тест-инструментов не удалась: {exc} — "
            f"деплой продолжается (инструменты опциональны).\x1b[0m"
        )


# ──────────────────────────────────────────────────────────────
# Step 3 – System optimisation (Reshala logic, non-interactive)
# We implement the Reshala menu options directly rather than calling
# the interactive TUI script.
# ──────────────────────────────────────────────────────────────

_KERNEL_HARDENING = """\
cat > /etc/sysctl.d/99-hardening.conf << 'EOF'
# === Reshala: Kernel Hardening ===

# Reverse path filtering
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# SYN flood protection
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 4096

# No ICMP redirects
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0

# Log martians
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1

# No IP source routing
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0

# Ignore bogus ICMP responses
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1

# Time-wait assassination protection
net.ipv4.tcp_rfc1337 = 1

# === Reshala: Network Performance ===
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_rmem = 4096 87380 134217728
net.ipv4.tcp_wmem = 4096 65536 134217728
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_mtu_probing = 1
EOF

sysctl --system 2>&1 | grep -v "^sysctl:" || true
echo "[kernel] hardening applied."
"""

def _parse_ip_list(s: str) -> list[str]:
    """Normalize a free-form whitelist string into a deduped list of valid
    IPv4 addresses / CIDR networks. Split on comma / whitespace / newline;
    silently drop anything that isn't a valid IPv4 address or IPv4 network.
    Order-preserving dedup."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in s.replace(",", " ").split():
        tok = raw.strip()
        if not tok:
            continue
        try:
            if "/" in tok:
                net = ipaddress.ip_network(tok, strict=False)
                if net.version != 4:
                    continue
                norm = str(net)
            else:
                addr = ipaddress.ip_address(tok)
                if addr.version != 4:
                    continue
                norm = str(addr)
        except ValueError:
            continue
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _fail2ban_setup(backend_ip: str, whitelist: Optional[list[str]] = None,
                    ssh_maxretry: int = 4) -> str:
    """Generate fail2ban setup script. `backend_ip` + `whitelist` IPs/CIDRs go
    into ignoreip; `ssh_maxretry` sets the sshd jail's maxretry (doubled to 8
    when the deploy allows SSH from everywhere, so broad exposure doesn't cause
    over-eager bans)."""
    trusted = [ip for ip in [backend_ip, *(whitelist or [])] if ip]
    trusted_suffix = (" " + " ".join(trusted)) if trusted else ""
    ignoreip_line = f"ignoreip  = 127.0.0.1/8 ::1{trusted_suffix}"
    return f"""\
{_APT_WAIT}
{_apt_install("fail2ban", "nginx")}

mkdir -p /var/log/nginx
touch /var/log/nginx/error.log /var/log/nginx/access.log
chmod 640 /var/log/nginx/*.log 2>/dev/null || true

cat > /etc/fail2ban/filter.d/portscan.conf << 'EOF'
[Definition]
failregex = .*\\[iptables DROP\\].*SRC=<HOST>.*
            .*kernel.*\\bSRC=<HOST>\\b.*\\bDPT=\\b
ignoreregex =
EOF

cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime   = 7200
findtime  = 600
maxretry  = 5
banaction = iptables-multiport
{ignoreip_line}

[sshd]
enabled  = true
port     = ssh
filter   = sshd
logpath  = %(syslog_authpriv)s
backend  = systemd
maxretry = {ssh_maxretry}
bantime  = 86400

[nginx-http-auth]
enabled  = true
filter   = nginx-http-auth
logpath  = /var/log/nginx/error.log
maxretry = 5

[nginx-botsearch]
enabled  = true
filter   = nginx-botsearch
logpath  = /var/log/nginx/access.log
maxretry = 2
bantime  = 86400

[nginx-badbots]
enabled  = true
filter   = nginx-badbots
logpath  = /var/log/nginx/access.log
maxretry = 1
bantime  = 86400

[portscan]
enabled  = true
filter   = portscan
logpath  = /var/log/kern.log
maxretry = 1
bantime  = 86400
EOF

systemctl enable fail2ban
systemctl restart fail2ban
echo "[fail2ban] status:"
systemctl is-active fail2ban
"""


def _zram_swap_script(ram_mb: int) -> str:
    zram_mb = int(ram_mb * 0.4)
    return f"""\
# === Reshala: Hybrid memory — ZRAM {zram_mb} MB + Swap 4 GB ===

# ── ZRAM ─────────────────────────────────────────────────────
modprobe zram 2>/dev/null || true

if [ -d /sys/class/zram-control ]; then
    DEV_NUM=$(cat /sys/class/zram-control/hot_add 2>/dev/null || echo 0)
    ZRAM_DEV="/dev/zram${{DEV_NUM}}"
else
    ZRAM_DEV="/dev/zram0"
fi

if [ -b "$ZRAM_DEV" ]; then
    # Reset in case it was already configured
    swapoff "$ZRAM_DEV" 2>/dev/null || true
    echo 1 > /sys/block/$(basename $ZRAM_DEV)/reset 2>/dev/null || true
    echo {zram_mb}M > /sys/block/$(basename $ZRAM_DEV)/disksize
    mkswap "$ZRAM_DEV"
    swapon -p 100 "$ZRAM_DEV"
    echo "[zram] $ZRAM_DEV active ({zram_mb} MB, priority 100)"
else
    echo "[zram] device not available, skipping"
fi

# Persist ZRAM via systemd service (survives reboots)
cat > /etc/systemd/system/zram-swap.service << 'EOF'
[Unit]
Description=ZRAM swap ({zram_mb} MB)
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c "modprobe zram; \
    DEV=$(cat /sys/class/zram-control/hot_add 2>/dev/null || echo 0); \
    echo {zram_mb}M > /sys/block/zram$DEV/disksize; \
    mkswap /dev/zram$DEV; \
    swapon -p 100 /dev/zram$DEV"
ExecStop=/bin/bash -c "swapoff /dev/zram0 2>/dev/null || true"

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable zram-swap.service

# ── Swap file 4 GB ────────────────────────────────────────────
if [ ! -f /swapfile ]; then
    fallocate -l 4G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=4096
    chmod 600 /swapfile
    mkswap /swapfile
fi
swapoff /swapfile 2>/dev/null || true
swapon -p 10 /swapfile
grep -q '/swapfile' /etc/fstab \
    || echo '/swapfile none swap sw,pri=10 0 0' >> /etc/fstab

echo ""
echo "[memory] Current swap:"
swapon --show
free -h
"""


def _firewall_extra_script(req: "DeployRequest", whitelist: list[str]) -> str:
    """UFW allow rules for the deploy whitelist + optional open-SSH-to-all.
    Each whitelisted IP/CIDR is trusted for all ports (`ufw allow from … to any`,
    matching the backend-IP whitelist pattern). `allow_ssh_all` explicitly opens
    the effective SSH port to any source (covers the change_ssh_port=off case
    where the dual-port script didn't run). Guarded on ufw being present; never
    force-enables UFW (that could lock out a box whose ports aren't allowed yet)."""
    ssh_port = req.new_ssh_port if req.change_ssh_port else req.current_ssh_port
    rules = [
        f"ufw allow from {ip} to any comment 'deploy-whitelist' 2>/dev/null || true"
        for ip in whitelist
    ]
    # allow_ssh_all opens the SSH port to any source. Only add it here when the
    # port is NOT already opened by the dual-port script: `_ssh_dualport_config_
    # _script` already does `ufw allow {new_port}/tcp` (all sources) when
    # change_ssh_port is on. Adding it again would create a duplicate rule that a
    # Scenario-Б rollback's single `ufw delete` wouldn't fully remove (leaving the
    # new port open after rollback). So emit it only for the no-port-change case.
    if req.allow_ssh_all and not req.change_ssh_port:
        rules.append(
            f"ufw allow {ssh_port}/tcp comment 'SSH open (all)' 2>/dev/null || true"
        )
    if not rules:
        return "echo '[firewall] нет доп. правил (whitelist пуст, SSH-all выкл).'"
    body = "\n    ".join(rules)
    return f"""\
if command -v ufw >/dev/null 2>&1; then
    {body}
    ufw status 2>/dev/null | grep -E 'deploy-whitelist|SSH open' || true
fi
echo "[firewall] доп. правила применены (whitelist={len(whitelist)}, ssh_all={str(req.allow_ssh_all).lower()})."
"""


async def step_system_optimize(
    ssh: SSHSession, task: Task, backend_ip: str, req: "DeployRequest"
) -> None:
    _begin_step(task, 6)

    whitelist = _parse_ip_list(req.whitelist_ips)
    if whitelist:
        task.add_log(f"\x1b[36m[whitelist] Доверенные IP/CIDR: {', '.join(whitelist)}\x1b[0m")

    await ssh.run_script(_KERNEL_HARDENING, task)
    await ssh.run_script(
        _fail2ban_setup(
            backend_ip, whitelist, ssh_maxretry=8 if req.allow_ssh_all else 4
        ),
        task,
    )
    await ssh.run_script(_firewall_extra_script(req, whitelist), task, check=False)

    # Use get_output() — reads stdout without adding noise to task logs
    raw = await ssh.get_output("grep MemTotal /proc/meminfo | awk '{print $2}'")
    try:
        ram_kb = int(raw)
    except ValueError:
        ram_kb = 1024 * 1024  # fallback: assume 1 GB

    ram_mb = ram_kb // 1024
    task.add_log(f"\x1b[32m[memory] RAM: {ram_mb} MB → ZRAM: {int(ram_mb * 0.4)} MB + Swap: 4 GB\x1b[0m")

    await ssh.run_script(_zram_swap_script(ram_mb), task)

    # ── Dual-port SSH config + cold reboot (Session #1) ───────
    # Strategy: make sshd listen on BOTH the old and new port, validate, then
    # REBOOT to prove the config survives an OS cold start. We never rely on a
    # single live session surviving a port swap — instead Step 6 reconnects after
    # the reboot and decides success/rollback based on which port answers.
    # Runs AFTER fail2ban is installed so its port can be set to "old,new".
    if req.change_ssh_port:
        # check=True → invalid sshd config (sshd -t) aborts BEFORE the reboot.
        await ssh.run_script(
            _ssh_dualport_config_script(req.current_ssh_port, req.new_ssh_port), task
        )
        task.add_log(
            f"\x1b[33m[ssh-dualport] Перезагрузка сервера для холодной проверки "
            f"портов {req.current_ssh_port} + {req.new_ssh_port}...\x1b[0m"
        )
        # Issue the reboot non-interactively and detached, then close Session #1.
        # The connection will drop as the OS goes down; Step 6 polls for it.
        await ssh.run_script(_reboot_script(), task, check=False)
        try:
            await ssh.close()
        except Exception:
            pass
    else:
        task.add_log("\x1b[90m[ssh-dualport] Смена порта SSH отключена — пропуск.\x1b[0m")


# ── Bash builders for the SSH-port lifecycle ──────────────────

def _whitelist_script(backend_ip: str) -> str:
    """Whitelist the deploy panel's IP in UFW + iptables (idempotent)."""
    return f"""\
echo "[whitelist] Добавление {backend_ip} в deploy-panel-whitelist..."
if command -v ufw >/dev/null 2>&1; then
    ufw allow from {backend_ip} to any comment 'deploy-panel-whitelist' 2>/dev/null || true
    ufw status | grep -F '{backend_ip}' || true
fi
iptables -C INPUT -s {backend_ip} -j ACCEPT 2>/dev/null \\
    || iptables -I INPUT 1 -s {backend_ip} -j ACCEPT \\
        -m comment --comment 'deploy-panel-whitelist' 2>/dev/null || true
netfilter-persistent save 2>/dev/null \\
    || iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
echo "[whitelist] Готово."
"""


def _ssh_dualport_config_script(old_port: int, new_port: int) -> str:
    """Step-5 dual-port config (Session #1): open BOTH ports in UFW, make sshd
    listen on both, validate, set fail2ban to protect both. `exit 1` on an
    invalid config so check=True aborts BEFORE the reboot."""
    return f"""\
echo "[ssh-dualport] Настройка двух SSH-портов: {old_port} + {new_port}"

# 1. UFW: allow BOTH ports (the old one stays open), then enable
if command -v ufw >/dev/null 2>&1; then
    ufw allow {old_port}/tcp comment 'SSH Old Port' 2>/dev/null || true
    ufw allow {new_port}/tcp comment 'SSH New Port' 2>/dev/null || true
    ufw --force enable 2>/dev/null || true
fi

# 2. sshd_config: listen on BOTH ports. Remove all Port lines, add the two we want.
sed -i -E '/^[[:space:]]*#?Port[[:space:]]+[0-9]+/d' /etc/ssh/sshd_config
echo "Port {old_port}" >> /etc/ssh/sshd_config
echo "Port {new_port}" >> /etc/ssh/sshd_config
# cloud-init drop-ins can pin a single Port — strip them
find /etc/ssh/sshd_config.d -maxdepth 1 -name '*.conf' 2>/dev/null \\
    -exec sed -i -E '/^[[:space:]]*#?Port[[:space:]]+[0-9]+/d' {{}} \\;
echo "[ssh-dualport] Port directives:"
grep -E '^Port' /etc/ssh/sshd_config || true

# 3. Validate — abort (exit 1) BEFORE the reboot if the config is invalid
if ! sshd -t 2>&1; then
    echo "[ssh-dualport] ОШИБКА: sshd -t — конфиг невалиден. Перезагрузка отменена."
    exit 1
fi
echo "[ssh-dualport] sshd -t OK"

# 4. fail2ban: protect BOTH ports (comma syntax)
if [ -f /etc/fail2ban/jail.local ]; then
    sed -i -E 's/^([[:space:]]*port[[:space:]]*=[[:space:]]*).*/\\1{old_port},{new_port}/' /etc/fail2ban/jail.local
    systemctl restart fail2ban 2>/dev/null || true
    echo "[ssh-dualport] fail2ban защищает порты {old_port},{new_port}"
fi
"""


def _reboot_script() -> str:
    """Reboot non-interactively and detached so the run returns before the
    connection drops. Prefers systemd --no-block; falls back to a nohup'd reboot."""
    return """\
echo "[ssh-dualport] Отправка команды перезагрузки..."
if command -v systemctl >/dev/null 2>&1; then
    systemctl reboot --no-block 2>/dev/null || nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &
else
    nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &
fi
echo "[ssh-dualport] Сервер уходит в перезагрузку."
"""


def _ssh_cleanup_newport_script(old_port: int, new_port: int) -> str:
    """Scenario А finalize (over the NEW-port session): keep only the new port —
    drop the old Port line, set fail2ban to the new port, delete the old UFW rule,
    restart sshd (this session is on the new port → established conn survives)."""
    return f"""\
echo "[ssh-cleanup] Финализация: оставляю только порт {new_port}, убираю {old_port}"
# 1. sshd_config: drop the old Port line, keep {new_port}
sed -i -E '/^[[:space:]]*Port[[:space:]]+{old_port}([[:space:]]|$)/d' /etc/ssh/sshd_config
grep -E '^Port' /etc/ssh/sshd_config || true
# 2. fail2ban: single port {new_port}
if [ -f /etc/fail2ban/jail.local ]; then
    sed -i -E 's/^([[:space:]]*port[[:space:]]*=[[:space:]]*).*/\\1{new_port}/' /etc/fail2ban/jail.local
    systemctl restart fail2ban 2>/dev/null || true
fi
# 3. UFW: remove the old SSH port
command -v ufw >/dev/null 2>&1 && ufw delete allow {old_port}/tcp 2>/dev/null || true
# 4. restart sshd (established connection on {new_port} survives)
systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null \\
    || service ssh restart 2>/dev/null || true
echo "[ssh-cleanup] Готово — SSH только на порту {new_port}."
"""


def _ssh_rollback_to_old_script(old_port: int, new_port: int) -> str:
    """Scenario Б rollback (over the OLD-port session): drop the new Port line,
    revert fail2ban to the old port, delete the new UFW rule, restart sshd."""
    return f"""\
echo "[ssh-rollback] Откат: возвращаю SSH только на порт {old_port}"
# 1. sshd_config: drop the new Port line, keep {old_port}
sed -i -E '/^[[:space:]]*Port[[:space:]]+{new_port}([[:space:]]|$)/d' /etc/ssh/sshd_config
grep -E '^Port' /etc/ssh/sshd_config || true
# 2. fail2ban: single port {old_port}
if [ -f /etc/fail2ban/jail.local ]; then
    sed -i -E 's/^([[:space:]]*port[[:space:]]*=[[:space:]]*).*/\\1{old_port}/' /etc/fail2ban/jail.local
    systemctl restart fail2ban 2>/dev/null || true
fi
# 3. UFW: remove the failed new SSH port
command -v ufw >/dev/null 2>&1 && ufw delete allow {new_port}/tcp 2>/dev/null || true
# 4. restart sshd (established connection on {old_port} survives)
systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null \\
    || service ssh restart 2>/dev/null || true
echo "[ssh-rollback] Откат завершён — SSH только на порту {old_port}."
"""


# ── Connectivity helpers (used by the post-reboot verification) ──

async def _tcp_reachable(host: str, port: int, timeout: float = 5.0) -> bool:
    """True if a TCP connection to host:port can be opened within `timeout`."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _try_ssh_connect(
    req: "DeployRequest", port: int, timeout: int = 12
) -> Optional[SSHSession]:
    """Attempt a full SSH connection on `port`; return the session or None."""
    sess = SSHSession(req.ip, port, req.ssh_user, req.ssh_password)
    try:
        await sess.connect(timeout=timeout)
        return sess
    except Exception:
        try:
            await sess.close()
        except Exception:
            pass
        return None


# ──────────────────────────────────────────────────────────────
# Step 6 – Post-reboot dual-port verification + cleanup / rollback
#
# After Step 5 rebooted the box, Session #1 is gone. We poll for the server to
# come back, then branch on which port answers:
#   • Scenario А — new port works → finalize (keep new, drop old), continue.
#   • Scenario Б — new port dead, old port alive → rollback, abort (FAILED).
#   • Scenario В — neither port answers in 90s → critical lockout, abort.
# Returns the live SSH session to use for Steps 10–14.
# ──────────────────────────────────────────────────────────────

async def step_ssh_dualport_verify(
    ssh: SSHSession,
    task: Task,
    req: "DeployRequest",
    backend_ip: str,
) -> SSHSession:
    # This former single step is now presented as THREE progress steps:
    #   7 «Перезагрузка»            — poll for the box to come back online
    #   8 «Проверка нового порта SSH» — SSH-connect on the new port (rollback/lockout here)
    #   9 «Удаление старого порта SSH» — cleanup: drop the old port
    _begin_step(task, 7)

    async def _whitelist(sess: SSHSession) -> None:
        if not backend_ip:
            task.add_log(
                "\x1b[33m[whitelist] IP бэкенда не определён — вайтлист пропущен.\x1b[0m"
            )
            return
        await sess.run_script(_whitelist_script(backend_ip), task, check=False)
        task.add_log(
            f"\x1b[32m[whitelist] IP бэкенда {backend_ip} в белом списке.\x1b[0m"
        )

    # No port change → no reboot happened; keep Session #1, just whitelist.
    # Advance through steps 7/8/9 so the progress bar still completes them.
    if not req.change_ssh_port:
        task.add_log("\x1b[90m[ssh-dualport] Смена порта отключена — перезагрузки не было.\x1b[0m")
        _begin_step(task, 8)
        task.add_log("\x1b[90m[ssh-dualport] Проверка порта не требуется.\x1b[0m")
        _begin_step(task, 9)
        await _whitelist(ssh)
        return ssh

    new_port = req.new_ssh_port
    old_port = req.current_ssh_port
    loop = asyncio.get_running_loop()

    # ── Step 7: poll for the server to come back online after the reboot ──
    task.add_log("\x1b[36m[ssh-dualport] Ожидание перезагрузки сервера...\x1b[0m")
    await asyncio.sleep(20)  # let the OS actually begin shutting down

    deadline = loop.time() + 90
    back_online = False
    while loop.time() < deadline:
        if await _tcp_reachable(req.ip, new_port) or await _tcp_reachable(req.ip, old_port):
            back_online = True
            break
        await asyncio.sleep(5)

    if not back_online:
        # ── SCENARIO В — total lockout ──
        raise RuntimeError(
            "Критическая ошибка сетевой доступности: после перезагрузки сервер не "
            f"ответил ни по одному порту SSH ({new_port}/{old_port}) за 90 секунд."
        )

    task.add_log("\x1b[32m[ssh-dualport] Сервер снова в сети — проверяю порты...\x1b[0m")
    await asyncio.sleep(3)  # give sshd a moment to finish binding

    # ── Step 8: verify the new port accepts SSH ──
    _begin_step(task, 8)
    session_new = await _try_ssh_connect(req, new_port, timeout=12)
    if session_new is not None:
        old_reachable = await _tcp_reachable(req.ip, old_port)
        task.add_log(
            f"\x1b[32m[ssh-dualport] УСПЕХ: подключение по новому порту {new_port} "
            f"установлено (старый порт {old_port}: "
            f"{'доступен' if old_reachable else 'закрыт'}).\x1b[0m"
        )
        # ── Step 9: keep only the new port everywhere ──
        _begin_step(task, 9)
        await session_new.run_script(
            _ssh_cleanup_newport_script(old_port, new_port), task, check=False
        )
        await _whitelist(session_new)
        try:
            await ssh.close()  # the pre-reboot session is already dead
        except Exception:
            pass
        return session_new

    # ── SCENARIO Б — new port down, old port still alive → rollback ──
    session_old = await _try_ssh_connect(req, old_port, timeout=12)
    if session_old is not None:
        task.add_log(
            f"\x1b[1;33m[ssh-dualport] Новый порт {new_port} не поднялся после "
            f"перезагрузки, но порт {old_port} доступен — выполняю откат...\x1b[0m"
        )
        await session_old.run_script(
            _ssh_rollback_to_old_script(old_port, new_port), task, check=False
        )
        try:
            await session_old.close()
        except Exception:
            pass
        raise RuntimeError(
            "Смена порта не удалась после перезагрузки сервера. Система автоматически "
            f"откатана на порт {old_port}, доступ сохранён."
        )

    # ── SCENARIO В (edge) — a port answered TCP but neither accepts SSH ──
    raise RuntimeError(
        "Критическая ошибка сетевой доступности: после перезагрузки не удалось "
        f"установить SSH ни по новому ({new_port}), ни по старому ({old_port}) порту."
    )


# ──────────────────────────────────────────────────────────────
# Step 5 – Cloudflare DNS + Wildcard SSL via acme.sh (DNS-01)
# ──────────────────────────────────────────────────────────────

def ssl_needs_cf_dns(cert_provider: str) -> bool:
    """Cloudflare (DNS-01) is the only provider we manage DNS for; the HTTP-01
    providers require the FQDN to already resolve to the server."""
    return cert_provider == "cloudflare"


def build_ssl_script(domain: str, email: str, cf_api_key: str, cert_provider: str) -> str:
    """The acme.sh install + per-provider issue + install-cert bash script,
    shared by the deploy pipeline (`step_ssl`) and the SSL-management endpoint
    (`api/certs.py`). Issues per-FQDN (never a root wildcard). Provider branch:
    cloudflare=DNS-01, letsencrypt=HTTP-01 standalone, zerossl=HTTP-01 + email-EAB."""
    if cert_provider == "cloudflare":
        provider_prep = f'export CF_Token="{cf_api_key}"'
        issue_flags = "--dns dns_cf --server letsencrypt"
    elif cert_provider == "zerossl":
        provider_prep = (
            "fuser -k 80/tcp 2>/dev/null || true\n"
            f'/root/.acme.sh/acme.sh --register-account --server zerossl -m "{email}" || true'
        )
        issue_flags = "--standalone --server zerossl"
    else:  # letsencrypt (HTTP-01 standalone)
        provider_prep = "fuser -k 80/tcp 2>/dev/null || true"
        issue_flags = "--standalone --server letsencrypt"

    # cloudflare + letsencrypt share the Let's Encrypt CA (DNS-01 vs HTTP-01);
    # only zerossl is a different CA. Skip re-issue only on a CA match.
    ca_marker = "zerossl" if cert_provider == "zerossl" else "letsencrypt"

    return f"""\
{_APT_WAIT}
{_apt_install("curl", "socat", "cron")}

# ── Install acme.sh if absent ─────────────────────────────────
if [ ! -f /root/.acme.sh/acme.sh ]; then
    curl -fsSL https://get.acme.sh | sh -s email={email} --force
    # Reload shell env
    export PATH="$PATH:/root/.acme.sh"
fi

# ── Issue per-NODE cert for the exact FQDN ({cert_provider}) ──
# IMPORTANT: we issue for the node's own FQDN ({domain}) — NOT a root wildcard.
# A wildcard (root + *.root) is the SAME "set of identifiers" for every node, so
# deploying ~5 nodes under one root domain hits the CA's limit of 5 certs per
# identical identifier set / 168h (429 rateLimited). Each node FQDN is a unique
# set, so per-FQDN issuance never collides and works on a fresh server.
{provider_prep}

# Decide issuance on the ACTUAL ECC cert files, not on `acme.sh --list`
# (a stale/partial registry entry would otherwise skip issuance, then
# --install-cert --ecc fails). Force a fresh issue only when files are absent.
ECC_DIR="/root/.acme.sh/{domain}_ecc"
# CA-match guard: skip re-issue only if the on-disk cert was issued by the SAME
# CA we're asking for now ({ca_marker}). A provider switch across CAs (→/from
# zerossl) leaves a mismatched conf → we fall through to --issue, never silently
# reuse the wrong CA's cert.
CA_OK=1
if [ -f "$ECC_DIR/{domain}.conf" ] && ! grep -qi '{ca_marker}' "$ECC_DIR/{domain}.conf"; then
    CA_OK=0
    echo "[acme] На диске сертификат другого CA — переиздаю под {cert_provider}."
fi
if [ -s "$ECC_DIR/{domain}.cer" ] && [ -s "$ECC_DIR/{domain}.key" ] && [ "$CA_OK" = "1" ]; then
    echo "[acme] ECC cert files present for {domain} ({ca_marker}) — пропускаю выпуск."
else
    echo "[acme] ECC cert files missing/mismatched — выпускаю сертификат для {domain} (--force)."
    /root/.acme.sh/acme.sh --issue \\
        {issue_flags} \\
        -d "{domain}" \\
        --keylength ec-256 \\
        --force
    echo "[acme] Certificate issued."
fi

# ── Install cert to well-known paths ─────────────────────────
mkdir -p /etc/ssl/certs /etc/ssl/private

/root/.acme.sh/acme.sh --install-cert -d "{domain}" \\
    --ecc \\
    --cert-file      /etc/ssl/certs/{domain}.crt \\
    --key-file       /etc/ssl/private/{domain}.key \\
    --fullchain-file /etc/ssl/certs/{domain}_fullchain.pem \\
    --reloadcmd      "systemctl reload nginx 2>/dev/null || true"

# Fail loudly if the cert/key are missing or empty (otherwise nginx would later
# fail to start with an empty cert mounted into the container).
if [ ! -s /etc/ssl/certs/{domain}_fullchain.pem ] || [ ! -s /etc/ssl/private/{domain}.key ]; then
    echo "[acme] ОШИБКА: сертификат или ключ не установлены (отсутствуют/пустые)."
    exit 1
fi

chmod 600 /etc/ssl/private/{domain}.key
echo "[acme] Cert installed:"
ls -lh /etc/ssl/certs/{domain}* /etc/ssl/private/{domain}.key
"""


async def step_ssl(
    ssh: SSHSession,
    task: Task,
    domain: str,
    email: str,
    cf_api_key: str,
    server_ip: str,
    cert_provider: str = "cloudflare",
) -> None:
    _begin_step(task, 10)

    # Only Cloudflare (DNS-01) manages DNS for us via the CF API; HTTP-01
    # providers validate over port 80, so the FQDN must already resolve here.
    if ssl_needs_cf_dns(cert_provider):
        task.add_log("Updating Cloudflare DNS A record...")
        await upsert_a_record(cf_api_key, domain, server_ip)
        task.add_log(f"\x1b[32m[CF] A record: {domain} → {server_ip}\x1b[0m")
    else:
        task.add_log(
            f"\x1b[33m[SSL] Провайдер '{cert_provider}' использует HTTP-01 (порт 80). "
            f"Убедитесь, что {domain} уже указывает на {server_ip}.\x1b[0m"
        )

    await ssh.run_script(build_ssl_script(domain, email, cf_api_key, cert_provider), task, timeout=360)


# ──────────────────────────────────────────────────────────────
# Step 6 – Remnanode
# Generates docker-compose.yml + nginx.conf from templates and runs
# them via Docker Compose. No third-party installer script.
# ──────────────────────────────────────────────────────────────

# docker-compose template. Placeholders ($domaincert/$nodeport/$token) are
# substituted in Python before upload. Contains no native shell/nginx vars.
_REMNANODE_COMPOSE_TPL = """\
x-common: &common
  ulimits:
    nofile:
      soft: 1048576
      hard: 1048576
  restart: always

x-logging: &logging
  logging:
    driver: json-file
    options:
      max-size: 100m
      max-file: 5

services:
  remnawave-nginx:
    image: nginx:1.28
    container_name: remnawave-nginx
    hostname: remnawave-nginx
    <<: [*common, *logging]
    network_mode: host
    volumes:
      - '/opt/remnanode/certbot/certs:/etc/letsencrypt:ro'
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - /etc/letsencrypt/live/$domaincert/fullchain.pem:/etc/nginx/ssl/$domaincert/fullchain.pem:ro
      - /etc/letsencrypt/live/$domaincert/privkey.pem:/etc/nginx/ssl/$domaincert/privkey.pem:ro
      - /dev/shm:/dev/shm:rw
      - /var/www/html:/var/www/html:ro
    command: sh -c 'rm -f /dev/shm/nginx.sock && exec nginx -g "daemon off;"'

  remnanode:
    image: remnawave/node:latest
    container_name: remnanode
    hostname: remnanode
    <<: [*common, *logging]
    network_mode: host
    cap_add:
      - NET_ADMIN
    environment:
      - NODE_PORT=$nodeport
      - SECRET_KEY=$token
    volumes:
      - '/opt/remnanode/certbot/certs:/etc/letsencrypt:ro'
      - /dev/shm:/dev/shm:rw
"""

# XHTTP location block — included only when $path is provided.
# $proxy_add_x_forwarded_for is a NATIVE nginx var: it must NOT be substituted.
_NGINX_LOCATION_TPL = """\

    location $path {
        client_max_body_size 0;
        grpc_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        client_body_timeout 5m;
        grpc_read_timeout 315;
        grpc_send_timeout 5m;
        grpc_pass unix:/dev/shm/xrxh.socket;
    }
"""

# nginx.conf template. $http_upgrade / $connection_upgrade are native nginx
# vars and survive substitution because we only replace $domain/$domaincert/$path.
# __LOCATION_BLOCK__ is filled with the rendered location block or "".
_NGINX_TPL = """\
server_names_hash_bucket_size 64;

map $http_upgrade $connection_upgrade {
    default upgrade;
    ""      close;
}

ssl_protocols TLSv1.2 TLSv1.3;
ssl_ecdh_curve X25519:prime256v1:secp384r1;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384:DHE-RSA-CHACHA20-POLY1305;
ssl_prefer_server_ciphers on;
ssl_session_timeout 1d;
ssl_session_cache shared:MozSSL:10m;
ssl_session_tickets off;

server {
    server_name $domain;
    listen unix:/dev/shm/nginx.sock ssl proxy_protocol;
    http2 on;

    ssl_certificate "/etc/nginx/ssl/$domaincert/fullchain.pem";
    ssl_certificate_key "/etc/nginx/ssl/$domaincert/privkey.pem";
    ssl_trusted_certificate "/etc/nginx/ssl/$domaincert/fullchain.pem";
__LOCATION_BLOCK__
    root /var/www/html;
    index index.html;
    add_header X-Robots-Tag "noindex, nofollow, noarchive, nosnippet, noimageindex" always;
}

server {
    listen unix:/dev/shm/nginx.sock ssl proxy_protocol default_server;
    server_name _;
    add_header X-Robots-Tag "noindex, nofollow, noarchive, nosnippet, noimageindex" always;
    ssl_reject_handshake on;
    return 444;
}
"""


def _render_remnanode_files(
    *, domain: str, domaincert: str, node_port: int, token: str, xhttp_path: str
) -> tuple[str, str]:
    """Render (compose, nginx_conf). Substitutes ONLY our system variables.

    $domaincert is replaced before $domain because $domain is a prefix of
    $domaincert — replacing $domain first would corrupt $domaincert.
    Native nginx vars ($http_upgrade, $proxy_add_x_forwarded_for, …) never
    match our keys, so they pass through untouched.
    """
    path = (xhttp_path or "").strip()

    # nginx: insert location block only when a path was given
    location = _NGINX_LOCATION_TPL if path else ""
    nginx_conf = _NGINX_TPL.replace("__LOCATION_BLOCK__", location)
    for key, val in (("$domaincert", domaincert), ("$domain", domain), ("$path", path)):
        nginx_conf = nginx_conf.replace(key, val)

    compose = _REMNANODE_COMPOSE_TPL
    for key, val in (
        ("$domaincert", domaincert),
        ("$nodeport", str(node_port)),
        ("$token", token),
    ):
        compose = compose.replace(key, val)

    return compose, nginx_conf


async def step_remnanode(
    ssh: SSHSession,
    task: Task,
    remnanode_token: str,
    domain: str,
    *,
    node_port: int = 2222,
    xhttp_path: str = "",
) -> None:
    _begin_step(task, 11)

    # The cert is issued per-FQDN (see step_ssl), so the cert identity IS the
    # node domain — not the root domain. All cert paths below key off the FQDN.
    domaincert = domain
    compose, nginx_conf = _render_remnanode_files(
        domain=domain,
        domaincert=domaincert,
        node_port=node_port,
        token=remnanode_token,
        xhttp_path=xhttp_path,
    )
    task.add_log(
        f"\x1b[90m[remnanode] domain={domain} domaincert={domaincert} "
        f"node_port={node_port} xhttp_path={xhttp_path or '—'}\x1b[0m"
    )

    # ── Ensure Docker is present ──────────────────────────────
    docker_setup = f"""\
{_APT_WAIT}
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi
docker --version
"""
    await ssh.run_script(docker_setup, task, timeout=180)

    # ── Write config files via quoted heredocs ────────────────
    # Single-quoted heredoc markers prevent the remote shell from expanding
    # the native nginx '$' vars. Build with concatenation (not an f-string)
    # because nginx.conf contains literal '{' / '}'.
    write_script = (
        _APT_WAIT
        + "mkdir -p /opt/remnanode/certbot/certs\n"
        + "cat > /opt/remnanode/docker-compose.yml << 'COMPOSE_EOF'\n"
        + compose
        + "COMPOSE_EOF\n"
        + "cat > /opt/remnanode/nginx.conf << 'NGINX_EOF'\n"
        + nginx_conf
        + "NGINX_EOF\n"
        + 'echo "[remnanode] docker-compose.yml + nginx.conf written to /opt/remnanode"\n'
    )
    await ssh.run_script(write_script, task)

    # ── Bridge: nginx expects certs under /etc/letsencrypt/live/<domaincert>/ ──
    # Our SSL step (acme.sh) installs them under /etc/ssl/. Symlink so the
    # nginx container finds fullchain.pem / privkey.pem at the expected path.
    cert_bridge = f"""\
mkdir -p /etc/letsencrypt/live/{domaincert}
if [ ! -e /etc/letsencrypt/live/{domaincert}/fullchain.pem ] \\
        && [ -f /etc/ssl/certs/{domaincert}_fullchain.pem ]; then
    ln -sf /etc/ssl/certs/{domaincert}_fullchain.pem /etc/letsencrypt/live/{domaincert}/fullchain.pem
    ln -sf /etc/ssl/private/{domaincert}.key        /etc/letsencrypt/live/{domaincert}/privkey.pem
    echo "[remnanode] linked acme.sh certs into /etc/letsencrypt/live/{domaincert}/"
fi
"""
    await ssh.run_script(cert_bridge, task, check=False)

    # ── Build & start containers ──────────────────────────────
    deploy_script = """\
cd /opt/remnanode
docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
docker compose up -d 2>&1 || docker-compose up -d 2>&1

echo "[remnanode] running containers:"
docker ps --filter "name=remnanode" --filter "name=remnawave-nginx" \
    --format "table {{.Names}}\\t{{.Status}}"
"""
    await ssh.run_script(deploy_script, task, timeout=300)

    # ── Verify remnanode container is actually running — fail hard if not ──
    running = await ssh.get_output(
        "docker ps --filter 'name=remnanode' --filter 'status=running' "
        "--format '{{.Names}}' 2>/dev/null | head -1"
    )
    if "remnanode" not in (running or ""):
        raise RuntimeError(
            "Контейнер remnanode не запущен после docker compose up. "
            "Проверьте: docker ps -a && docker logs remnanode && docker logs remnawave-nginx"
        )
    task.add_log("\x1b[32m[remnanode] Контейнеры запущены.\x1b[0m")


# ──────────────────────────────────────────────────────────────
# Step 7 – WARP Native (optional)
# Uses wgcf (WireGuard-based) instead of warp-cli daemon.
# Key safety: Table = off in warp.conf prevents wg-quick from
# injecting a default route, so SSH stays alive.
# Based on: https://github.com/distillium/warp-native
# ──────────────────────────────────────────────────────────────

async def step_warp(ssh: SSHSession, task: Task) -> None:
    _begin_step(task, 13)

    warp_script = f"""\
{_APT_WAIT}
{_apt_install("wireguard", "curl")}

# ── Download wgcf binary ──────────────────────────────────
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  WGCF_ARCH="amd64" ;;
    aarch64) WGCF_ARCH="arm64" ;;
    armv7l)  WGCF_ARCH="armv7" ;;
    *)       WGCF_ARCH="amd64" ;;
esac

if ! command -v wgcf &>/dev/null; then
    WGCF_VER=$(curl -fsSL "https://api.github.com/repos/ViRb3/wgcf/releases/latest" \\
        | grep '"tag_name"' | cut -d'"' -f4)
    WGCF_VER="${{WGCF_VER:-v2.2.26}}"
    WGCF_VER_NUM="${{WGCF_VER#v}}"
    WGCF_URL="https://github.com/ViRb3/wgcf/releases/download/${{WGCF_VER}}/wgcf_${{WGCF_VER_NUM}}_linux_${{WGCF_ARCH}}"
    echo "[WARP] Downloading wgcf ${{WGCF_VER}} (${{WGCF_ARCH}})..."
    curl -fsSL "${{WGCF_URL}}" -o /usr/local/bin/wgcf
    chmod +x /usr/local/bin/wgcf
fi
wgcf --version

# ── Register WARP account ─────────────────────────────────
mkdir -p /etc/wireguard
cd /etc/wireguard

if [ ! -f wgcf-account.toml ]; then
    echo "[WARP] Registering with Cloudflare WARP..."
    yes | timeout 60 wgcf register 2>&1
else
    echo "[WARP] Account already registered."
fi

# ── Generate WireGuard profile ────────────────────────────
yes | wgcf generate 2>&1 || true
cp -f wgcf-profile.conf warp.conf

# ── Patch warp.conf — critical SSH-preservation steps ─────
#
# Table = off  → wg-quick does NOT inject a default route, so the
#                existing SSH route is never overwritten. Without this
#                the tunnel would intercept all traffic and kill the session.
# Remove DNS   → prevents the tunnel from hijacking DNS resolution.
# IPv4-only    → strip IPv6 from AllowedIPs for a simpler, stable setup.
# PersistentKeepalive → keeps the NAT pinhole open through Cloudflare.

sed -i '/^DNS/d' warp.conf
sed -i '/^\\[Interface\\]/a Table = off' warp.conf
sed -i '/^\\[Peer\\]/a PersistentKeepalive = 25' warp.conf
sed -i 's|AllowedIPs = .*|AllowedIPs = 0.0.0.0/0|' warp.conf

echo "[WARP] Patched warp.conf:"
cat warp.conf

# ── Bring up tunnel ───────────────────────────────────────
wg-quick down warp 2>/dev/null || true
wg-quick up warp 2>&1

systemctl enable wg-quick@warp 2>/dev/null || true

sleep 2
echo "[WARP] Tunnel status:"
wg show warp 2>&1 || true
echo "[WARP] done."
"""
    await ssh.run_script(warp_script, task, timeout=180)


# ──────────────────────────────────────────────────────────────
# Step 10 – Certbot SSL (standalone, port 80) + remnanode wiring + cron renew
# Deploys an isolated certbot container, issues a cert for the node FQDN via the
# ACME HTTP-01 challenge, mounts the certs into remnanode (read-only), restarts
# the stack, and schedules monthly auto-renewal in root's crontab.
# Runs with check=True: a certbot failure aborts the whole pipeline → FAILED.
# ──────────────────────────────────────────────────────────────

async def step_certbot_ssl(
    ssh: SSHSession,
    task: Task,
    domain: str,
    email: str,
) -> None:
    _begin_step(task, 14)

    # ── 1. Provision the isolated certbot environment + issue the cert ──
    # The certbot/docker-compose.yml has no template vars (plain YAML). $domain
    # and $email are substituted into the `docker run` invocation below.
    # check=True → if `docker run certbot` exits non-zero, run_script raises and
    # the pipeline stops with the node marked FAILED (cert error shown in UI).
    issue_script = f"""\
{_APT_WAIT}
mkdir -p /opt/certbot
cat > /opt/certbot/docker-compose.yml << 'CERTBOT_EOF'
services:
  certbot:
    container_name: certbot
    image: certbot/certbot
    network_mode: host
    volumes:
      - ./certs:/etc/letsencrypt
CERTBOT_EOF
echo "[certbot] compose записан в /opt/certbot/docker-compose.yml"

# Open + free port 80 for the standalone ACME HTTP-01 challenge
if command -v ufw > /dev/null; then ufw allow 80/tcp; fi
fuser -k 80/tcp > /dev/null 2>&1 || true

echo "[certbot] Выпуск сертификата для {domain} (standalone, порт 80)..."
docker run --rm \\
  -v /opt/certbot/certs:/etc/letsencrypt \\
  -v /opt/certbot/var-lib-letsencrypt:/var/lib/letsencrypt \\
  --network host \\
  certbot/certbot certonly --standalone \\
  --non-interactive --agree-tos \\
  --email "{email}" \\
  -d "{domain}"
echo "[certbot] Сертификат выпущен."
"""
    await ssh.run_script(issue_script, task, timeout=300)

    # ── 2. Wire certs into remnanode + restart + schedule renewal ──
    # NOT an f-string: the awk program contains literal { } braces, and no form
    # variables are needed here (all paths are static).
    #
    # The remnanode service already has a placeholder mount on /etc/letsencrypt;
    # Docker forbids two mounts on the same target, so we REPLACE that line
    # (inside the remnanode block only) with the real certbot certs path rather
    # than appending a duplicate. Idempotent — re-runs converge to the same line.
    wire_script = """\
echo "[certbot] Подключение сертификатов к remnanode..."
DC=/opt/remnanode/docker-compose.yml
if [ -f "$DC" ]; then
    awk '
      /^[[:space:]]*remnanode:[[:space:]]*$/ { inr=1 }
      inr && /:\\/etc\\/letsencrypt:ro/ && !patched {
          sub(/-[[:space:]].*:\\/etc\\/letsencrypt:ro.*/, "- /opt/certbot/certs:/etc/letsencrypt:ro")
          patched=1
      }
      { print }
    ' "$DC" > "$DC.tmp" && mv "$DC.tmp" "$DC"

    grep -q "/opt/certbot/certs:/etc/letsencrypt:ro" "$DC" || {
        echo "[certbot] ОШИБКА: не удалось внедрить mount сертификатов в $DC"; exit 1; }
    echo "[certbot] remnanode volumes (letsencrypt):"
    grep -n "letsencrypt" "$DC" || true

    cd /opt/remnanode/ && docker compose down && docker compose up -d
    echo "[certbot] Стек remnanode перезапущен."
else
    echo "[certbot] ПРЕДУПРЕЖДЕНИЕ: $DC не найден — подключение к remnanode пропущено."
fi

# ── Monthly auto-renew via root crontab (28th), de-duplicated ──
( crontab -l 2>/dev/null | grep -v "certbot renew" ; \\
  echo "0 0 28 * * cd /opt/certbot && docker compose run --rm certbot renew" ) | crontab -
echo "[certbot] Cron автообновления установлен (28-е число каждого месяца)."
"""
    await ssh.run_script(wire_script, task, timeout=180)
    task.add_log("\x1b[32m[certbot] SSL развёрнут, автообновление настроено.\x1b[0m")


# ──────────────────────────────────────────────────────────────
# Step 11 – SNI masking-site uniquization
# Fills /var/www/html with a randomly-chosen, per-node uniquified decoy site
# so every node has a different page fingerprint (anti-DPI / anti-censorship).
# Served read-only by the remnawave-nginx container from /var/www/html.
# Runs with check=True: any failure (curl/unzip/…) → pipeline FAILED → retry UI.
# ──────────────────────────────────────────────────────────────

async def step_sni_masking(ssh: SSHSession, task: Task) -> None:
    _begin_step(task, 12)

    # `set -euo pipefail` makes the whole step abort (non-zero exit) on the
    # first failing command — exactly the "critical step" behaviour required:
    # a failed download/unzip propagates up and marks the node FAILED.
    #
    # Obfuscation strategy: ADDITIVE uniquization only. We inject random
    # markers (meta tag + HTML comment in <head>, a hidden marker before
    # </body>, a trailing comment in every CSS file) using openssl-generated
    # hex tokens. This changes the page/asset hash on every node WITHOUT
    # rewriting existing tags/selectors — so the markup never breaks.
    masking_script = """\
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# 1. Ensure unzip is present
apt-get install unzip -y

# 2. Download + unpack the templates repo (overwrite if present)
cd /opt/
curl -fL https://github.com/distillium/sni-templates/archive/refs/heads/main.zip -o sni-templates.zip
unzip -o sni-templates.zip
cd sni-templates-main/

# 3. Pick a random template directory via $RANDOM
mapfile -t DIRS < <(find . -maxdepth 1 -mindepth 1 -type d | sort)
COUNT=${#DIRS[@]}
[ "$COUNT" -gt 0 ] || { echo "[sni] В архиве не найдено ни одного шаблона"; exit 1; }
SEL="${DIRS[$((RANDOM % COUNT))]}"
cd "$SEL"
echo "[sni] Выбран случайный шаблон: $SEL (из $COUNT)"

# 4. Deep uniquization — generate unique hex tokens and inject them
H1=$(openssl rand -hex 4)
H2=$(openssl rand -hex 4)
H3=$(openssl rand -hex 4)
H4=$(openssl rand -hex 4)
echo "[sni] Уникальные токены: $H1 $H2 $H3 $H4"

# 4a. Into every HTML file: add a random meta tag + comment to <head>, and a
#     hidden, invisible marker div before </body>. Additive → never breaks DOM.
#     '#' delimiter + 'I' flag (case-insensitive </HEAD>). Only the first match
#     per file is enough to change the hash; we replace all to be safe.
find . -type f -name '*.html' -print0 | while IFS= read -r -d '' f; do
    sed -i "s#</head>#<meta name=\\"x-${H1}\\" content=\\"${H2}\\">\\n<!-- ${H3} -->\\n</head>#I" "$f"
    sed -i "s#</body>#<div id=\\"m-${H4}\\" hidden aria-hidden=\\"true\\"></div>\\n</body>#I" "$f"
done

# 4b. Into every CSS file: append a unique comment (changes the asset hash
#     without affecting any selector/rule).
find . -type f -name '*.css' -print0 | while IFS= read -r -d '' f; do
    printf '\\n/* %s%s%s */\\n' "$H1" "$H3" "$H4" >> "$f"
done

# 5. Deploy into the nginx web root (mounted read-only into the container)
mkdir -p /var/www/html
rm -rf /var/www/html/*
cp -r ./* /var/www/html/
echo "[sni] Маскировочный сайт развёрнут в /var/www/html"

# 6. Clean up temporary files in /opt
cd /
rm -rf /opt/sni-templates.zip /opt/sni-templates-main
echo "[sni] Временные файлы удалены."
"""
    await ssh.run_script(masking_script, task, timeout=180)
    task.add_log("\x1b[32m[sni] Уникализация маскировочного сайта завершена.\x1b[0m")


# ──────────────────────────────────────────────────────────────
# HAProxy relay mode (alternative to Steps 10–14)
# Installs HAProxy and configures a plain TCP relay from the source port to a
# destination IP:port. No Remnawave/DNS/SSL/Xray involvement.
# ──────────────────────────────────────────────────────────────

def _haproxy_cfg(req: "DeployRequest") -> str:
    """Render /etc/haproxy/haproxy.cfg from the form's HAProxy fields.
    Plain text with no shell/regex metachars — safe inside a quoted heredoc."""
    return f"""global
    log /dev/log local0
    log /dev/log local1 notice
    chroot /var/lib/haproxy
    user haproxy
    group haproxy
    daemon

    # Оптимизация для работы с большим количеством соединений
    maxconn {req.haproxy_maxconn}

defaults
    log     {req.haproxy_log}
    mode    {req.haproxy_mode}
    timeout connect {req.haproxy_timeout_connect}
    timeout client  {req.haproxy_timeout_client}
    timeout server  {req.haproxy_timeout_server}
    timeout tunnel  {req.haproxy_timeout_tunnel}

frontend con_in
    # Порт, который будет слушать HAProxy на реле-сервере
    bind *:{req.haproxy_source_port}
    mode tcp
    option tcplog
    default_backend con_out

backend con_out
    mode tcp
    # Пересылка трафика на целевой сервер
    server con_destination {req.haproxy_dest_ip}:{req.haproxy_dest_port} check
"""


async def step_haproxy_deploy(ssh: SSHSession, task: Task, req: "DeployRequest") -> None:
    """HAProxy relay deploy — reuses step slot 10 (Steps 11–14 are skipped)."""
    _begin_step(task, 10, "Установка HAProxy-реле")

    task.add_log(
        f"\x1b[90m[haproxy] {req.haproxy_source_port} → "
        f"{req.haproxy_dest_ip}:{req.haproxy_dest_port} "
        f"(mode={req.haproxy_mode}, maxconn={req.haproxy_maxconn})\x1b[0m"
    )

    # 1. Update + install haproxy + enable; ensure the relay port is open; backup cfg
    setup_script = f"""\
{_APT_WAIT}
apt-get update -y
apt-get upgrade -y -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold'
{_apt_install("haproxy")}
systemctl enable haproxy 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then
    ufw allow {req.haproxy_source_port}/tcp comment 'HAProxy relay' 2>/dev/null || true
fi
# Back up the original config once
if [ -f /etc/haproxy/haproxy.cfg ] && [ ! -f /etc/haproxy/haproxy.cfg.bak ]; then
    cp /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.bak
    echo "[haproxy] Оригинальный конфиг сохранён в haproxy.cfg.bak"
fi
"""
    await ssh.run_script(setup_script, task, timeout=600)

    # 2. Write config, validate syntax (abort on error), restart, show status.
    #    Built by concatenation (not an f-string) because the config is embedded
    #    literally in a quoted heredoc.
    apply_script = (
        "cat > /etc/haproxy/haproxy.cfg << 'HAPROXY_EOF'\n"
        + _haproxy_cfg(req)
        + "HAPROXY_EOF\n"
        + 'echo "[haproxy] Конфиг записан. Проверка синтаксиса..."\n'
        + "if ! haproxy -c -f /etc/haproxy/haproxy.cfg; then\n"
        + '    echo "[haproxy] ОШИБКА: конфигурация невалидна (haproxy -c)."; exit 1\n'
        + "fi\n"
        + 'echo "[haproxy] Configuration file is valid. Перезапуск службы..."\n'
        + "systemctl restart haproxy\n"
        + "sleep 1\n"
        + "systemctl status haproxy --no-pager 2>&1 | head -n 12 || true\n"
    )
    await ssh.run_script(apply_script, task, timeout=120)

    # 3. Verify the daemon is active (running) — hard fail otherwise
    status = await ssh.get_output("systemctl is-active haproxy 2>/dev/null")
    if (status or "").strip() != "active":
        raise RuntimeError(
            "HAProxy не запущен (systemctl is-active ≠ active). "
            "Проверьте: journalctl -u haproxy -n 50"
        )
    task.add_log("\x1b[32m[haproxy] Служба active (running) — реле развёрнуто.\x1b[0m")


# ──────────────────────────────────────────────────────────────
# Ф6 — Auto-create Remnawave hosts from local host-templates
# ──────────────────────────────────────────────────────────────

def _map_host_optional(tpl: dict) -> dict:
    """Map a local HostTemplateBody dict (accounts/<id>/hosts.json) → the OPTIONAL
    CreateHostRequestDto fields (Remnawave camelCase). Only non-empty / enabled
    values are included so the API keeps its own defaults for everything else.
    These strings go into a JSON API body (NOT bash), so no shell-escaping."""
    import re as _re

    out: dict = {}

    def put_str(key: str, val) -> None:
        if isinstance(val, str) and val.strip():
            out[key] = val

    put_str("sni",               tpl.get("sni"))
    put_str("host",              tpl.get("host"))
    put_str("path",              tpl.get("path"))
    put_str("alpn",              tpl.get("alpn"))
    put_str("fingerprint",       tpl.get("fingerprint"))
    put_str("serverDescription", tpl.get("server_description"))

    # security_layer: local values are lowercase (default|tls|reality); Remnawave's
    # host enum is DEFAULT|TLS|NONE. Skip "default" (API default already); map
    # tls/none; DROP anything else (e.g. "reality" — not a host-level enum in
    # Remnawave) so a stray value can't 400 the whole host-create.
    _sec = str(tpl.get("security_layer") or "").strip().lower()
    if _sec in ("tls", "none"):
        out["securityLayer"] = _sec.upper()

    if tpl.get("sni_from_address"): out["overrideSniFromAddress"] = True
    if tpl.get("sni_empty"):        out["keepSniBlank"] = True
    if tpl.get("hide_host"):        out["isHidden"] = True
    if tpl.get("shuffle_host"):     out["shuffleHost"] = True
    if tpl.get("x25519mlkem768"):   out["mihomoX25519"] = True
    # local «Хост виден» off (visible=False) → Remnawave isDisabled=True (inverse).
    if tpl.get("visible") is False:
        out["isDisabled"] = True

    route_id = tpl.get("vless_route_id")
    if isinstance(route_id, int) and route_id > 0:
        out["vlessRouteId"] = route_id

    excl_squads = tpl.get("exclude_squads")
    if isinstance(excl_squads, list) and excl_squads:
        out["excludedInternalSquads"] = excl_squads
    excl_sub = tpl.get("exclude_sub_types")
    if isinstance(excl_sub, list) and excl_sub:
        # local values are lowercase (xray_json…); Remnawave's enum is uppercase.
        out["excludeFromSubscriptionTypes"] = [str(s).upper() for s in excl_sub if str(s).strip()]

    for local_key, api_key in (
        ("xhttp",      "xhttpExtraParams"),
        ("mux",        "muxParams"),
        ("sockopt",    "sockoptParams"),
        ("final_mask", "finalMask"),
    ):
        v = tpl.get(local_key)
        if isinstance(v, dict) and v:
            out[api_key] = v

    tag = tpl.get("tag")
    if isinstance(tag, str) and _re.fullmatch(r"[A-Z0-9_:]+", tag) and len(tag) <= 36:
        out["tags"] = [tag]

    # NOT mapped by design: `xray_json_template` (local free text) has no clean
    # target — Remnawave's `xrayJsonTemplateUuid` expects a UUID to a separate
    # entity, not inline JSON — so it's intentionally omitted.
    return out


async def step_create_hosts(
    task: Task,
    client,
    req: "DeployRequest",
    node_uuid: str,
    config_profile_uuid: str,
    host_template_ids: list[str],
) -> None:
    """
    Ф6 — auto-create Remnawave hosts from the account's local host-templates.

    For each selected host-template (the Template's `host_template_ids` MINUS the
    deploy's `disabled_host_template_ids`), POST /api/hosts with:
      - address = the new node's FQDN (req.domain),
      - nodes   = [node_uuid],
      - inbound = { configProfileUuid: <node's profile>, configProfileInboundUuid:
                    <template.inbound> } (the local `inbound` free-text field is
                    treated as the inbound uuid),
    plus mapped optional fields. Hosts are ADDITIVE: a per-host failure is logged
    as a warning and skipped — it never fails the deploy. A template with an empty
    `inbound` is invalid for POST /api/hosts → skipped with a log.
    """
    from app.services import storage as _storage
    from app.services.remnawave_client import RemnavaveError

    disabled = set(req.disabled_host_template_ids or [])
    wanted = [hid for hid in (host_template_ids or []) if hid not in disabled]
    if not wanted:
        return

    all_hosts = {
        h["id"]: h
        for h in _storage.load_hosts()
        if isinstance(h, dict) and h.get("id")
    }
    task.add_log("\n\x1b[36m[Хосты] Создаю хосты Remnawave из шаблонов...\x1b[0m")

    suffix = req.domain.split(".")[0]
    for hid in wanted:
        tpl = all_hosts.get(hid)
        if tpl is None:
            task.add_log(f"\x1b[33m[Хосты] Шаблон хоста id={hid} не найден — пропуск.\x1b[0m")
            continue
        inbound_uuid = (tpl.get("inbound") or "").strip()
        if not inbound_uuid:
            task.add_log(
                f"\x1b[33m[Хосты] Шаблон «{tpl.get('remark', hid)}» без inbound — "
                f"пропуск (POST /api/hosts требует inbound).\x1b[0m"
            )
            continue
        # Remnawave's host `remark` maxLength is 40 (local allows 200) — truncate
        # or the whole create 400s (silently, since failures are caught below).
        remark = f"{tpl.get('remark') or 'host'} · {suffix}"[:40]
        try:
            await client.create_host(
                inbound={
                    "configProfileUuid": config_profile_uuid,
                    "configProfileInboundUuid": inbound_uuid,
                },
                remark=remark,
                address=req.domain,
                port=int(tpl.get("port") or req.remnanode_port),
                nodes=[node_uuid],
                **_map_host_optional(tpl),
            )
            task.add_log(f"\x1b[32m[Хосты] Хост «{remark}» создан (address={req.domain}).\x1b[0m")
        except RemnavaveError as exc:
            task.add_log(
                f"\x1b[33m[ПРЕДУПРЕЖДЕНИЕ] Не удалось создать хост «{remark}»: {exc.detail}\x1b[0m"
            )
        except Exception as exc:
            task.add_log(
                f"\x1b[33m[ПРЕДУПРЕЖДЕНИЕ] Ошибка при создании хоста «{remark}»: {exc}\x1b[0m"
            )


# ──────────────────────────────────────────────────────────────
# Pre-deploy: Register node in Remnawave, obtain token for SSH step
# ──────────────────────────────────────────────────────────────

async def step_remnawave_pre_deploy(
    task: Task, req: "DeployRequest"
) -> tuple[str, str]:
    """
    Create config profile + node in Remnawave BEFORE the SSH remnanode step.
    Returns (secret_key, node_uuid):
      - secret_key — the long base64/JWT token (eyJ…) from GET /api/keygen;
        this is the container SECRET_KEY env value.
      - node_uuid  — the node identifier used only for routing (squad binding,
        traffic rules, …).

    Raises RuntimeError on failure (stops the pipeline).
    """
    import json as _json
    from app.services import storage as _storage
    from app.models.settings import AppSettings
    from app.services.remnawave_client import RemnavaveClient, RemnavaveError

    task.add_log("\n\x1b[36m──────────────────────────────────────────────────────────\x1b[0m")
    task.add_log("\x1b[1;36m[Remnawave] Шаг 1/2 — регистрация ноды в панели...\x1b[0m")
    task.add_log("\x1b[36m──────────────────────────────────────────────────────────\x1b[0m")

    cfg = AppSettings(**_storage.load_settings()).remnawave
    if not cfg.panel_url or not cfg.api_token:
        raise RuntimeError(
            "Опция «Зарегистрирова��ь в Remnawave» включена, но панель не настроена. "
            "Укажите URL и токен в разделе Настройки → Remnawave."
        )

    domain = req.domain
    name = domain.split(".")[0]
    client = RemnavaveClient(cfg.panel_url, cfg.api_token)

    # Step A: Create config profile from template
    templates = _storage.load_templates()
    tpl = next((t for t in templates if t["id"] == req.template_id), None)
    if tpl is None:
        raise RuntimeError(
            f"Шаблон id={req.template_id!r} не найден. "
            "Создайте шаблон в разделе Шаблоны или снимите флаг «Зарегистрировать в Remnawave»."
        )

    privkey  = _generate_x25519_privkey()
    short_id = _generate_short_id()
    task.add_log(f"\x1b[90m[Remnawave] privkey={privkey[:8]}… shortid={short_id}\x1b[0m")

    config_str = (
        tpl["config"]
        .replace("$domain",  domain)
        .replace("$name",    name)
        .replace("$privkey", privkey)
        .replace("$shortid", short_id)
    )
    try:
        config_json = _json.loads(config_str)
    except _json.JSONDecodeError as exc:
        raise RuntimeError(
            f"JSON шаблона невалиден после подстановки переменных: {exc}"
        ) from exc

    try:
        profile = await client.create_config_profile(name, config_json)
    except RemnavaveError as exc:
        raise RuntimeError(
            f"Не удалось создать профиль конфигурации в Remnawave: {exc.detail}"
        ) from exc

    config_profile_uuid: str = profile["uuid"]
    active_inbounds: list[str] = [ib["uuid"] for ib in profile.get("inbounds", [])]
    task.add_log(
        f"\x1b[32m[Remnawave] Профиль конфига создан: {config_profile_uuid} "
        f"({len(active_inbounds)} inbound(s))\x1b[0m"
    )

    # Step B: Create node. The node API binds a single plugin via
    # activePluginUuid (None = no plugin).
    try:
        node = await client.create_node(
            name=name,
            address=req.ip,
            port=req.remnanode_port,  # from the "Порт remnanode" form field (default 2222)
            config_profile_uuid=config_profile_uuid,
            active_inbounds=active_inbounds,
            country_code=req.country_code,
            active_plugin_uuid=req.plugin_uuid,
        )
    except RemnavaveError as exc:
        raise RuntimeError(
            f"Не удалось создать ноду в Remnawave: {exc.detail}"
        ) from exc

    node_uuid: str = node["uuid"]
    task.add_log(
        f"\x1b[32m[Remnawave] Нода зарегистрирована. UUID (для маршрутизации): {node_uuid}\x1b[0m"
    )

    # Step B2: Bind the node's config-profile inbounds to selected internal
    # squads. Without this the node exists but squad users can't reach it.
    # Form selection takes priority; fall back to settings defaults.
    int_squads = list(req.internal_squad_ids) or list(cfg.default_internal_squad_ids)
    if int_squads and active_inbounds:
        for sq_id in int_squads:
            try:
                await client.add_inbounds_to_internal_squad(sq_id, active_inbounds)
                task.add_log(
                    f"\x1b[32m[Remnawave] Профиль ноды привязан к скваду {sq_id[:8]}…\x1b[0m"
                )
            except RemnavaveError as exc:
                task.add_log(
                    f"\x1b[33m[ПРЕДУПРЕЖДЕНИЕ] Не удалось привязать профиль к скваду "
                    f"{sq_id[:8]}…: {exc.detail}\x1b[0m"
                )

    # Step B3: auto-create Remnawave hosts from the account's host-templates (Ф6).
    # Reuses the SAME configProfileUuid create_node was given; additive/non-fatal.
    await step_create_hosts(
        task, client, req, node_uuid, config_profile_uuid,
        tpl.get("host_template_ids", []) if isinstance(tpl, dict) else [],
    )

    # Step C: Fetch the real SECRET_KEY (long base64 token) — NOT the UUID.
    # The container authenticates with this, not with the node uuid.
    try:
        secret_key = await client.get_node_secret_key()
    except RemnavaveError as exc:
        raise RuntimeError(
            f"Не удалось получить SECRET_KEY ноды (GET /api/keygen): {exc.detail}"
        ) from exc

    task.add_log(
        f"\x1b[32m[Remnawave] SECRET_KEY получен (eyJ…): {secret_key[:24]}…\x1b[0m"
    )

    return secret_key, node_uuid  # (SECRET_KEY token, routing uuid)


# ─────────────────────────────────────────────────���────────────
# Post-deploy: Add users to squads (non-fatal)
# ──────────────────────────────────────────────────────────────

async def step_apply_traffic_rules(task: Task, node_uuid: str) -> None:
    """
    After node creation in Remnawave: find matching ALL-scope traffic rules in
    local storage and apply them (node-level monthly bandwidth cap).
    Errors are logged as warnings — they don't fail the deploy.
    """
    from app.services import storage as _storage
    from app.models.settings import AppSettings
    from app.models.traffic_rules import TrafficRule
    from app.api.traffic_rules import apply_rule_to_remnawave
    from app.services.remnawave_client import RemnavaveClient

    if not node_uuid:
        return

    rules_raw = _storage.load_traffic_rules()
    matching = [
        r for r in rules_raw
        if r.get("node_uuid") == node_uuid and r.get("scope") == "ALL"
    ]
    if not matching:
        return

    cfg = AppSettings(**_storage.load_settings()).remnawave
    if not cfg.panel_url or not cfg.api_token:
        return

    client = RemnavaveClient(cfg.panel_url, cfg.api_token)
    task.add_log("\n\x1b[36m[Ограничение трафика] Применяю сохранённые правила...\x1b[0m")

    for raw in matching:
        try:
            rule = TrafficRule(**raw)
            await apply_rule_to_remnawave(client, rule)
            task.add_log(
                f"\x1b[32m[Ограничение трафика] Правило «{raw.get('limit_gb', 0)} ГБ/"
                f"{raw.get('period', '')}» применено к ноде.\x1b[0m"
            )
            # Update sync status in storage
            all_rules = _storage.load_traffic_rules()
            from datetime import datetime, timezone
            for r in all_rules:
                if r["id"] == raw["id"]:
                    r["sync_status"]    = "synced"
                    r["last_synced_at"] = datetime.now(timezone.utc).isoformat()
                    r["sync_error"]     = None
            _storage.save_traffic_rules(all_rules)
        except Exception as exc:
            task.add_log(
                f"\x1b[33m[ПРЕДУПРЕЖДЕНИЕ] Не удалось применить правило трафика: {exc}\x1b[0m"
            )


async def step_remnawave_add_squads(task: Task, req: "DeployRequest") -> None:
    """Add all users to every selected squad. Failures are warnings only."""
    from app.services import storage as _storage
    from app.models.settings import AppSettings
    from app.services.remnawave_client import RemnavaveClient, RemnavaveError

    cfg = AppSettings(**_storage.load_settings()).remnawave
    if not cfg.panel_url or not cfg.api_token:
        return

    client = RemnavaveClient(cfg.panel_url, cfg.api_token)

    task.add_log("\n\x1b[36m──────────────────────────────────────────────────────────\x1b[0m")
    task.add_log("\x1b[1;36m[Remnawave] Шаг 2/2 — привязка пользователей к сквадам...\x1b[0m")

    # Merge form IDs with settings defaults; form takes priority over defaults
    int_squads = list(req.internal_squad_ids) or list(cfg.default_internal_squad_ids)
    ext_squads = list(req.external_squad_ids) or list(cfg.default_external_squad_ids)

    if not int_squads and not ext_squads:
        task.add_log("\x1b[90m[Remnawave] Сквады не выбраны — привязка пропущена.\x1b[0m")
        return

    try:
        for sq_id in int_squads:
            await client.add_all_users_to_internal_squad(sq_id)
            task.add_log(
                f"\x1b[32m[Remnawave] Внутренний сквад {sq_id[:8]}… — пользователи добавлены.\x1b[0m"
            )
        for sq_id in ext_squads:
            await client.add_all_users_to_external_squad(sq_id)
            task.add_log(
                f"\x1b[32m[Remnawave] Внешний сквад {sq_id[:8]}… — пользователи добавлены.\x1b[0m"
            )
    except RemnavaveError as exc:
        task.add_log(
            f"\n\x1b[1;33m[ПРЕДУПРЕЖДЕНИЕ] Нода развёрнута, но привязка к скваду не удалась: "
            f"{exc.detail}\x1b[0m"
        )
    except Exception as exc:
        task.add_log(
            f"\n\x1b[1;33m[ПРЕДУПРЕЖДЕНИЕ] Ошибка привязки к скваду: {exc}\x1b[0m"
        )


# ──────────────────────────────────────────────────────────────
# Main pipeline runner
# ──────────────────────────────────────────────────────────────

async def run_pipeline(req: DeployRequest, task: Task) -> None:
    ssh: Optional[SSHSession] = None
    try:
        # ── Resolve backend IP for whitelist rules ────────────
        backend_ip = await get_backend_ip()
        if backend_ip:
            task.add_log(f"\x1b[90m[whitelist] IP бэкенда: {backend_ip}\x1b[0m")
        else:
            task.add_log("\x1b[33m[whitelist] Не удалось определить IP бэкенда — "
                         "вайтлист в UFW/Fail2Ban/iptables будет пропущен.\x1b[0m")

        # ── Step 1: Connect ──────────────────────────────────
        _begin_step(task, 1)
        task.add_log(f"Подключение к {req.ip}:{req.current_ssh_port} как {req.ssh_user}...")
        ssh = SSHSession(req.ip, req.current_ssh_port, req.ssh_user, req.ssh_password)
        await ssh.connect()

        os_info = await ssh.get_output(
            "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'"
        )
        task.add_log(f"\x1b[32mПодключено. ОС: {os_info or 'unknown'}\x1b[0m")

        # ── Step 2: apt-get update always; upgrade conditional ───
        _begin_step(task, 2)
        if req.update_system:
            task.add_log("\x1b[36mОбновление индекса пакетов и апгрейд системы...\x1b[0m")
            update_script = f"""\
{_APT_WAIT}
apt-get update -y
apt-get upgrade -y -o Dpkg::Options::='--force-confdef' \
    -o Dpkg::Options::='--force-confold'
echo "[update] Обновление завершено."
"""
            await ssh.run_script(update_script, task, timeout=600)
        else:
            task.add_log("\x1b[36mОбновление индекса пакетов (apt-get update)...\x1b[0m")
            refresh_script = f"""\
{_APT_WAIT}
apt-get update -y
echo "[update] Индекс пакетов обновлён."
"""
            await ssh.run_script(refresh_script, task, timeout=120)

        # ── Base utility: vnstat (network traffic monitor) ────
        # Starts collecting per-interface stats immediately; the deploy cards
        # read `vnstat --json` for the traffic block. Gated on install_vnstat —
        # if off, the card hides the traffic block and /api/stats/node degrades
        # to empty trafficStats (does not 500).
        if req.install_vnstat:
            vnstat_script = f"""\
{_APT_WAIT}
{_apt_install("vnstat")}
systemctl enable --now vnstat 2>/dev/null || true
echo "[vnstat] Демон vnstat установлен и запущен."
"""
            await ssh.run_script(vnstat_script, task, check=False, timeout=120)
        else:
            task.add_log("\x1b[90m[vnstat] Пропущено по настройке (install_vnstat=false).\x1b[0m")

        # skip_components (add-existing-server flow): components already present on
        # the box are begun-but-skipped. Dependency order is preserved (skipping is
        # per-component, the step sequence is unchanged), so you can't skip a
        # prerequisite in a way that breaks a later step.
        skip = set(req.skip_components or [])

        if "node_accelerator" in skip:
            _skip_component(task, 3, "node-accelerator")
        else:
            await step_node_accelerator(ssh, task, req)
        if "trafficguard" in skip:
            _skip_component(task, 4, "TrafficGuard")
        elif req.install_trafficguard:
            await step_traffic_guard(ssh, task, backend_ip)
        else:
            _begin_step(task, 4)
            task.add_log("\x1b[90m[TrafficGuard] Пропущено по настройке (install_trafficguard=false).\x1b[0m")
        # Step 5: test toolkit (iperf3/speedtest/xray) — optional, non-fatal,
        # runs in both modes.
        if "test_tools" in skip:
            _skip_component(task, 5, "test-tools")
        else:
            await step_test_tools(ssh, task, req)
        # Step 6 configures dual-port SSH and reboots the box (when enabled),
        # closing the pre-reboot session.
        await step_system_optimize(ssh, task, backend_ip, req)
        # Step 7 polls for the rebooted server, then verifies the new port and
        # finalizes — or rolls back via the old port and aborts. Returns the live
        # session used for all later steps.
        ssh = await step_ssh_dualport_verify(ssh, task, req, backend_ip)

        # ── Mode branch: haproxy relay (step 10, skips 11–14) vs full remnanode
        #    stack (steps 10–14) ──
        if req.mode == "haproxy":
            if "haproxy" in skip:
                _skip_component(task, 10, "HAProxy", label="Установка HAProxy-реле")
            else:
                await step_haproxy_deploy(ssh, task, req)
        else:
            if "ssl" in skip:
                _skip_component(task, 10, "SSL")
            else:
                await step_ssl(ssh, task, req.domain, req.email, req.cloudflare_api_key,
                               req.ip, req.cert_provider)

            # ── Remnawave pre-deploy: create node, get token ──────
            # (Panel-side registration — independent of whether the on-server
            # remnanode install is skipped.)
            remnanode_token = req.remnanode_token  # manual token (may be None)
            _uuid = None
            if req.create_in_remnawave:
                token, _uuid = await step_remnawave_pre_deploy(task, req)
                remnanode_token = token

            if "remnanode" in skip:
                _skip_component(task, 11, "remnanode")
            else:
                if not remnanode_token:
                    raise RuntimeError(
                        "Токен Remnanode не указан и не получен из панели. "
                        "Укажите токен вручную или включите «Зарегистрировать в Remnawave»."
                    )
                await step_remnanode(
                    ssh, task, remnanode_token, req.domain,
                    node_port=req.remnanode_port,
                    xhttp_path=req.xhttp_path,
                )

            # ── Step 12: uniquize the masking decoy site — runs BEFORE WARP ──
            # (masking mutates /var/www/html and must not be affected by WARP's
            # routing changes; ordering: Remnanode → Masking → WARP → Hysteria2).
            if "masking" in skip:
                _skip_component(task, 12, "masking")
            else:
                await step_sni_masking(ssh, task)

            # ── Step 13: WARP Native (non-fatal) ──
            if "warp" in skip:
                _skip_component(task, 13, "warp")
            elif req.install_warp:
                try:
                    await step_warp(ssh, task)
                except Exception as _warp_exc:
                    task.add_log(
                        f"\n\x1b[33m[ПРЕДУПРЕЖДЕНИЕ] WARP завершился с ошибкой: {_warp_exc}\n"
                        f"Нода Remnawave продолжает работу.\x1b[0m"
                    )
            else:
                _begin_step(task, 13)
                task.add_log("\x1b[90m[skip] WARP не выбран.\x1b[0m")

            # ── Step 14: Hysteria2 (Certbot standalone SSL — label only renamed) ──
            if "hysteria2" in skip:
                _skip_component(task, 14, "hysteria2")
            else:
                await step_certbot_ssl(ssh, task, req.domain, req.email)

            # ── Remnawave post-deploy: assign users to squads ─────
            if req.create_in_remnawave:
                await step_remnawave_add_squads(task, req)
                await step_apply_traffic_rules(task, _uuid)

        task.finish(TaskStatus.SUCCESS)
        task.add_log("\n\x1b[1;32m✓ Деплой завершён успешно!\x1b[0m")

    except asyncio.CancelledError:
        task.add_log(
            "\n\x1b[1;33m[СИСТЕМА] Процесс деплоя принудительно остановлен "
            "пользователем. Соединение закрыто.\x1b[0m"
        )
        task.finish(TaskStatus.FAILED, "Остановлено пользователем")
        raise  # CancelledError must always be re-raised
    except Exception as exc:
        task.add_log(f"\n\x1b[1;31m✗ Ошибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED, str(exc))
        raise
    finally:
        if ssh:
            await ssh.close()
