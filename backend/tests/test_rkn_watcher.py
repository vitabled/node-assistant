"""Шаг 4 — RKN Watcher (вместо TrafficGuard): payload, fail-closed на SHA, метрики.

Проверяется:
  (a) payload шага: закреплённый коммит + SHA256 трёх файлов, предзаполнение
      /etc/rkn-watcher с whitelist.json `"enabled": false`, снятие легаси, установка
      нашего доп. компонента (второй ресурс списков), правило `deploy-panel-whitelist`
      ДО любых DROP-цепочек, машинно-читаемый итог `RKN_SWAP_RESULT`;
  (b) несовпадение SHA256 → payload НИЧЕГО не применяет (реальный прогон bash с
      подставными curl/tar/sha256sum: до блоков легаси/конфига/установки дело не доходит);
  (c) `step_rkn_watcher` нефатален (ошибка → запись в лог, без исключения) и идёт через
      payload + пробу состояния;
  (d) `/api/stats/node` отдаёт новые поля rknWatcher* (trafficGuardActive убран);
  (e) legacy-имена маппятся: поле `install_trafficguard` → `install_rkn_watcher`,
      компонент `trafficguard` → `rkn_watcher` (модель деплоя и node_ops).

`asyncssh` подменяется заглушкой (как в test_pipeline_scripts), чтобы стек SSH
импортировался без нативных зависимостей.
"""
import asyncio
import os
import stat
import subprocess
import sys
import types
import uuid

import pytest
from fastapi.testclient import TestClient

sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

import app.api.node_ops as node_ops  # noqa: E402
import app.api.stats as stats_api  # noqa: E402
import app.services.pipeline as pipeline  # noqa: E402
import app.services.rkn_watcher as rkn_watcher  # noqa: E402
from app.main import app  # noqa: E402
from app.models.deploy import DeployRequest  # noqa: E402
from app.services.task_store import STEP_LABELS  # noqa: E402

client = TestClient(app)

BACKEND_IP = "203.0.113.9"


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"rkn-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _payload() -> str:
    """Payload шага — как его соберёт pipeline.step_rkn_watcher."""
    return rkn_watcher.build_rkn_watcher_script(BACKEND_IP, "198.51.100.5, 10.1.0.0/16")


# ── (a) payload ───────────────────────────────────────────────

def test_payload_pins_upstream_commit_and_checks_sha256():
    p = _payload()
    assert rkn_watcher.RKN_COMMIT in p
    assert f"tar.gz/{rkn_watcher.RKN_COMMIT}" in p      # скачивание по коммиту
    assert "sha256sum" in p
    # Mirror First: зеркало первым, апстрим — резерв (прямых апстрим-URL не бывает)
    assert rkn_watcher.RKN_TARBALL_MIRROR in p
    assert "vitabled/mirror-rkn-watcher" in p
    assert rkn_watcher.RKN_TARBALL_URLS == (rkn_watcher.RKN_TARBALL_MIRROR,
                                           rkn_watcher.RKN_TARBALL_UPSTREAM)
    assert p.index(rkn_watcher.RKN_TARBALL_MIRROR) < p.index(rkn_watcher.RKN_TARBALL_UPSTREAM)
    assert "пробую резервный источник" in p
    # все три исполняемых файла с их эталонными дайджестами (имена — из апстрима)
    for name, digest in rkn_watcher.RKN_SHA256.items():
        assert f"{name}:{digest}" in p
    # эталонные значения (закреплены явно — смена = осознанная правка)
    assert "b9f4b471746d6d2739a8f87753e9d81cd5705d3a8b5d72a4367c103522d9a143" in p
    assert "e1804e57be03ea9ff89b51f98727b308765f2ab83c0d710b8ceb27a0b351513c" in p
    assert "b3d63fc9815fa4933a9d6c653b311b9a79815638b047fc37efc0b3c24fc3718f" in p


def test_payload_has_no_unresolved_placeholders():
    p = _payload()
    for placeholder in ("__RKN_COMMIT__", "__RKN_TARBALL_URLS__", "__RKN_EXTRA_DIR__",
                        "__BACKEND_IP__", "__WHITELIST__", "__SHA_PAIRS__",
                        "__APT_WAIT__", "__APT_INSTALL__", "__EXTRA_SCRIPT__"):
        assert placeholder not in p, placeholder


