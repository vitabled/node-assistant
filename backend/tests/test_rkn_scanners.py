"""RKNscanner: стор адресов сканеров, проба/применение на ноде, роуты, логирование на нодах.

Проверяется:
  (a) стор per-account: merge не дублирует адреса и обновляет lastSeen/hits/nodes;
  (b) проба: парсер достаёт IP+порт из строк лога, скрипт читает journalctl по префиксу RKNSCAN:;
  (c) применение: ipset RKN_SCANNERS_V4 + цепочка RKN_SCANNERS с RETURN для приватных сетей;
  (d) роуты /api/rkn-scanners (GET/save/collect/sync/DELETE) с подставным SSH;
  (e) доп. компонент шага 4 логирует попытки с префиксом RKNSCAN: перед своим DROP и
      НЕ добавляет DROP наборам апстрима (TSPUIPS/GOVIPS).
"""
import os
import re
import subprocess
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts, rkn_scanners, rkn_watcher

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"rknsc-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _isolate(tmp_path, monkeypatch, aid: str = "acc-rkn"):
    """Стор в свой каталог — как test_f2b_list: `accounts.data_dir` читается в момент вызова."""
    monkeypatch.setattr(accounts, "data_dir",
                        lambda a=None, instance_id=None: tmp_path / "accounts" / (a or aid))
    return aid


# ── (a) стор ───────────────────────────────────────────────────

def test_validate_entry():
    assert rkn_scanners.validate_entry("203.0.113.10") == "203.0.113.10"
    assert rkn_scanners.validate_entry("198.51.100.9/24") == "198.51.100.0/24"   # нормализация
    assert rkn_scanners.validate_entry("203.0.113.10/32") == "203.0.113.10"      # /32 — тот же адрес
    assert rkn_scanners.validate_entry(" 10.0.0.1 ") == "10.0.0.1"
    for bad in ("", "example.com", "999.1.1.1", "10.0.0.0/33", "1.2.3.4 5.6.7.8"):
        try:
            rkn_scanners.validate_entry(bad)
            raise AssertionError(f"{bad} должен был упасть")
        except ValueError:
            pass


def test_merge_dedups_and_updates_lastseen_hits_nodes(tmp_path, monkeypatch):
    aid = _isolate(tmp_path, monkeypatch)
    first = rkn_scanners.merge(
        [{"ip": "198.51.100.8", "port": 22, "chain": "antiscan", "hits": 3}], "203.0.113.5", aid)
    assert first == {"added": 1, "updated": 0, "hitsAdded": 3, "total": 1}
    saved = rkn_scanners.load(aid)
    assert saved[0]["ip"] == "198.51.100.8" and saved[0]["hits"] == 3
    assert saved[0]["nodes"] == ["203.0.113.5"] and saved[0]["port"] == 22
    assert saved[0]["firstSeen"] and saved[0]["firstSeen"] == saved[0]["lastSeen"]

    second = rkn_scanners.merge(
        [{"ip": "198.51.100.8", "port": 8443, "chain": "tspu", "hits": 4},
         {"ip": "192.0.2.77", "hits": 1}], "198.51.100.1", aid)
    assert second["added"] == 1 and second["updated"] == 1
    entries = rkn_scanners.load(aid)
    assert len(entries) == 2                                   # адрес НЕ продублирован
    merged = next(e for e in entries if e["ip"] == "198.51.100.8")
    assert merged["hits"] == 7                                 # 3 + 4
    assert merged["nodes"] == ["203.0.113.5", "198.51.100.1"]  # обе ноды
    assert merged["port"] == 8443 and merged["chain"] == "tspu"
    assert merged["firstSeen"] <= merged["lastSeen"] == rkn_scanners._now()


def test_merge_ignores_garbage_and_keeps_updatedat_on_noop(tmp_path, monkeypatch):
    aid = _isolate(tmp_path, monkeypatch)
    rkn_scanners.merge([{"ip": "198.51.100.8", "hits": 1}], "203.0.113.5", aid)
    stamp = rkn_scanners.load_document(aid)["updatedAt"]
    assert rkn_scanners.merge([{"ip": "not-an-ip"}, "мусор"], "203.0.113.5", aid)["added"] == 0
    assert rkn_scanners.load_document(aid)["updatedAt"] == stamp


