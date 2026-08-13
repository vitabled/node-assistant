"""Wave-5 PR-1 — cookie-защита nginx, ACME-статус, SelfSteal."""
import uuid as _uuid

from fastapi.testclient import TestClient

from app.main import app
import app.api.certs as certs
from app.services import panel_pipeline

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"rv-{_uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── nginx cookie-защита ────────────────────────────────────────
def test_render_nginx_guard_only_on_panel_port():
    targets = [("panel.example.com", 3000), ("sub.example.com", 3010)]
    conf = panel_pipeline._render_nginx(targets, "ab12cd34ef56ab12")
    # У панели два server-блока с одним server_name: [1] — редирект 80→443,
    # [2] — ssl-блок. Страж должен быть только в ssl-блоке.
    parts = conf.split("server_name panel.example.com;")
    redirect_block, ssl_block = parts[1], parts[2]
    assert "return 301" in redirect_block and "nai_guard" not in redirect_block
    assert "if ($nai_guard = 0) { return 404; }" in ssl_block
    assert "$cookie_ab12cd34ef56ab12" in ssl_block
    assert "add_header Set-Cookie" in ssl_block
    assert ssl_block.index("nai_guard") < ssl_block.index("proxy_pass")
    # подписочная страница не тронута
    assert "nai_guard" not in conf.split("server_name sub.example.com;")[1]


def test_render_nginx_without_guard_unchanged():
    conf = panel_pipeline._render_nginx([("panel.example.com", 3000)])
    assert "nai_guard" not in conf


# ── ACME-статус ────────────────────────────────────────────────
def test_parse_acme_status():
    out = (
        "CERT=node1.example.com|Jan 10 12:00:00 2026 GMT|https://acme-v02.api.letsencrypt.org/directory\n"
        "CERT=node2.example.com|Feb 11 10:00:00 2026 GMT|\n"
        "CRON=1\n"
    )
    d = certs._parse_acme_status(out)
    assert d["renewal_cron"] is True
    assert d["certs"][0]["domain"] == "node1.example.com"
    assert "letsencrypt" in d["certs"][0]["ca"]
    assert d["certs"][1]["not_after"].startswith("Feb 11")


def test_acme_status_route(monkeypatch):
    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self):
            pass

        async def get_script_output(self, script, timeout=None):
            return "CERT=n.example.com|Jan 1 00:00:00 2027 GMT|le\nCRON=1"

        async def close(self):
            pass

    monkeypatch.setattr(certs, "SSHSession", FakeSSH)
    r = client.post("/api/certs/acme-status", headers=_auth(),
                    json={"ip": "1.2.3.4", "ssh_user": "root", "ssh_password": "x"})
    assert r.status_code == 200
    assert r.json()["certs"][0]["domain"] == "n.example.com"
    assert r.json()["renewal_cron"] is True


# ── SelfSteal ──────────────────────────────────────────────────
def test_masking_script_extracted():
    s = panel_pipeline_module = __import__("app.services.pipeline", fromlist=["masking_script"])
    script = s.masking_script()
    assert "sni-templates" in script and "set -euo pipefail" in script


def test_selfsteal_route(monkeypatch):
    seen = {}

    class FakeSSH:
        def __init__(self, *a, **k):
            pass

        async def connect(self):
            pass

        async def run_script(self, script, task, check=True, timeout=None):
            seen["script"] = script
            return 0

        async def close(self):
            pass

    monkeypatch.setattr(certs, "SSHSession", FakeSSH)
    r = client.post("/api/certs/selfsteal", headers=_auth(),
                    json={"ip": "1.2.3.4", "ssh_user": "root", "ssh_password": "x"})
    assert r.status_code == 200 and r.json()["task_type"] == "selfsteal"
    import asyncio, time
    for _ in range(50):          # background task
        if "script" in seen:
            break
        time.sleep(0.05)
    assert "sni-templates" in seen["script"]