def test_payload_prefills_config_with_disabled_geoip_allowlist():
    p = _payload()
    assert "/etc/rkn-watcher/settings.conf" in p
    assert 'FILTER_PORTS="all"' in p and 'ENABLE_TSPUBLOCK="y"' in p and 'ENABLE_GOVIPS="y"' in p
    assert "/etc/rkn-watcher/whitelist.json" in p
    # enabled: false ОБЯЗАТЕЛЕН — иначе GeoIP-allowlist отрежет клиентов
    assert '"enabled": False, "countries": [], "ips": ips, "ports": []' in p
    assert "/etc/rkn-watcher/blacklist.json" in p
    # в ips — адрес панели, whitelist запроса и приватные сети
    for token in (BACKEND_IP, "198.51.100.5", "10.1.0.0/16", "172.16.0.0/12",
                  "10.0.0.0/8", "192.168.0.0/16", "127.0.0.1/8"):
        assert token in p


def test_payload_removes_legacy_toolchain():
    p = _payload()
    for removed in (
        "antiscan-aggregate.timer", "antiscan-ipset-restore.service",
        "/etc/rsyslog.d/10-iptables-scanners.conf", "/etc/logrotate.d/iptables-scanners",
        "/usr/local/bin/rknpidor", "/usr/local/bin/traffic-guard",
        "/opt/trafficguard-manager.sh", "/opt/TrafficGuard-auto",
        "SCANNERS-BLOCK-V4", "SCANNERS-BLOCK-V6", "na-ctguard",
        # снимаем сторонние apt-источники speedtest/Ookla ДО установщика апстрима:
        # на noble их apt-get update внутри установщика падает
        "*ookla*", "*speedtest*",
    ):
        assert removed in p, removed
    for chain in ("SCANNERS", "SCANNERS-BLOCK"):
        assert chain in p


def test_payload_installs_upstream_non_interactively():
    p = _payload()
    assert "RKN_SKIP_DEP_INSTALL=1" in p
    assert "printf 'y\\n' | RKN_SKIP_DEP_INSTALL=1 bash ./rkn-watcher.sh install" in p
    assert "/opt/rkn-watcher/rkn-watcher.sh apply --quiet" in p
    assert "systemctl enable rkn-watcher-update.timer" in p


def test_payload_deploys_extra_component_and_units():
    p = _payload()
    # сам доп. компонент (второй ресурс: shadow-netlab/traffic-guard-lists)
    assert rkn_watcher.EXTRA_SCRIPT in p
    assert "EXTRA_ANTISCAN" in p and "EXTRA_GOVNET" in p
    assert "antiscanner.list" in p and "government_networks.list" in p
    assert "shadow-netlab/traffic-guard-lists" in p
    # Mirror First: списки с зеркала, апстрим — резерв, источники пробуются по порядку
    assert "vitabled/mirror-traffic-guard-lists" in p
    assert "LIST_SCAN_URLS=(" in p and "LIST_GOV_URLS=(" in p
    assert p.index("vitabled/mirror-traffic-guard-lists/main/public/antiscanner.list") < p.index(
        "shadow-netlab/traffic-guard-lists/refs/heads/main/public/antiscanner.list")
    assert "RKN_EXTRA" in p          # своя цепочка, правила апстрима не трогаем
    assert "with_lock" in p and "flock" in p   # boot и таймер не наплодят дублей
    # три юнита
    for unit in ("rkn-watcher-extra.service", "rkn-watcher-extra-boot.service",
                 "rkn-watcher-extra.timer"):
        assert f"/etc/systemd/system/{unit}" in p
    # enable БЕЗ --now, первичный update вручную, и только потом таймер —
    # иначе boot-юнит и ручной запуск наплодят дубли правил
    assert "systemctl enable rkn-watcher-extra-boot.service rkn-watcher-extra.timer" in p
    assert "enable --now rkn-watcher-extra" not in p     # ставим без --now
    manual_update = p.index('rkn-watcher-extra.sh" update')
    timer_start = p.index("systemctl start rkn-watcher-extra.timer")
    assert manual_update < timer_start
    # вайтлист прокинут и в конфиг доп. компонента
    assert 'WHITELIST="' in p and "/etc/rkn-watcher-extra/extra.conf" in p