def test_save_keeps_history_and_marks_manual(tmp_path, monkeypatch):
    aid = _isolate(tmp_path, monkeypatch)
    rkn_scanners.merge([{"ip": "198.51.100.8", "port": 22, "chain": "antiscan", "hits": 5}],
                       "203.0.113.5", aid)
    before = rkn_scanners.load(aid)[0]

    saved = rkn_scanners.save(["198.51.100.8", "192.0.2.9/32"], aid)
    assert [e["ip"] for e in saved] == ["198.51.100.8", "192.0.2.9"]
    known = saved[0]
    assert known["firstSeen"] == before["firstSeen"]      # история наблюдений сохранена
    assert known["hits"] == 5 and known["nodes"] == ["203.0.113.5"]
    assert known["port"] == 22 and known["source"] == "probe"
    assert saved[1]["source"] == "manual"                 # новый адрес пришёл из панели

    # снятый из списка адрес исчезает
    assert [e["ip"] for e in rkn_scanners.save(["198.51.100.8"], aid)] == ["198.51.100.8"]

    try:
        rkn_scanners.save(["198.51.100.8", "bad!!"], aid)
        raise AssertionError("мусор должен был упасть")
    except ValueError:
        pass


def test_save_accepts_screen_entry_shape_with_numeric_timestamps():
    """Экран присылает свою же таблицу с `firstSeen/lastSeen = Date.now()` — метки приводим к ISO."""
    auth = _auth()
    now_ms = 1758624000000
    r = client.post("/api/rkn-scanners/save", headers=auth, json={"entries": [
        {"ip": "198.51.100.8", "port": 22, "chain": "antiscan", "hits": 1,
         "firstSeen": now_ms, "lastSeen": now_ms, "nodes": [], "source": "manual"},
        "203.0.113.9",
    ]})
    assert r.status_code == 200, r.text
    entries = {e["ip"]: e for e in r.json()["entries"]}
    assert entries["198.51.100.8"]["firstSeen"] == rkn_scanners._as_stamp(now_ms)
    assert entries["198.51.100.8"]["firstSeen"].endswith("Z")
    assert entries["198.51.100.8"]["source"] == "manual"
    assert entries["203.0.113.9"]["source"] == "manual"


def test_timestamp_normalisation():
    # 1758624000000 ms == 1758624000 s == 2025-09-23T10:40:00Z
    assert rkn_scanners._as_stamp(1758624000000) == "2025-09-23T10:40:00Z"
    assert rkn_scanners._as_stamp(1758624000) == "2025-09-23T10:40:00Z"
    assert rkn_scanners._as_stamp("2025-09-23T13:40:00+03:00") == "2025-09-23T10:40:00Z"
    assert rkn_scanners._as_stamp("2025-09-23T10:40:00Z") == "2025-09-23T10:40:00Z"
    assert rkn_scanners._as_stamp("") == "" and rkn_scanners._as_stamp(None) == ""


def test_load_tolerates_hand_edited_file(tmp_path, monkeypatch):
    aid = _isolate(tmp_path, monkeypatch)
    path = tmp_path / "accounts" / aid
    path.mkdir(parents=True)
    (path / rkn_scanners.STORAGE_FILE).write_text(
        '["198.51.100.8", "мусор", {"ip": "203.0.113.9", "hits": "7", "nodes": ["x", "1.1.1.1"]}, '
        '{"ip": "203.0.113.9"}]', encoding="utf-8")
    entries = rkn_scanners.load(aid)
    assert [e["ip"] for e in entries] == ["198.51.100.8", "203.0.113.9"]
    assert entries[1]["hits"] == 7 and entries[1]["nodes"] == ["1.1.1.1"]


# ── (b) проба ──────────────────────────────────────────────────

_ISSUE = ('Sep 23 12:00:00 node kernel: RKNSCAN:antiscan IN=eth0 OUT= MAC=aa:bb SRC=198.51.100.8 '
          'DST=203.0.113.5 LEN=60 PROTO=TCP SPT=44321 DPT=22 SYN URGP=0')
_TSPU = ('Sep 23 12:00:01 node kernel: RKNSCAN:tspu IN=eth0 SRC=203.0.113.7 SPT=51000 DPT=2222 SYN')


