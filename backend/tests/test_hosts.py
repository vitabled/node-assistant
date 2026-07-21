"""Ф11 — local Remnawave-host templates CRUD + per-account isolation."""
import sys
import types
import uuid

sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"host-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _body(**over):
    base = dict(remark="My Node", address="node.example.com", port=443)
    base.update(over)
    return base


def test_hosts_crud_and_isolation():
    a, b = _auth(), _auth()

    # create
    r = client.post("/api/hosts", headers=a, json=_body(remark="Alpha", tag="ROUTING_HOST", nodes=["n1"]))
    assert r.status_code == 201
    host = r.json()
    assert host["remark"] == "Alpha" and host["id"]
    assert host["visible"] is True and host["security_layer"] == "default"  # defaults applied

    # list — a sees its host, b sees none (isolation)
    assert len(client.get("/api/hosts", headers=a).json()) == 1
    assert client.get("/api/hosts", headers=b).json() == []

    # update (PUT full body)
    r = client.put(f"/api/hosts/{host['id']}", headers=a,
                   json=_body(remark="Alpha-2", port=8443, sni="sni.example.com", allow_insecure=True))
    assert r.status_code == 200
    upd = r.json()
    assert upd["remark"] == "Alpha-2" and upd["port"] == 8443 and upd["allow_insecure"] is True
    assert upd["id"] == host["id"]

    # update unknown → 404
    assert client.put("/api/hosts/nope", headers=a, json=_body()).status_code == 404

    # delete
    assert client.delete(f"/api/hosts/{host['id']}", headers=a).status_code == 204
    assert client.get("/api/hosts", headers=a).json() == []


def test_hosts_required_fields_and_bounds():
    a = _auth()
    assert client.post("/api/hosts", headers=a, json=_body(remark="")).status_code == 422       # remark required
    assert client.post("/api/hosts", headers=a, json=_body(port=0)).status_code == 422           # port bound
    assert client.post("/api/hosts", headers=a, json=_body(server_description="x" * 31)).status_code == 422
    assert client.post("/api/hosts", headers=a, json=_body(vless_route_id=-1)).status_code == 422      # route bound
    assert client.post("/api/hosts", headers=a, json=_body(vless_route_id=99999)).status_code == 422
    assert client.post("/api/hosts", headers=a, json=_body(vless_route_id=0)).status_code == 201       # 0 = off, valid


def test_hosts_requires_auth():
    assert client.get("/api/hosts").status_code == 401
    assert client.post("/api/hosts", json=_body()).status_code == 401


def test_host_shell_safety(monkeypatch=None):
    """Wave-5 Plan F — host/sni/path reject shell metacharacters + CR/LF."""
    a = _auth()
    # host / sni metacharacters → 422
    assert client.post("/api/hosts", headers=a, json=_body(host="a.com; rm -rf /")).status_code == 422
    assert client.post("/api/hosts", headers=a, json=_body(sni="$(whoami).com")).status_code == 422
    # CR/LF (header/response splitting) → 422
    assert client.post("/api/hosts", headers=a, json=_body(host="a.com\r\nInjected: 1")).status_code == 422
    # path metacharacters → 422
    assert client.post("/api/hosts", headers=a, json=_body(path="/a;$(id)")).status_code == 422
    # valid host/sni/path → 201
    assert client.post("/api/hosts", headers=a,
                       json=_body(host="cdn.example.com", sni="sni.example.com", path="/ws/path")).status_code == 201
