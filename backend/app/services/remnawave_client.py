"""
Async HTTP client for the Remnawave panel API.
Audited against api-1.json = OpenAPI 3.0.0, "Remnawave API v2.8.0".

Response structure convention used throughout this file:
  All endpoints return { "response": <payload> } (verified across every DTO we use).

v2.8.0 audit notes (2026-07-01):
  - POST /api/nodes response has NO token field → SECRET_KEY still comes from
    GET /api/keygen `pubKey` (get_node_secret_key). Do NOT try to read a token
    from the create-node response; it does not exist.
  - POST /api/users/bulk/update body is nested: { uuids, fields: { … } }.
  - trafficLimitStrategy enum = NO_RESET | DAY | WEEK | MONTH | MONTH_ROLLING.
  - New OPTIONAL request fields available on nodes (unused by us): proxyUrl,
    nodeConsumptionMultiplier, note. add-users bulk-actions take no request body.
"""
from __future__ import annotations
from typing import Any, Optional
import httpx


class RemnavaveError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Remnawave API {status}: {detail}")
        self.status = status
        self.detail = detail


def _unwrap(data: Any) -> Any:
    """Extract the `response` envelope that all Remnawave endpoints use."""
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data


class RemnavaveClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ── Core HTTP helper ───────────────────────────────────────

    async def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=20, verify=False) as client:
            resp = await client.request(method, url, headers=self._headers, **kwargs)
        if resp.status_code >= 400:
            try:
                detail = str(resp.json())
            except Exception:
                detail = resp.text[:300]
            raise RemnavaveError(resp.status_code, detail)
        try:
            return resp.json()
        except Exception:
            return {}

    # ── Health check ───────────────────────────────────────────

    async def check_connection(self) -> dict:
        """
        GET /api/internal-squads — used as connectivity probe.
        /api/remnawave-settings returns 403 for non-admin tokens;
        /api/internal-squads returns 200 for any valid token with squad/node rights.
        """
        data = await self._req("GET", "/api/internal-squads")
        payload = _unwrap(data)
        squads = payload.get("internalSquads", []) if isinstance(payload, dict) else []
        return {"ok": True, "squads": len(squads)}

    # ── Squad endpoints ────────────────────────────────────────

    async def list_internal_squads(self) -> list[dict]:
        """
        GET /api/internal-squads
        Response: { response: { total, internalSquads: [{ uuid, name, ... }] } }
        Returns list of { uuid, name } dicts.
        """
        data = await self._req("GET", "/api/internal-squads")
        payload = _unwrap(data)
        if isinstance(payload, dict):
            return payload.get("internalSquads", [])
        return []

    async def list_external_squads(self) -> list[dict]:
        """
        GET /api/external-squads
        Response: { response: { total, externalSquads: [{ uuid, name, ... }] } }
        Returns list of { uuid, name } dicts.
        """
        data = await self._req("GET", "/api/external-squads")
        payload = _unwrap(data)
        if isinstance(payload, dict):
            return payload.get("externalSquads", [])
        return []

    # ── Node plugins ───────────────────────────────────────────

    async def list_node_plugins(self) -> list[dict]:
        """
        GET /api/node-plugins  (NodePluginController_getAllConfigs)
        Response: { response: { total, nodePlugins: [{ uuid, name, ... }] } }
        Returns the list of { uuid, name, ... } plugin dicts.
        """
        data = await self._req("GET", "/api/node-plugins")
        payload = _unwrap(data)
        if isinstance(payload, dict):
            return payload.get("nodePlugins", [])
        return []

    # ── Config profiles ────────────────────────────────────────

    async def create_config_profile(self, name: str, config: dict) -> dict:
        """
        POST /api/config-profiles
        Body: { name: str (2-30 chars, /^[A-Za-z0-9_\\s-]+$/), config: object }
        Response: { response: { uuid, inbounds: [{ uuid, tag, ... }], ... } }
        Returns the `response` payload.
        """
        # Profile name must match ^[A-Za-z0-9_\s-]+$ (no dots/special chars)
        import re as _re
        safe_name = _re.sub(r"[^A-Za-z0-9_\s\-]", "_", name)[:30].strip() or "node"
        if len(safe_name) < 2:
            safe_name = "node_" + safe_name

        data = await self._req("POST", "/api/config-profiles", json={
            "name": safe_name,
            "config": config,
        })
        return _unwrap(data)

    # ── Nodes ──────────────────────────────────────────────────

    async def create_node(
        self,
        *,
        name: str,
        address: str,
        port: int,
        config_profile_uuid: str,
        active_inbounds: list[str],
        country_code: str = "XX",
        active_plugin_uuid: Optional[str] = None,
    ) -> dict:
        """
        POST /api/nodes
        Required fields: name (3-30 chars), address, configProfile.
        configProfile.activeConfigProfileUuid and activeInbounds are both required.
        countryCode is a 2-char ISO code (panel default "XX").
        activePluginUuid binds a single node plugin (the API supports only one
        active plugin per node).
        Response: { response: { uuid, name, address, ... } }
        Returns the `response` payload. NOTE: response.uuid is the node's
        identifier for routing (squad binding, etc.) — it is NOT the container
        SECRET_KEY. The SECRET_KEY is fetched separately via get_node_secret_key().
        """
        # Node name: 3-30 chars
        safe_name = name[:30]
        if len(safe_name) < 3:
            safe_name = (safe_name + "___")[:3]

        body = {
            "name": safe_name,
            "address": address,
            "port": port,
            "countryCode": (country_code or "XX").upper()[:2],
            "configProfile": {
                "activeConfigProfileUuid": config_profile_uuid,
                "activeInbounds": active_inbounds,
            },
        }
        if active_plugin_uuid:
            body["activePluginUuid"] = active_plugin_uuid
        data = await self._req("POST", "/api/nodes", json=body)
        return _unwrap(data)

    # ── Hosts ──────────────────────────────────────────────────

    async def create_host(
        self,
        *,
        inbound: dict,
        remark: str,
        address: str,
        port: int,
        nodes: Optional[list[str]] = None,
        **optional: Any,
    ) -> dict:
        """
        POST /api/hosts (CreateHostRequestDto).
        Required: inbound (OBJECT {configProfileUuid, configProfileInboundUuid}),
        remark, address, port. Optional CreateHostRequestDto fields (already in
        Remnawave camelCase, e.g. sni/host/path/alpn/fingerprint/securityLayer/
        isHidden/vlessRouteId/shuffleHost/serverDescription/overrideSniFromAddress/
        keepSniBlank/excludedInternalSquads/xhttpExtraParams/…) are passed through
        **optional; None values are dropped so the API keeps its own defaults.
        Response: { response: { uuid, ... } }. Returns the `response` payload.
        """
        body: dict[str, Any] = {
            "inbound": inbound,
            "remark": remark,
            "address": address,
            "port": port,
        }
        if nodes:
            body["nodes"] = nodes
        for key, value in optional.items():
            if value is not None:
                body[key] = value
        data = await self._req("POST", "/api/hosts", json=body)
        return _unwrap(data)

    async def get_internal_squad(self, squad_uuid: str) -> dict:
        """
        GET /api/internal-squads/{uuid}
        Response: { response: { uuid, name, inbounds: [{ uuid, ... }], ... } }
        Returns the `response` payload.
        """
        data = await self._req("GET", f"/api/internal-squads/{squad_uuid}")
        return _unwrap(data)

    async def add_inbounds_to_internal_squad(
        self, squad_uuid: str, inbound_uuids: list[str]
    ) -> None:
        """
        Bind a node's config-profile inbounds to an internal squad so the
        squad's users gain access to the new node.

        PATCH /api/internal-squads expects the FULL desired inbound list, so we
        read the squad's current inbounds, union them with the new ones, and
        send the merged set (idempotent — re-runs add nothing new).
        """
        squad = await self.get_internal_squad(squad_uuid)
        current = [
            ib["uuid"]
            for ib in (squad.get("inbounds", []) if isinstance(squad, dict) else [])
            if isinstance(ib, dict) and ib.get("uuid")
        ]
        merged = list(dict.fromkeys(current + list(inbound_uuids)))  # de-dup, keep order
        if set(merged) == set(current):
            return  # nothing to add
        await self._req("PATCH", "/api/internal-squads", json={
            "uuid": squad_uuid,
            "inbounds": merged,
        })

    async def get_node_secret_key(self) -> str:
        """
        GET /api/keygen — "Get SECRET_KEY for Remnawave Node".
        Response: { response: { pubKey: "<long base64/JWT token, eyJ...>" } }

        This pubKey is the panel-wide SECRET_KEY that every remnanode container
        uses to authenticate with the panel — it goes into the container's
        SECRET_KEY env var. It is distinct from a node's uuid.
        """
        data = await self._req("GET", "/api/keygen")
        payload = _unwrap(data)
        if isinstance(payload, dict):
            key = payload.get("pubKey", "")
            if key:
                return key
        raise RemnavaveError(500, "GET /api/keygen вернул пустой pubKey")

    # ── Squad bulk-actions ─────────────────────────────────────

    async def add_all_users_to_internal_squad(self, squad_uuid: str) -> None:
        """
        POST /api/internal-squads/{uuid}/bulk-actions/add-users
        Summary: "Add all users to internal squad"
        No request body. Enqueues a background job.
        """
        await self._req(
            "POST",
            f"/api/internal-squads/{squad_uuid}/bulk-actions/add-users",
        )

    async def add_all_users_to_external_squad(self, squad_uuid: str) -> None:
        """
        POST /api/external-squads/{uuid}/bulk-actions/add-users
        Summary: "Add all users to external squad"
        No request body. Enqueues a background job.
        """
        await self._req(
            "POST",
            f"/api/external-squads/{squad_uuid}/bulk-actions/add-users",
        )

    # ── Nodes ──────────────────────────────────────────────────

    async def list_nodes(self) -> list[dict]:
        """GET /api/nodes — returns list of node objects."""
        data = await self._req("GET", "/api/nodes")
        payload = _unwrap(data)
        return payload if isinstance(payload, list) else []

    async def get_nodes_metrics(self) -> list[dict]:
        """GET /api/system/nodes/metrics — per-node live metrics.
        Response: { response: { nodes: [{ nodeUuid, nodeName, countryEmoji,
        providerName, usersOnline, inboundsStats, outboundsStats }] } }.
        `usersOnline` is a COUNT — the reliable signal for node-load stats."""
        data = await self._req("GET", "/api/system/nodes/metrics")
        payload = _unwrap(data)
        if isinstance(payload, dict):
            nodes = payload.get("nodes", [])
            return nodes if isinstance(nodes, list) else []
        return []

    async def get_node_users_usage(self, node_uuid: str) -> dict:
        """GET /api/bandwidth-stats/nodes/{uuid}/users — cumulative top users on a node.
        Response: { response: { categories, sparklineData, topUsers: [{ username, total }] } }.
        Best-effort user↔node membership (cumulative usage, NOT live-online)."""
        data = await self._req("GET", f"/api/bandwidth-stats/nodes/{node_uuid}/users")
        payload = _unwrap(data)
        return payload if isinstance(payload, dict) else {}

    async def update_node_traffic(
        self,
        node_uuid: str,
        *,
        is_active: bool,
        limit_bytes: int,
        reset_day: int = 1,
    ) -> dict:
        """
        PATCH /api/nodes — update node-level traffic tracking.
        is_active=False + limit_bytes=0 → disables quota (unlimited).
        """
        body: dict = {
            "uuid": node_uuid,
            "isTrafficTrackingActive": is_active,
            "trafficLimitBytes": limit_bytes,
        }
        if is_active:
            body["trafficResetDay"] = reset_day
        data = await self._req("PATCH", "/api/nodes", json=body)
        return _unwrap(data)

    # ── Users ──────────────────────────────────────────────────

    async def get_users_in_squad(self, squad_uuid: str) -> list[str]:
        """
        Fetch all user UUIDs whose activeInternalSquads includes squad_uuid.
        Paginates GET /api/users (size=500) up to 2000 users.
        """
        result: list[str] = []
        page_size = 500
        start = 0
        while start < 2000:
            data = await self._req("GET", f"/api/users?size={page_size}&start={start}")
            payload = _unwrap(data)
            if not isinstance(payload, dict):
                break
            users = payload.get("users", [])
            if not users:
                break
            for u in users:
                squads = [s["uuid"] for s in (u.get("activeInternalSquads") or [])]
                if squad_uuid in squads:
                    result.append(u["uuid"])
            if len(users) < page_size:
                break
            start += page_size
        return result

    async def bulk_update_users_traffic(
        self,
        user_uuids: list[str],
        *,
        limit_bytes: int,
        strategy: str,
    ) -> None:
        """
        POST /api/users/bulk/update — set trafficLimitBytes + trafficLimitStrategy.
        Sends in chunks of 500 (API max).
        """
        for i in range(0, len(user_uuids), 500):
            chunk = user_uuids[i : i + 500]
            await self._req("POST", "/api/users/bulk/update", json={
                "uuids": chunk,
                "fields": {
                    "trafficLimitBytes": limit_bytes,
                    "trafficLimitStrategy": strategy,
                },
            })

    # ── Infra billing (v2.8.0 InfraBillingController) ──────────
    # Remnawave stores only: provider {name, faviconLink, loginUrl}; billing node
    # {providerUuid, nodeUuid, name, nextBillingAt}; history {providerUuid, amount,
    # billedAt}. Balances/costs/currency are NOT here — those live in our local
    # store (services/infra_billing_store.py). PATCH takes the uuid in the BODY.

    async def infra_list_providers(self) -> list[dict]:
        data = await self._req("GET", "/api/infra-billing/providers")
        p = _unwrap(data)
        return p.get("providers", []) if isinstance(p, dict) else []

    async def infra_create_provider(
        self, *, name: str, favicon_link: str = "", login_url: str = ""
    ) -> dict:
        body: dict[str, Any] = {"name": name}
        if favicon_link:
            body["faviconLink"] = favicon_link
        if login_url:
            body["loginUrl"] = login_url
        return _unwrap(await self._req("POST", "/api/infra-billing/providers", json=body))

    async def infra_update_provider(
        self, uuid: str, *, name: Optional[str] = None,
        favicon_link: Optional[str] = None, login_url: Optional[str] = None,
    ) -> dict:
        body: dict[str, Any] = {"uuid": uuid}
        if name is not None:        body["name"] = name
        if favicon_link is not None: body["faviconLink"] = favicon_link
        if login_url is not None:    body["loginUrl"] = login_url
        return _unwrap(await self._req("PATCH", "/api/infra-billing/providers", json=body))

    async def infra_delete_provider(self, uuid: str) -> None:
        await self._req("DELETE", f"/api/infra-billing/providers/{uuid}")

    async def infra_list_nodes(self) -> dict:
        """Returns {billingNodes, availableBillingNodes, stats, totals}."""
        return _unwrap(await self._req("GET", "/api/infra-billing/nodes"))

    async def infra_create_node(
        self, *, provider_uuid: str, node_uuid: str, name: str, next_billing_at: str
    ) -> dict:
        return _unwrap(await self._req("POST", "/api/infra-billing/nodes", json={
            "providerUuid": provider_uuid,
            "nodeUuid": node_uuid,
            "name": name,
            "nextBillingAt": next_billing_at,
        }))

    async def infra_update_nodes(self, uuids: list[str], *, next_billing_at: str) -> dict:
        return _unwrap(await self._req("PATCH", "/api/infra-billing/nodes", json={
            "uuids": uuids,
            "nextBillingAt": next_billing_at,
        }))

    async def infra_delete_node(self, uuid: str) -> None:
        await self._req("DELETE", f"/api/infra-billing/nodes/{uuid}")

    async def infra_list_history(self) -> list[dict]:
        data = _unwrap(await self._req("GET", "/api/infra-billing/history"))
        return data.get("records", []) if isinstance(data, dict) else []

    async def infra_create_history(
        self, *, provider_uuid: str, amount: float, billed_at: str
    ) -> dict:
        return _unwrap(await self._req("POST", "/api/infra-billing/history", json={
            "providerUuid": provider_uuid,
            "amount": amount,
            "billedAt": billed_at,
        }))

    async def infra_delete_history(self, uuid: str) -> None:
        await self._req("DELETE", f"/api/infra-billing/history/{uuid}")
