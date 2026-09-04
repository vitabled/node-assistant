"""VNStat parser and POST /api/node/vnstat with per-request SSH credentials."""
import uuid

from fastapi.testclient import TestClient

import app.api.node_ops as node_ops
from app.main import app


client = TestClient(app)

VNSTAT_JSON = """{
  "updated": {"date": {"year": 2026, "month": 9, "day": 4}, "time": {"hour": 12, "minute": 30}},
  "interfaces": [
    {
      "name": "lo",
      "traffic": {"total": {"rx": 1, "tx": 2}, "day": [{"date": {"year": 2026, "month": 9, "day": 4}, "rx": 1, "tx": 2}]}
    },
    {
      "name": "eth0",
      "traffic": {
        "total": {"rx": 900, "tx": 800},
        "month": [{"date": {"year": 2026, "month": 8}, "rx": 300, "tx": 200}, {"date": {"year": 2026, "month": 9}, "rx": 500, "tx": 400}],
        "day": [
          {"date": {"year": 2026, "month": 9, "day": 2}, "rx": 50, "tx": 50},
          {"date": {"year": 2026, "month": 9, "day": 3}, "rx": 200, "tx": 100},
          {"date": {"year": 2026, "month": 9, "day": 4}, "rx": 10, "tx": 20}
        ]
      }
    }
  ]
}"""


def _auth():
    response = client.post(
        "/api/auth/register",
        json={"login": f"vn-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _body(**overrides):
    body = {"ip": "1.2.3.4", "ssh_user": "root", "ssh_password": "pw", "ssh_port": 22}
    body.update(overrides)
    return body


def test_parse_vnstat_prefers_eth0_and_returns_current_buckets_and_top_five_days():
    result = node_ops._parse_vnstat_json(VNSTAT_JSON)

    assert result.ok is True
    assert result.reason is None
    assert result.interface == "eth0"
    assert result.updated_at == "2026-09-04 12:30"
    assert result.total.model_dump() == {"rx": 900, "tx": 800}
    assert result.month.model_dump() == {"rx": 500, "tx": 400}
    assert result.day.model_dump() == {"rx": 10, "tx": 20}
    assert [day.model_dump() for day in result.top_days] == [
        {"date": "2026-09-03", "rx": 200, "tx": 100},
        {"date": "2026-09-02", "rx": 50, "tx": 50},
        {"date": "2026-09-04", "rx": 10, "tx": 20},
    ]


def test_parse_vnstat_uses_months_as_total_fallback_and_no_data_response():
    fallback = """{"interfaces":[{"name":"ens3","traffic":{"months":[{"rx":4,"tx":6},{"rx":10,"tx":20}],"days":[{"date":{"year":2026,"month":9,"day":4},"rx":1,"tx":2}]}}]}"""

    result = node_ops._parse_vnstat_json(fallback)
    assert result.ok is True
    assert result.interface == "ens3"
    assert result.total.model_dump() == {"rx": 14, "tx": 26}
    assert result.month.model_dump() == {"rx": 10, "tx": 20}
    assert result.day.model_dump() == {"rx": 1, "tx": 2}

    empty = node_ops._parse_vnstat_json('{"interfaces": []}')
    assert empty.model_dump() == {
        "ok": False,
        "reason": "no data",
        "interface": None,
        "updated_at": None,
        "total": {"rx": None, "tx": None},
        "month": {"rx": None, "tx": None},
        "day": {"rx": None, "tx": None},
        "top_days": [],
    }
    empty_interface = node_ops._parse_vnstat_json(
        '{"interfaces": [{"name": "eth0", "traffic": {}}]}'
    )
    assert empty_interface.reason == "no data"
    assert empty_interface.total.rx is None


class FakeSSH:
    scripts: list[str] = []
    output = VNSTAT_JSON

    def __init__(self, *_args, **_kwargs):
        pass

    async def connect(self, *_args, **_kwargs):
        pass

    async def get_script_output(self, script: str, timeout=None) -> str:
        self.scripts.append(script)
        return self.output

    async def close(self):
        pass


def test_vnstat_endpoint_uses_silent_ssh_and_returns_structured_no_data(monkeypatch):
    FakeSSH.scripts = []
    monkeypatch.setattr(node_ops, "SSHSession", FakeSSH)

    response = client.post("/api/node/vnstat", headers=_auth(), json=_body())
    assert response.status_code == 200
    assert response.json()["interface"] == "eth0"
    assert response.json()["total"] == {"rx": 900, "tx": 800}
    assert len(FakeSSH.scripts) == 1
    assert "vnstat --json" in FakeSSH.scripts[0]

    FakeSSH.output = "__VNSTAT_NOT_INSTALLED__"
    response = client.post("/api/node/vnstat", headers=_auth(), json=_body())
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["reason"] == "vnstat not installed"