def test_parse_log_line_extracts_source_ip_and_port():
    parsed = rkn_scanners.parse_log_line(_ISSUE)
    assert parsed == {"ip": "198.51.100.8", "port": 44321, "chain": "antiscan"}
    assert rkn_scanners.parse_log_line(_TSPU)["chain"] == "tspu"
    # строки без префикса/без SRC — не наш лог
    assert rkn_scanners.parse_log_line("Sep 23 12:00 kernel: RKN-EXTRA: SRC=1.2.3.4") is None
    assert rkn_scanners.parse_log_line("RKNSCAN:antiscan IN=eth0 PROTO=TCP") is None
    assert rkn_scanners.parse_log_line("") is None


def test_parse_probe_output_reads_tsv_and_raw_lines():
    raw = "198.51.100.8\t44321\tantiscan\t3\n203.0.113.7\t51000\ttspu\t1\n\n"
    assert rkn_scanners.parse_probe_output(raw) == [
        {"ip": "198.51.100.8", "port": 44321, "chain": "antiscan", "hits": 3},
        {"ip": "203.0.113.7", "port": 51000, "chain": "tspu", "hits": 1},
    ]
    # вывод другого формата (сырые строки journalctl) тоже разбирается
    parsed = rkn_scanners.parse_probe_output(f"{_ISSUE}\n{_TSPU}\n")
    assert [(p["ip"], p["port"]) for p in parsed] == [("198.51.100.8", 44321),
                                                      ("203.0.113.7", 51000)]
    assert rkn_scanners.parse_probe_output("") == []
    assert rkn_scanners.parse_probe_output("[rkn-scanners] journalctl не найден") == []


