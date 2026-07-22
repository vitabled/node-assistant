"""Wave-7 Plan E Ф2 — the assistant borrowing tools from our own MCP server.

Two properties matter more than the happy path:
  • degradation — MCP off/unreachable/foreign must never break a chat;
  • ownership — a container holding ANOTHER account's creds must contribute
    nothing, or this account's questions get answered from the wrong panel.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.settings import AiConfig
from app.services import ai_agent, mcp_client

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register",
                    json={"login": f"mc-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── read-only classification ──────────────────────────────────
@pytest.mark.parametrize("name", [
    "nodes_get_all", "users_get_by_uuid", "system_get_stats", "hosts_get_all",
])
def test_read_tools_are_read_only(name):
    assert mcp_client.is_read_only(name)


@pytest.mark.parametrize("name", [
    "nodes_create", "users_delete", "hosts_bulk_disable", "nodes_restart",
    "sub_page_configs_reorder", "users_update", "nodes_actions_enable",
])
def test_mutating_tools_are_detected(name):
    assert not mcp_client.is_read_only(name)


# ── SSE / JSON parsing ────────────────────────────────────────
def test_parses_a_plain_json_body():
    assert mcp_client._parse('{"result": {"tools": []}}') == {"result": {"tools": []}}


def test_parses_a_single_sse_frame():
    body = 'event: message\ndata: {"result": {"tools": [{"name": "a"}]}}\n\n'
    assert mcp_client._parse(body)["result"]["tools"][0]["name"] == "a"


def test_rejects_garbage():
    with pytest.raises(mcp_client.McpClientError):
        mcp_client._parse("not json")


# ── degradation ───────────────────────────────────────────────
@pytest.mark.anyio
async def test_disabled_use_mcp_yields_no_tools():
    assert await ai_agent._mcp_tools(AiConfig(use_mcp=False)) == []


@pytest.mark.anyio
async def test_unreachable_mcp_yields_no_tools(monkeypatch):
    async def dead():
        raise RuntimeError("no docker")
    monkeypatch.setattr("app.services.mcp_server.status", dead)
    assert await ai_agent._mcp_tools(AiConfig(use_mcp=True)) == []


@pytest.mark.anyio
async def test_foreign_container_yields_no_tools(monkeypatch):
    """The shared container carries the creds of whoever enabled it."""
    async def foreign():
        return {"container": "foreign", "reachable": False}
    monkeypatch.setattr("app.services.mcp_server.status", foreign)
    assert await ai_agent._mcp_tools(AiConfig(use_mcp=True)) == []


@pytest.mark.anyio
async def test_running_container_contributes_prefixed_tools(monkeypatch):
    async def running():
        return {"container": "running", "reachable": True}
    monkeypatch.setattr("app.services.mcp_server.status", running)
    monkeypatch.setattr("app.services.mcp_server.read_auth_token", lambda *a, **k: "tok")

    async def fake_list(self):
        return [
            {"name": "nodes_get_all", "description": "d", "inputSchema": {"type": "object"}},
            {"name": "nodes_delete", "description": "d", "inputSchema": {"type": "object"}},
        ]
    monkeypatch.setattr(mcp_client.McpSession, "list_tools", fake_list)

    tools = await ai_agent._mcp_tools(AiConfig(use_mcp=True, readonly=True))
    names = [t["name"] for t in tools]
    # prefixed so an MCP tool can never collide with a built-in one
    assert names == ["mcp__nodes_get_all"]

    tools = await ai_agent._mcp_tools(AiConfig(use_mcp=True, readonly=False))
    assert [t["name"] for t in tools] == ["mcp__nodes_get_all", "mcp__nodes_delete"]


@pytest.mark.anyio
async def test_tool_catalogue_is_capped(monkeypatch):
    """Injecting the whole 156-tool contract would add tens of kB of schemas to
    every turn and degrade the model's choice."""
    async def running():
        return {"container": "running", "reachable": True}
    monkeypatch.setattr("app.services.mcp_server.status", running)
    monkeypatch.setattr("app.services.mcp_server.read_auth_token", lambda *a, **k: "tok")

    async def many(self):
        return [{"name": f"get_{i}", "description": "d", "inputSchema": {}}
                for i in range(500)]
    monkeypatch.setattr(mcp_client.McpSession, "list_tools", many)

    tools = await ai_agent._mcp_tools(AiConfig(use_mcp=True))
    assert len(tools) == ai_agent.MAX_MCP_TOOLS


# ── specs merge ───────────────────────────────────────────────
def test_specs_merge_builtin_and_mcp():
    extra = [{"name": "mcp__x", "description": "d", "schema": {"type": "object"}}]
    oa = ai_agent._tool_specs_openai(extra)
    an = ai_agent._tool_specs_anthropic(extra)
    assert oa[-1]["function"]["name"] == "mcp__x"
    assert an[-1]["name"] == "mcp__x"
    assert len(oa) == len(ai_agent.TOOLS) + 1


@pytest.mark.anyio
async def test_unknown_builtin_tool_is_reported_not_raised():
    ok, msg = await ai_agent._run_tool("nope", {}, "acc")
    assert ok is False and "nope" in msg


# ── status endpoint ───────────────────────────────────────────
def test_tools_status_reports_off_by_default():
    h = _auth()
    r = client.get("/api/ai/tools", headers=h)
    assert r.status_code == 200
    assert r.json()["mcp"] == 0 and r.json()["reason"] == "off"
    assert r.json()["builtin"] == len(ai_agent.TOOLS)


def test_tools_status_requires_auth():
    assert client.get("/api/ai/tools").status_code == 401


@pytest.fixture
def anyio_backend():
    return "asyncio"
