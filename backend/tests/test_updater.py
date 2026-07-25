"""Wave-8 §3 — updater pure helpers + config/status persistence + no-docker."""
import asyncio

from app.services import updater


def test_is_behind():
    assert updater.is_behind("a", "b") is True
    assert updater.is_behind("a", "a") is False
    assert updater.is_behind("", "b") is False
    assert updater.is_behind("a", "") is False


def test_safe_branch():
    assert updater._safe_branch("main") == "main"
    assert updater._safe_branch("claude/remnawave-wave3") == "claude/remnawave-wave3"
    assert updater._safe_branch("bad;rm -rf") == ""
    assert updater._safe_branch("a b") == ""
    assert updater._safe_branch("") == ""


def test_parse_check_output():
    raw = ("===LOCAL===\nabc123\n"
           "===REMOTE===\ndef456\n"
           "===SUBJECT===\nfix: thing\n"
           "===BRANCH===\nmain\n")
    assert updater.parse_check_output(raw) == {
        "local": "abc123", "remote": "def456", "subject": "fix: thing", "branch": "main"}


def test_parse_check_output_missing_sections():
    p = updater.parse_check_output("===LOCAL===\nabc\n===BRANCH===\nmain\n")
    assert p["local"] == "abc" and p["branch"] == "main"
    assert p["remote"] == "" and p["subject"] == ""


def test_check_argv():
    argv = updater.check_argv("docker:cli", "/host/repo", "main")
    assert argv[:2] == ["run", "--rm"]
    assert "/host/repo:/repo" in argv
    assert argv[-3] == "sh" and argv[-2] == "-c"
    assert "main" in argv[-1]                       # branch interpolated in the script


def test_apply_argv_mounts_socket_and_data():
    argv = updater.apply_argv("docker:cli", "/host/repo", "node-data-vol", "main")
    assert "-d" in argv
    assert "/var/run/docker.sock:/var/run/docker.sock" in argv
    assert "/host/repo:/repo" in argv
    assert "node-data-vol:/data" in argv
    assert updater.UPDATER_CONTAINER in argv


def test_apply_script_writes_status_and_builds():
    s = updater._apply_script("main")
    assert "/data/updater_status.json" in s
    assert "docker compose -f /repo/docker-compose.yml build" in s
    assert "up -d" in s
    # a malicious branch never reaches the script
    assert "rm -rf" not in updater._apply_script("evil;rm -rf /")


def test_config_roundtrip():
    cfg = updater.save_config(True, "main", "docker:cli")
    assert cfg == {"auto_update": True, "branch": "main", "image": "docker:cli"}
    loaded = updater.load_config()
    assert loaded["auto_update"] is True and loaded["branch"] == "main"
    cfg2 = updater.save_config(False, "bad;branch", "")
    assert cfg2["branch"] == "" and cfg2["image"] == updater.DEFAULT_IMAGE


def test_status_roundtrip():
    updater._write_status("build", True, None)
    st = updater.read_status()
    assert st["step"] == "build" and st["running"] is True and st["ok"] is None


def test_check_no_docker(monkeypatch):
    async def fake_pd():
        return updater._NO_DOCKER
    monkeypatch.setattr(updater, "project_dir", fake_pd)
    updater._check_cache["data"] = None
    st = asyncio.run(updater.check(force=True))
    assert st["docker"] is False and st["behind"] is False and "error" in st
