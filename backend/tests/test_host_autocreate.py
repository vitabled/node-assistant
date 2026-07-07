"""Ф6 — auto-create Remnawave hosts from local host-templates at deploy time.

Exercises `pipeline.step_create_hosts` in isolation: a fake RemnavaveClient
records create_host calls, `storage.load_hosts` is monkeypatched to a fixture,
and the loop is driven with a Template carrying 3 host-template ids (one valid,
one disabled via the deploy request, one with an empty inbound). `asyncssh` is
stubbed so the pipeline module imports without the SSH stack.
"""
import asyncio
import sys
import types
from types import SimpleNamespace

sys.modules.setdefault("asyncssh", types.ModuleType("asyncssh"))

from app.services.pipeline import step_create_hosts, _map_host_optional  # noqa: E402
from app.services.remnawave_client import RemnavaveError  # noqa: E402


NODE_UUID = "node-uuid-123"
PROFILE_UUID = "profile-uuid-abc"


class _FakeTask:
    def __init__(self):
        self.logs: list[str] = []

    def add_log(self, msg: str) -> None:
        self.logs.append(msg)


class _FakeClient:
    """Records create_host payloads. `fail_on` = inbound uuids that raise."""

    def __init__(self, fail_on: set[str] | None = None):
        self.calls: list[dict] = []
        self.fail_on = fail_on or set()

    async def create_host(self, **kwargs):
        self.calls.append(kwargs)
        ib = kwargs.get("inbound", {}).get("configProfileInboundUuid")
        if ib in self.fail_on:
            raise RemnavaveError(400, "duplicate remark")
        return {"uuid": "host-uuid-xyz"}


def _req(disabled=None):
    return SimpleNamespace(
        domain="node1.example.com",
        remnanode_port=2222,
        disabled_host_template_ids=disabled or [],
    )


def _hosts_fixture():
    # h1 valid, h2 will be disabled, h3 has empty inbound (invalid)
    return [
        {"id": "h1", "remark": "Reality", "inbound": "ib-1", "port": 443,
         "sni": "www.microsoft.com", "host": "cdn.example.com", "path": "/vless",
         "hide_host": True, "vless_route_id": 7, "security_layer": "tls",
         "tag": "ROUTING_HOST", "server_description": "primary"},
        {"id": "h2", "remark": "Disabled one", "inbound": "ib-2", "port": 8443},
        {"id": "h3", "remark": "No inbound", "inbound": "", "port": 443},
    ]


def _run(monkeypatch, client, host_ids, req):
    import app.services.storage as storage
    monkeypatch.setattr(storage, "load_hosts", lambda *a, **k: _hosts_fixture())
    task = _FakeTask()
    asyncio.run(step_create_hosts(task, client, req, NODE_UUID, PROFILE_UUID, host_ids))
    return task


def test_creates_selected_host_with_node_address(monkeypatch):
    client = _FakeClient()
    _run(monkeypatch, client, ["h1", "h2", "h3"], _req(disabled=["h2"]))

    # Only h1 created: h2 disabled, h3 empty-inbound skipped.
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["address"] == "node1.example.com"      # == req.domain (FQDN)
    assert call["nodes"] == [NODE_UUID]
    assert call["inbound"]["configProfileUuid"] == PROFILE_UUID
    assert call["inbound"]["configProfileInboundUuid"] == "ib-1"   # == template.inbound
    assert call["port"] == 443
    assert call["remark"].startswith("Reality")
    assert "node1" in call["remark"]                   # node suffix appended


def test_disabled_template_not_created(monkeypatch):
    client = _FakeClient()
    _run(monkeypatch, client, ["h1", "h2"], _req(disabled=["h2"]))
    inbounds = [c["inbound"]["configProfileInboundUuid"] for c in client.calls]
    assert "ib-2" not in inbounds


def test_empty_inbound_skipped(monkeypatch):
    client = _FakeClient()
    task = _run(monkeypatch, client, ["h3"], _req())
    assert client.calls == []
    assert any("без inbound" in m for m in task.logs)


def test_create_host_failure_continues(monkeypatch):
    # h1 raises; h2 must still be attempted → deploy continues (no exception).
    client = _FakeClient(fail_on={"ib-1"})
    task = _run(monkeypatch, client, ["h1", "h2"], _req())
    assert len(client.calls) == 2                      # both attempted
    assert any("ПРЕДУПРЕЖДЕНИЕ" in m for m in task.logs)


def test_no_host_templates_is_noop(monkeypatch):
    client = _FakeClient()
    task = _run(monkeypatch, client, [], _req())
    assert client.calls == []
    assert task.logs == []                             # nothing logged


def test_map_host_optional_camelcase():
    tpl = _hosts_fixture()[0]
    opt = _map_host_optional(tpl)
    assert opt["sni"] == "www.microsoft.com"
    assert opt["host"] == "cdn.example.com"
    assert opt["path"] == "/vless"
    assert opt["isHidden"] is True
    assert opt["vlessRouteId"] == 7
    assert opt["securityLayer"] == "TLS"   # local "tls" → Remnawave enum "TLS"
    assert opt["serverDescription"] == "primary"
    assert opt["tags"] == ["ROUTING_HOST"]
    # off-by-default flags absent
    assert "shuffleHost" not in opt
    assert "keepSniBlank" not in opt


def test_map_host_optional_enum_normalization():
    # local lowercase enums → Remnawave UPPERCASE; unsupported/default dropped;
    # visible=False → isDisabled=True (inverse); huge remark truncation is checked
    # at the create_host call site, not here.
    assert _map_host_optional({"security_layer": "reality"}) == {}   # unsupported → dropped, no 400
    assert _map_host_optional({"security_layer": "none"})["securityLayer"] == "NONE"
    assert _map_host_optional({"exclude_sub_types": ["xray_json", "clash"]})[
        "excludeFromSubscriptionTypes"] == ["XRAY_JSON", "CLASH"]
    assert _map_host_optional({"visible": False})["isDisabled"] is True
    assert "isDisabled" not in _map_host_optional({"visible": True})   # default visible → not sent
    # tag over 36 chars is rejected (Remnawave maxLength)
    assert "tags" not in _map_host_optional({"tag": "A" * 37})


def test_map_host_optional_defaults_dropped():
    # A bare host (default security_layer/vless_route_id=0/flags off) → empty map.
    opt = _map_host_optional({"remark": "x", "inbound": "ib", "port": 443,
                              "security_layer": "default", "vless_route_id": 0})
    assert opt == {}