def test_probe_script_end_to_end_on_fake_journalctl(tmp_path):
    """Реальный прогон скрипта пробы (bash+awk) на подставном journalctl: без SSH и без ipset."""
    lines = "\n".join([
        _ISSUE, _ISSUE, _TSPU,
        "Sep 23 12:00:02 node kernel: RKN-EXTRA: IN=eth0 SRC=9.9.9.9 DPT=22",   # чужой префикс — прочь
        "Sep 23 12:00:03 node kernel: RKNSCAN:govnet IN=eth0 SRC=192.0.2.5 DPT=443",  # нет SPT
    ])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "journalctl"
    stub.write_text("#!/usr/bin/env bash\ncat <<'JRN'\n" + lines + "\nJRN\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    proc = subprocess.run(["bash"], input=rkn_scanners.probe_script(24), capture_output=True,
                          text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    # вывод пробы отсортирован по адресу, hits посчитаны на узле
    assert rkn_scanners.parse_probe_output(proc.stdout) == [
        {"ip": "192.0.2.5", "port": None, "chain": "govnet", "hits": 1},
        {"ip": "198.51.100.8", "port": 44321, "chain": "antiscan", "hits": 2},
        {"ip": "203.0.113.7", "port": 51000, "chain": "tspu", "hits": 1},
    ]


def test_probe_script_survives_empty_log(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "journalctl"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    proc = subprocess.run(["bash"], input=rkn_scanners.probe_script(24), capture_output=True,
                          text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert rkn_scanners.parse_probe_output(proc.stdout) == []


def test_probe_script_reads_journalctl_for_rknscan_prefix():
    script = rkn_scanners.probe_script(48)
    assert 'journalctl -k --since "-${HOURS}h" --no-pager' in script
    assert "HOURS=48" in script
    assert "grep -a 'RKNSCAN:'" in script
    assert "SPT=[0-9][0-9]*" in script and "SRC=[0-9][0-9.]*" in script
    assert rkn_scanners.MAX_SINCE_HOURS == 720
    for bad in (0, -1, rkn_scanners.MAX_SINCE_HOURS + 1, "часы"):
        try:
            rkn_scanners.probe_script(bad)
            raise AssertionError(f"{bad} должен был упасть")
        except ValueError:
            pass
    assert subprocess.run(["bash", "-n"], input=script, capture_output=True,
                          text=True).returncode == 0


# ── (c) применение ─────────────────────────────────────────────

def test_apply_script_fills_ipset_and_returns_private_before_drop():
    script = rkn_scanners.apply_script(["198.51.100.8", "192.0.2.0/24"])
    assert 'RKNSC_SET="RKN_SCANNERS_V4"' in script
    assert "hash:net family inet maxelem 100000" in script
    assert "ipset restore -exist" in script and "ipset flush" in script
    assert "RKN_SCANNERS_V4" in script and "RKN_SCANNERS" in script
    # RETURN для установленных соединений и приватных сетей — ДО правила с набором
    established = script.index("--ctstate ESTABLISHED,RELATED -j RETURN")
    drop = script.index('-p tcp --syn -m set --match-set "$RKNSC_SET" src -j DROP')
    for net in rkn_scanners.SCANNERS_PRIVATE:
        at = script.index(f'-s "$_w" -j RETURN', 0)      # правило приватных сетей
        assert at < drop
        assert net in script
    assert established < drop
    # переход в INPUT и сохранение состояния
    assert 'iptables -I INPUT 1 -j "$RKNSC_CHAIN"' in script
    assert 'ipset save "$RKNSC_SET"' in script
    assert "@reboot" in script and "rkn-scanners-restore" in script
    assert "RKN_SCANNERS_RESULT=OK" in script and "RKN_SCANNERS_COUNT=" in script
    # адреса в списке и никаких незаменённых плейсхолдеров
    assert "198.51.100.8" in script and "192.0.2.0/24" in script
    assert "__ENTRIES__" not in script and "__RESTORE__" not in script
    assert "__" not in script.replace("__main__", "")


def test_apply_script_awk_builds_restore_lines(tmp_path):
    """Строка `add <set> <cidr> -exist` из скрипта — реальный прогон awk на файле списка."""
    script = rkn_scanners.apply_script(["198.51.100.8", "192.0.2.0/24"])
    awk_line = next(line for line in script.splitlines() if "awk -v s=" in line)
    entries = tmp_path / "entries.list"
    entries.write_text("198.51.100.8\n192.0.2.0/24\n\n", encoding="utf-8")
    cmd = (awk_line.rstrip().rstrip("\\").rstrip()
           .replace('"$RKNSC_SET"', '"RKN_SCANNERS_V4"')
           .replace('"$RKNSC_ENTRIES"', f'"{entries}"'))
    proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "add RKN_SCANNERS_V4 198.51.100.8 -exist",
        "add RKN_SCANNERS_V4 192.0.2.0/24 -exist",
    ]


def test_apply_script_is_valid_bash_and_rejects_garbage():
    script = rkn_scanners.apply_script(["198.51.100.8"])
    assert subprocess.run(["bash", "-n"], input=script, capture_output=True,
                          text=True).returncode == 0
    restore = rkn_scanners.restore_script()
    assert subprocess.run(["sh", "-n"], input=restore, capture_output=True,
                          text=True).returncode == 0
    assert rkn_scanners.STATE_DIR in restore
    for bad in (["1.2.3.4", "не-адрес"], ["1.2.3.4; rm -rf /"]):
        try:
            rkn_scanners.apply_script(bad)
            raise AssertionError(f"{bad} должен был упасть")
        except ValueError:
            pass


def test_parse_apply_output_markers():
    assert rkn_scanners.parse_apply_output("RKN_SCANNERS_COUNT=42\n") == 42
    assert rkn_scanners.parse_apply_output("нет маркера") is None
    assert rkn_scanners.apply_failed("RKN_SCANNERS_RESULT=CHECK") is True
    assert rkn_scanners.apply_failed("RKN_SCANNERS_RESULT=OK") is False


# ── (d) роуты ──────────────────────────────────────────────────

_PROBE_OUTPUT = "198.51.100.8\t44321\tantiscan\t3\n203.0.113.7\t51000\tgovnet\t1\n"


class _FakeSSH:
    commands: list[str] = []
    scripts: list[str] = []
    output = _PROBE_OUTPUT
    script_output = "RKN_SCANNERS_RESULT=OK\nRKN_SCANNERS_COUNT=2\n"
    fail_connect = False

    def __init__(self, *args, **kwargs):
        self.host = args[0]

    async def connect(self):
        if type(self).fail_connect:
            raise OSError("connection refused")

    async def close(self):
        return None

    async def get_output(self, command):
        type(self).commands.append(command)
        return type(self).output

    async def get_script_output(self, script):
        type(self).scripts.append(script)
        return type(self).script_output


def _fake_node_ssh(monkeypatch):
    _FakeSSH.commands = []
    _FakeSSH.scripts = []
    _FakeSSH.output = _PROBE_OUTPUT
    _FakeSSH.fail_connect = False
    monkeypatch.setattr(rkn_scanners, "SSHSession", _FakeSSH)
    return _FakeSSH


def test_routes_save_get_delete_roundtrip():
    auth = _auth()
    assert client.get("/api/rkn-scanners", headers=auth).json() == {
        "entries": [], "total": 0, "updatedAt": ""}
    r = client.post("/api/rkn-scanners/save", headers=auth,
                    json={"entries": ["1.2.3.4", "bad!!"]})
    assert r.status_code == 422
    r = client.post("/api/rkn-scanners/save", headers=auth,
                    json={"entries": ["1.2.3.4", "192.168.0.0/16"]})
    assert r.status_code == 200 and r.json()["total"] == 2
    got = client.get("/api/rkn-scanners", headers=auth).json()
    assert [e["ip"] for e in got["entries"]] == ["1.2.3.4", "192.168.0.0/16"]
    assert got["total"] == 2 and got["updatedAt"]
    # повторная правка принимает и записи в том виде, в каком их отдал GET
    r = client.post("/api/rkn-scanners/save", headers=auth, json={"entries": got["entries"]})
    assert r.status_code == 200 and r.json()["total"] == 2
    assert client.delete("/api/rkn-scanners", headers=auth).json()["total"] == 0
    assert client.get("/api/rkn-scanners", headers=auth).json()["total"] == 0


def test_collect_route_uses_node_probe(monkeypatch):
    fake = _fake_node_ssh(monkeypatch)
    auth = _auth()
    r = client.post("/api/rkn-scanners/collect", headers=auth, json={
        "ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw", "since_hours": 12})
    assert r.status_code == 200
    body = r.json()
    assert body["ip"] == "203.0.113.20" and body["total"] == 2 and body["sinceHours"] == 12
    assert body["scanners"][0]["ip"] == "198.51.100.8" and body["scanners"][0]["hits"] == 3
    assert "HOURS=12" in fake.commands[0] and "journalctl -k" in fake.commands[0]
    # сбор НЕ трогает центральный список
    assert client.get("/api/rkn-scanners", headers=auth).json()["total"] == 0


def test_collect_route_reports_node_error(monkeypatch):
    _fake_node_ssh(monkeypatch)
    _FakeSSH.fail_connect = True
    auth = _auth()
    r = client.post("/api/rkn-scanners/collect", headers=auth, json={
        "ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw"})
    assert r.status_code == 502 and "203.0.113.20" in r.json()["detail"]


def test_node_request_validates_ip_and_user():
    auth = _auth()
    for payload in ({"ip": "not-an-ip", "ssh_user": "root", "ssh_password": "pw"},
                    {"ip": "203.0.113.20", "ssh_user": "", "ssh_password": "pw"},
                    {"ip": "203.0.113.20", "ssh_user": "root"},          # нет кред
                    {"ip": "203.0.113.20", "ssh_user": "root",
                     "ssh_password": "pw", "since_hours": 0}):
        r = client.post("/api/rkn-scanners/collect", headers=auth, json=payload)
        assert r.status_code == 422, payload


def test_sync_merges_collected_and_applies_back(monkeypatch):
    fake = _fake_node_ssh(monkeypatch)
    auth = _auth()
    r = client.post("/api/rkn-scanners/sync", headers=auth, json={
        "nodes": [{"ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw",
                   "apply": True},
                  {"ip": "203.0.113.21", "ssh_user": "root", "ssh_password": "pw"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["merged"]["added"] == 2
    assert body["results"][0]["collected"] == 2 and body["results"][0]["merged"]["added"] == 2
    # на второй ноде оба адреса уже в списке: они не дублируются, а обновляются
    assert body["results"][1]["merged"] == {"added": 0, "updated": 2}
    assert body["results"][0]["applied"] == 2 and body["results"][0]["inSet"] == 2
    assert body["results"][0]["ok"] is True
    assert body["collected"] == 4 and body["new"] == 2               # плоские итоги для экрана
    assert "applied" not in body["results"][1]                       # apply не просили
    assert len(fake.scripts) == 1
    assert "RKN_SCANNERS_V4" in fake.scripts[0]
    assert "198.51.100.8" in fake.scripts[0] and "203.0.113.7" in fake.scripts[0]
    # ноды собрались в nodes у адреса
    entry = next(e for e in client.get("/api/rkn-scanners", headers=auth).json()["entries"]
                 if e["ip"] == "198.51.100.8")
    assert entry["nodes"] == ["203.0.113.20", "203.0.113.21"]


def test_sync_without_merge_leaves_central_list_alone(monkeypatch):
    _fake_node_ssh(monkeypatch)
    auth = _auth()
    body = client.post("/api/rkn-scanners/sync", headers=auth, json={
        "nodes": [{"ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw"}],
        "merge_collected": False,
    }).json()
    assert body["total"] == 0 and body["results"][0]["collected"] == 2
    assert body["merged"] == {"added": 0, "updated": 0, "hitsAdded": 0}


def test_sync_accepts_screen_payload_shape(monkeypatch):
    """Панель шлёт и общий `apply`, и `sinceHours` рядом с `since_hours` — оба должны работать."""
    fake = _fake_node_ssh(monkeypatch)
    auth = _auth()
    r = client.post("/api/rkn-scanners/sync", headers=auth, json={
        "nodes": [{"ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw",
                   "since_hours": 6, "sinceHours": 6, "collect": True, "apply": True}],
        "apply": True,
        "merge_collected": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert "HOURS=6" in fake.commands[0]
    assert body["results"][0]["applied"] == 2 and len(fake.scripts) == 1


def test_sync_top_level_apply_flag_is_honoured(monkeypatch):
    """Общий флаг раздачи (словесный контракт раздела) работает без флага на ноде."""
    fake = _fake_node_ssh(monkeypatch)
    auth = _auth()
    body = client.post("/api/rkn-scanners/sync", headers=auth, json={
        "nodes": [{"ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw"}],
        "apply": True,
    }).json()
    assert body["results"][0]["applied"] == 2 and len(fake.scripts) == 1


def test_sync_reports_per_node_failure_and_keeps_going(monkeypatch):
    _fake_node_ssh(monkeypatch)
    _FakeSSH.fail_connect = True
    auth = _auth()
    body = client.post("/api/rkn-scanners/sync", headers=auth, json={
        "nodes": [{"ip": "203.0.113.20", "ssh_user": "root", "ssh_password": "pw"}],
    }).json()
    assert body["results"][0]["ok"] is False and "refused" in body["results"][0]["error"]


# ── (e) логирование на нодах (доп. компонент шага 4) ───────────

def _payload() -> str:
    return rkn_watcher.build_rkn_watcher_script("203.0.113.9", "198.51.100.5")


def test_extra_chain_logs_rknscan_before_its_drop():
    payload = _payload()
    limit = "--limit 30/min --limit-burst 60"
    assert payload.count(limit) == 4                     # по правилу на каждый набор
    for prefix in ("RKNSCAN:antiscan ", "RKNSCAN:govnet ", "RKNSCAN:tspu ", "RKNSCAN:govips "):
        assert f'-j LOG --log-prefix "{prefix}"' in payload
    # LOG стоит ПЕРЕД соответствующим DROP, иначе пакет до лога не доходит
    assert payload.index('RKNSCAN:antiscan') < payload.index('printf -- "$rule" "$SET_SCAN"')
    assert payload.index('RKNSCAN:govnet') < payload.index('printf -- "$rule" "$SET_GOV"')
    # цепочка чистится на каждом прогоне → повторный apply не плодит дубли правил
    assert payload.index('iptables -F "$CHAIN"') < payload.index('RKNSCAN:antiscan')


def test_upstream_sets_are_logged_without_drop():
    payload = _payload()
    for set_name, prefix in (("TSPUIPS", "RKNSCAN:tspu "), ("GOVIPS", "RKNSCAN:govips ")):
        rule = next(line for line in payload.splitlines() if prefix in line)
        assert "-j LOG" in rule and "-j DROP" not in rule          # семантику апстрима не меняем
        assert '--match-set "%s" src' % set_name in payload
        assert '-m set --match-set "%s" src -j DROP' % set_name not in payload
    # ровно два DROP'а в доп. цепочке — по одному на её собственные наборы
    assert payload.count('printf -- "$rule"') == 2


def test_watcher_payload_still_valid_bash_with_new_log_rules():
    proc = subprocess.run(["bash", "-n"], input=_payload(), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert rkn_watcher.EXTRA_SCRIPT in _payload()
    for marker in ("RKN_SWAP_RESULT=OK", "RKN_SWAP_RESULT=CHECK", "deploy-panel-whitelist",
                   "with_lock", "flock"):
        assert marker in _payload()