def test_payload_whitelists_panel_before_drop_chains():
    p = _payload()
    whitelist_rule = p.index("deploy-panel-whitelist")
    # правило ставится раньше, чем создаются блокирующие цепочки и запускается установщик
    assert whitelist_rule < p.index("rkn-watcher.sh install")
    assert whitelist_rule < p.index('for _ch in SCANNERS')
    assert "iptables -I INPUT 1" in p


def test_payload_reports_machine_readable_result():
    p = _payload()
    assert "RKN_SWAP_RESULT=OK" in p and "RKN_SWAP_RESULT=CHECK" in p
    assert "RKN_SWAP_COUNTS tspu=" in p and "govnet=" in p
    for chain in rkn_watcher.RKN_CHAINS:
        assert chain in p
    for ipset_name in rkn_watcher.RKN_SETS:
        assert ipset_name in p


def test_payload_is_valid_bash():
    """`bash -n` по payload'у: скобки/heredoc'и (EXTRA_SCRIPT вложен в heredoc)."""
    proc = subprocess.run(["bash", "-n"], input=_payload(),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_whitelist_tokens_are_validated_and_deduped():
    wl = rkn_watcher._whitelist("203.0.113.9", "1.2.3.4, 1.2.3.4 ; rm -rf / 10.1.0.5/24 ::1")
    assert wl == ("203.0.113.9 1.2.3.4 10.1.0.0/24 172.16.0.0/12 10.0.0.0/8 "
                  "192.168.0.0/16 127.0.0.1/8")
    # одиночный адрес без /32, мусор и IPv6 — прочь; приватные сети — в эталонном виде
    assert "/32" not in wl and "rm" not in wl and "127.0.0.1/8" in wl
    # пустой backend_ip не ломает список
    assert rkn_watcher._whitelist("", "") == ("172.16.0.0/12 10.0.0.0/8 "
                                              "192.168.0.0/16 127.0.0.1/8")


# ── (b) fail-closed: реальный прогон bash с подставными утилитами ──

_STUB = """#!/usr/bin/env bash
echo "{name} $*" >> "$STUB_LOG"
{body}
"""


def _sandbox(tmp_path, *, good_digests: bool) -> tuple[str, str]:
    """Стенд: PATH с заглушками + лог вызовов. Возвращает (PATH, лог).

    Заглушки пишут свой вызов в лог — по нему видно, дошли ли до «применения».
    ⚠️ `fuser` обязан отвечать «локов нет» (exit 1): заглушка, возвращающая 0,
    зациклит `_wait_apt` в payload'е.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    digests = {name: (digest if good_digests else "0" * 64)
               for name, digest in rkn_watcher.RKN_SHA256.items()}
    case = "\n".join(
        f'        {name}) echo "{digest}  $1" ;;' for name, digest in digests.items()
    )
    bodies = {
        "curl": 'out=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
                '[ -n "$out" ] && : > "$out"\nexit 0',
        "tar": 'dest=""; prev=""; for a in "$@"; do [ "$prev" = "-C" ] && dest="$a"; prev="$a"; done\n'
               'if [ -n "$dest" ]; then\n'
               '    for f in rkn-watcher.sh config_tool.py geoip_apply.py installer.sh; do : > "$dest/$f"; done\n'
               'fi\nexit 0',
        "sha256sum": 'case "$(basename "$1")" in\n'
                     + case + "\n"
                     '        *) echo "0000000000000000000000000000000000000000000000000000000000000000  $1" ;;\n'
                     'esac\nexit 0',
        "mktemp": 'd="$SANDBOX/tmp.$$.$RANDOM"; mkdir -p "$d"; echo "$d"\nexit 0',
        "fuser": "exit 1",      # локов нет
        # `-C` (проверить правило) обязан отвечать «нет правила» (exit 1), иначе цикл
        # «while iptables -C …; do iptables -D …; done» в rkn_whitelist зацикливается.
        "iptables": '[ "$1" = "-C" ] && exit 1\nexit 0',
    }
    for name in ("apt-get", "ipset", "systemctl", "pkill",
                 "sleep", "netfilter-persistent", "chmod"):
        bodies.setdefault(name, "exit 0")
    for name, body in bodies.items():
        stub = bin_dir / name
        stub.write_text(_STUB.format(name=name, body=body), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(bin_dir), str(log)


def _run_prefix(tmp_path, *, good_digests: bool) -> tuple[str, str]:
    """Прогоняет предполётную часть payload'а (до снятия легаси) + маркер «применяем».

    Всё, что ПОСЛЕ маркера снятия легаси, — это и есть «применение» (легаси, конфиг,
    установка, доп. компонент): если маркер не напечатан, не выполнено ничего из этого.
    """
    payload = _payload()
    prefix = payload.split('echo "[rkn] Снятие легаси', 1)[0]
    bin_dir, log = _sandbox(tmp_path, good_digests=good_digests)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               SANDBOX=str(tmp_path), STUB_LOG=log)
    proc = subprocess.run(["bash"], input=prefix + '\necho "APPLY_SENTINEL_REACHED"\n',
                          capture_output=True, text=True, env=env, timeout=120)
    return proc.stdout + proc.stderr, log


def test_sha256_mismatch_applies_nothing(tmp_path):
    out, log = _run_prefix(tmp_path, good_digests=False)
    assert "SHA256 НЕ СОВПАЛ" in out
    assert "RKN_SWAP_RESULT=CHECK" in out
    # НИЧЕГО не применено: до блоков применения (легаси/конфиг/установка/доп) дело не дошло
    assert "APPLY_SENTINEL_REACHED" not in out
    # и ни одна утилита применения не вызывалась — только предполётный набор
    called = {line.split()[0] for line in
              open(log, encoding="utf-8").read().splitlines() if line.strip()}
    assert called <= {"apt-get", "iptables", "curl", "tar", "sha256sum", "mktemp",
                      "pkill", "fuser", "sleep", "chmod"}
    assert not called & {"systemctl", "ipset", "rm", "netfilter-persistent"}


def test_sha256_match_reaches_apply_stage(tmp_path):
    """Положительный контроль: с верными дайджестами предполётная часть проходит."""
    out, _ = _run_prefix(tmp_path, good_digests=True)
    assert "SHA256 ок: rkn-watcher.sh" in out
    assert "APPLY_SENTINEL_REACHED" in out
    assert "RKN_SWAP_RESULT=CHECK" not in out


def test_sha256_download_failure_applies_nothing(tmp_path):
    """Обрыв загрузки (curl не отработал) — та же fail-closed семантика."""
    payload = _payload()
    prefix = payload.split('echo "[rkn] Снятие легаси', 1)[0]
    bin_dir, log = _sandbox(tmp_path, good_digests=True)
    curl = os.path.join(bin_dir, "curl")
    with open(curl, "w", encoding="utf-8") as fh:
        fh.write(f'#!/usr/bin/env bash\necho "curl $*" >> "$STUB_LOG"\nexit 1\n')
    os.chmod(curl, 0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               SANDBOX=str(tmp_path), STUB_LOG=log)
    proc = subprocess.run(["bash"], input=prefix + '\necho "APPLY_SENTINEL_REACHED"\n',
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0
    assert "RKN_SWAP_RESULT=CHECK" in proc.stdout
    assert "APPLY_SENTINEL_REACHED" not in proc.stdout


# ── (c) шаг pipeline: нефатальный + payload + проба ────────────

class _Task:
    total_steps = 14

    def __init__(self):
        self.begun: list[int] = []
        self.logs: list[str] = []
        self.status = None

    def set_step(self, i, _s):
        self.begun.append(i)

    def add_log(self, line):
        self.logs.append(line)

    def finish(self, status, *_a):
        self.status = status


class _Req:
    whitelist_ips = "198.51.100.5"


def _patch_backend_ip(monkeypatch, value=BACKEND_IP):
    async def _ip():
        return value
    monkeypatch.setattr(pipeline, "get_backend_ip", _ip)


def test_step_rkn_watcher_runs_payload_and_logs_state(monkeypatch):
    _patch_backend_ip(monkeypatch)

    class _SSH:
        def __init__(self):
            self.script = ""

        async def run_script(self, script, task, **kw):
            self.script = script
            return 0

        async def get_output(self, cmd):
            return f"RKN_ACTIVE=1\nRKN_ENTRIES=4242\nRKN_LEGACY=0"

    ssh, task = _SSH(), _Task()
    asyncio.run(pipeline.step_rkn_watcher(ssh, task, _Req()))
    assert 4 in task.begun                                  # шаг 4
    assert rkn_watcher.RKN_COMMIT in ssh.script             # ушёл payload, а не старый clone
    assert BACKEND_IP in ssh.script
    assert any("активен" in ln and "4242" in ln for ln in task.logs)


def test_step_rkn_watcher_warns_when_legacy_left(monkeypatch):
    _patch_backend_ip(monkeypatch)

    class _SSH:
        async def run_script(self, *a, **k):
            return 0

        async def get_output(self, cmd):
            return "RKN_ACTIVE=1\nRKN_ENTRIES=10\nRKN_LEGACY=1"

    task = _Task()
    asyncio.run(pipeline.step_rkn_watcher(_SSH(), task, _Req()))
    assert any("артефакты" in ln for ln in task.logs)


def test_step_rkn_watcher_is_non_fatal(monkeypatch):
    _patch_backend_ip(monkeypatch)

    class _BoomSSH:
        async def run_script(self, *a, **k):
            raise RuntimeError("ssh is gone")

        async def get_output(self, *a, **k):
            raise RuntimeError("ssh is gone")

    task = _Task()
    # исключение наружу НЕ уходит: шаг нефатальный, деплой продолжается
    asyncio.run(pipeline.step_rkn_watcher(_BoomSSH(), task, _Req()))
    assert any("ПРЕДУПРЕЖДЕНИЕ" in ln for ln in task.logs)


def test_step_label_and_skip_names_renamed():
    assert STEP_LABELS[3] == "RKN Watcher (защита от сканеров)"
    src = open(pipeline.__file__, encoding="utf-8").read()
    # в наборе управляемых компонентов — новое имя, прежнего там нет
    assert '"rkn_watcher", "test_tools"' in src
    managed = src.split("managed = {")[1].split("}")[0]
    assert '"trafficguard"' not in managed
    assert '"rkn_watcher"' in managed


class _SSH:
    """Заглушка SSHSession для run_pipeline (шаг 1 читает os-release)."""

    def __init__(self, *a, **k):
        pass

    async def connect(self, *a, **k):
        pass

    async def close(self):
        pass

    async def get_output(self, *a, **k):
        return "Ubuntu 22.04"

    async def run_script(self, *a, **k):
        return 0

    async def run(self, *a, **k):
        return 0


def test_pipeline_skips_rkn_watcher_component(monkeypatch):
    """skip_components: и новое имя, и legacy `trafficguard` глушат шаг 4."""
    called: list[str] = []

    def rec(name):
        async def f(*a, **k):
            called.append(name)
        return f

    async def _dualport(ssh, task, *a, **k):
        return ssh

    async def _ip():
        return ""

    monkeypatch.setattr(pipeline, "SSHSession", _SSH)
    monkeypatch.setattr(pipeline, "step_ssh_dualport_verify", _dualport)
    monkeypatch.setattr(pipeline, "get_backend_ip", _ip)
    for name in ("step_rkn_watcher", "step_node_accelerator", "step_test_tools",
                 "step_system_optimize", "step_ssl", "step_remnanode",
                 "step_sni_masking", "step_warp", "step_psiphon", "step_certbot_ssl"):
        monkeypatch.setattr(pipeline, name, rec(name))

    for skip in (["rkn_watcher"], ["trafficguard"]):
        called.clear()
        req = DeployRequest(
            mode="remnanode", ip="1.2.3.4", ssh_password="pw",
            domain="node1.example.com", email="a@b.co", cert_provider="letsencrypt",
            remnanode_token="tok", open_ports="80,443", create_in_remnawave=False,
            country_code="US", install_warp=False, change_ssh_port=False,
            skip_components=skip, install_components=[],
        )
        task = _Task()
        asyncio.run(pipeline.run_pipeline(req, task))
        assert "step_rkn_watcher" not in called, f"{skip} должен был пропустить шаг 4"
        assert 4 in task.begun        # шаг всё равно начат (прогресс-бар двигается)
        assert any("RKN Watcher" in ln and "Пропущено" in ln for ln in task.logs)


# ── (d) метрики /api/stats/node ────────────────────────────────

def test_parse_state_markers_and_garbage():
    assert rkn_watcher.parse_state("") == (0, 0, 0)
    assert rkn_watcher.parse_state("Ubuntu 22.04\nmotd") == (0, 0, 0)
    assert rkn_watcher.parse_state(
        "RKN_ACTIVE=1\nRKN_ENTRIES=1234\nRKN_LEGACY=0"
    ) == (1, 1234, 0)
    assert rkn_watcher.parse_state("RKN_ACTIVE=0\nRKN_ENTRIES=0\nRKN_LEGACY=1") == (0, 0, 1)


def test_stats_probe_measures_both_sets_of_resources():
    probe = rkn_watcher.state_probe()
    for name in rkn_watcher.RKN_SETS:
        assert name in probe
    assert "rkn-watcher-update.timer" in probe and "TSPUBLOCK" in probe
    assert "/usr/local/bin/rknpidor" in probe and "/opt/TrafficGuard-auto" in probe


def test_stats_node_reports_rkn_watcher_fields(monkeypatch):
    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def get_output(self, command):
            if "RKN_ENTRIES" in command:
                return "RKN_ACTIVE=1\nRKN_ENTRIES=98765\nRKN_LEGACY=0"
            return ""

        async def close(self):
            pass

    monkeypatch.setattr(stats_api, "SSHSession", FakeSSH)
    r = client.post("/api/stats/node", headers=_auth(),
                    json={"ip": "1.2.3.4", "ssh_password": "pw", "ssh_port": 22})
    assert r.status_code == 200
    sec = r.json()["securityStats"]
    assert sec["rknWatcherActive"] == 1
    assert sec["rknWatcherEntries"] == 98765
    assert sec["rknWatcherLegacy"] == 0
    assert "trafficGuardActive" not in sec      # поле убрано (переход на новые имена)
    assert "fail2banActive" in sec


def test_stats_node_degrades_when_probe_is_junk(monkeypatch):
    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self, *a, **k):
            pass

        async def get_output(self, command):
            return "bash: ipset: command not found"

        async def close(self):
            pass

    monkeypatch.setattr(stats_api, "SSHSession", FakeSSH)
    r = client.post("/api/stats/node", headers=_auth(),
                    json={"ip": "1.2.3.4", "ssh_password": "pw"})
    assert r.status_code == 200
    sec = r.json()["securityStats"]
    # нода без RKN Watcher → нули, никаких 500
    assert (sec["rknWatcherActive"], sec["rknWatcherEntries"], sec["rknWatcherLegacy"]) == (0, 0, 0)


# ── (e) legacy-имена ──────────────────────────────────────────

def _remnanode(**over) -> DeployRequest:
    base = dict(
        mode="remnanode", ip="1.2.3.4", ssh_password="pw",
        domain="node1.example.com", email="a@b.co", cert_provider="letsencrypt",
        remnanode_token="tok", open_ports="80,443", create_in_remnawave=False,
        country_code="US", install_warp=False, change_ssh_port=False,
    )
    base.update(over)
    return DeployRequest(**base)


def test_install_rkn_watcher_defaults_to_true():
    assert _remnanode().install_rkn_watcher is True
    assert _remnanode(install_rkn_watcher=False).install_rkn_watcher is False


def test_legacy_install_trafficguard_maps_to_new_field():
    """Сохранённые карточки деплоя (localStorage) ещё шлют install_trafficguard."""
    assert _remnanode(install_trafficguard=False).install_rkn_watcher is False
    assert _remnanode(install_trafficguard=True).install_rkn_watcher is True
    # новое поле приоритетнее legacy
    r = _remnanode(install_trafficguard=True, install_rkn_watcher=False)
    assert r.install_rkn_watcher is False


def test_legacy_trafficguard_component_id_maps_to_rkn_watcher():
    r = _remnanode(skip_components=["trafficguard", "ssl"])
    assert r.skip_components == ["rkn_watcher", "ssl"]
    r2 = _remnanode(install_components=["trafficguard"])
    assert r2.install_components == ["rkn_watcher"]
    # прочие id не трогаем
    assert _remnanode(skip_components=["warp"]).skip_components == ["warp"]


def test_node_ops_component_renamed_with_legacy_alias():
    assert "rkn_watcher" in node_ops.Component.__args__
    assert "trafficguard" not in node_ops.Component.__args__
    assert node_ops._COMPONENT_LABEL["rkn_watcher"] == "RKN Watcher"
    assert set(node_ops._DETECT_SCRIPTS) == set(node_ops.Component.__args__)
    assert set(node_ops._UNINSTALL_SCRIPTS) == set(node_ops.Component.__args__)
    # детект/удаление смотрят на НОВЫЙ инструмент, а не на прежний клон
    detect = node_ops._DETECT_SCRIPTS["rkn_watcher"]("node1.example.com")
    assert "test -d /opt/rkn-watcher" in detect and "TSPUIPS" in detect
    uninstall = node_ops._UNINSTALL_SCRIPTS["rkn_watcher"](None)
    assert "rkn-watcher.sh uninstall" in uninstall
    assert "RKN_ASSUME_YES=1" in uninstall
    assert "/opt/rkn-watcher-extra/rkn-watcher-extra.sh uninstall" in uninstall
    assert "ipset destroy" in uninstall


def test_node_ops_accepts_legacy_component_id():
    """POST /api/node/step со старым component=trafficguard не должен падать 422."""
    req = node_ops.NodeOpRequest(
        mode="haproxy", ip="1.2.3.4", ssh_password="pw", open_ports="443",
        haproxy_dest_ip="5.6.7.8", component="trafficguard", action="reinstall",
    )
    assert req.component == "rkn_watcher"
    req2 = node_ops.NodeOpRequest(
        mode="haproxy", ip="1.2.3.4", ssh_password="pw", open_ports="443",
        haproxy_dest_ip="5.6.7.8", component="rkn_watcher", action="uninstall",
    )
    assert req2.component == "rkn_watcher"


# ── (f) доп. компонент: swap_set не обнуляет живой набор ───────
# ⚠️ Реальный баг с живой ноды: `ipset restore` берёт ИМЯ НАБОРА ИЗ ФАЙЛА, поэтому прежний
# swap_set заливал clean-файл (строки `add EXTRA_ANTISCAN …`) прямо в ЖИВОЙ набор, временный
# оставался пустым, а `ipset swap` уносил пустышку в живой → набор обнулялся (155/2784 → 0).
# Тесты гоняют РЕАЛЬНЫЙ текст функций из EXTRA_SCRIPT на мини-ipset'е.

_IPSET_STUB = r'''#!/usr/bin/env bash
# Мини-ipset: состояние набора = файл $IPSET_STATE/<имя>, по строке на элемент.
echo "ipset $*" >> "$STUB_LOG"
cmd="$1"; shift || true
case "$cmd" in
    create)
        [ -f "$IPSET_STATE/$1" ] || : > "$IPSET_STATE/$1"
        ;;
    destroy)
        rm -f "$IPSET_STATE/$1"
        ;;
    add)
        grep -qx "$2" "$IPSET_STATE/$1" 2>/dev/null || echo "$2" >> "$IPSET_STATE/$1"
        ;;
    restore)
        while read -r line; do
            echo "restore: $line" >> "$STUB_LOG"
            arr=($line)
            if [ "${arr[0]:-}" = "add" ] && [ -n "${arr[1]:-}" ] && [ -n "${arr[2]:-}" ]; then
                grep -qx "${arr[2]}" "$IPSET_STATE/${arr[1]}" 2>/dev/null \
                    || echo "${arr[2]}" >> "$IPSET_STATE/${arr[1]}"
            fi
        done
        ;;
    list)
        if [ "$1" = "-n" ]; then ls "$IPSET_STATE" 2>/dev/null; exit 0; fi
        [ -f "$IPSET_STATE/$1" ] || exit 1
        echo "Name: $1"
        echo "Number of entries: $(wc -l < "$IPSET_STATE/$1" | tr -d ' ')"
        ;;
    swap)
        tmpfile=$(mktemp "$SANDBOX/swap.XXXXXX")
        cp "$IPSET_STATE/$1" "$tmpfile"
        cp "$IPSET_STATE/$2" "$IPSET_STATE/$1"
        cp "$tmpfile" "$IPSET_STATE/$2"
        rm -f "$tmpfile"
        ;;
    save)
        if [ -n "${1:-}" ] && [ -f "$IPSET_STATE/$1" ]; then
            while read -r e; do echo "add $1 $e"; done < "$IPSET_STATE/$1"
        fi
        ;;
esac
exit 0
'''


def _extra_function(name: str) -> str:
    """Вырезает функцию из РЕАЛЬНОГО EXTRA_SCRIPT — тестируем поставляемый код, не копию."""
    src = rkn_watcher.EXTRA_SCRIPT
    start = src.index(f"{name}() {{")
    first_line_end = src.index("\n", start)
    if src[start:first_line_end].rstrip().endswith("}"):     # однострочная функция
        return src[start:first_line_end]
    return src[start:src.index("\n}\n", start) + 3]


def _run_swap(tmp_path, live_entries, clean_body, expect_rc=0) -> tuple[str, str, str]:
    """Прогон swap_set из EXTRA_SCRIPT на мини-ipset'е. Возвращает (stdout, живой набор, лог)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "sets"
    state.mkdir()
    log = tmp_path / "calls.log"
    stub = bin_dir / "ipset"
    stub.write_text(_IPSET_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    live = state / "EXTRA_ANTISCAN"
    live.write_text("\n".join(live_entries) + "\n", encoding="utf-8")
    clean = tmp_path / "antiscanner.clean"
    clean.write_text(clean_body, encoding="utf-8")

    script = "\n".join([
        _extra_function("ensure_set"),
        _extra_function("set_count"),
        _extra_function("swap_set"),
        f'swap_set EXTRA_ANTISCAN "{clean}"',
    ])
    # страховка: тестируем именно ту реализацию, что уходит на ноду
    assert "sed \"s/^add [^ ]*/add ${tmp}/\"" in script
    proc = subprocess.run(["bash"], input=script, capture_output=True, text=True,
                          env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                                   SANDBOX=str(tmp_path), IPSET_STATE=str(state),
                                   STUB_LOG=str(log)),
                          timeout=60)
    assert proc.returncode == expect_rc, proc.stderr
    return proc.stdout.strip(), str(live), str(log)


def test_swap_set_fills_temp_set_and_never_touches_live_one(tmp_path):
    out, live_path, log_path = _run_swap(
        tmp_path,
        live_entries=["10.0.0.0/8", "192.168.0.0/16"],
        clean_body=("add EXTRA_ANTISCAN 1.1.1.0/24 -exist\n"
                    "add EXTRA_ANTISCAN 2.2.2.0/24 -exist\n"),
    )
    assert out == "2"          # вернулось число применённых записей
    log = open(log_path, encoding="utf-8").read()
    # имя набора в файле переписано на временный → restore не наполняет живой набор
    assert "restore: add EXTRA_ANTISCAN_tmp 1.1.1.0/24 -exist" in log
    assert "restore: add EXTRA_ANTISCAN 1.1.1.0/24 -exist" not in log
    assert "ipset swap EXTRA_ANTISCAN_tmp EXTRA_ANTISCAN" in log
    # живой набор получил НОВЫЕ записи (а не пустоту)
    assert sorted(open(live_path, encoding="utf-8").read().split()) == ["1.1.1.0/24", "2.2.2.0/24"]


def test_swap_set_keeps_live_set_when_temp_is_empty(tmp_path):
    """Пустой временный набор → swap не вызывается, живой набор не тронут (guard)."""
    out, live_path, log_path = _run_swap(
        tmp_path,
        live_entries=["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"],
        clean_body="# пусто (например, список не распарсился)\n",
        expect_rc=1,          # swap_set вернул ошибку — так и должно быть
    )
    assert out == ""                       # swap_set вернул ошибку
    assert "ipset swap" not in open(log_path, encoding="utf-8").read()
    assert len(open(live_path, encoding="utf-8").read().split()) == 3   # живой набор цел
