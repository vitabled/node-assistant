from __future__ import annotations
import uuid as _uuid
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class RemnavaveConfig(BaseModel):
    panel_url: str = ""
    api_token: str = ""
    default_internal_squad_ids: list[str] = []
    default_external_squad_ids: list[str] = []


class PanelEntry(BaseModel):
    """One Remnawave panel in the per-account registry (Wave-5 Plan K)."""
    id: str = Field(default_factory=lambda: _uuid.uuid4().hex[:12])
    name: str = "Основная"
    kind: str = "custom"  # custom | deployed
    panel_url: str = ""
    api_token: str = ""
    default_internal_squad_ids: list[str] = []
    default_external_squad_ids: list[str] = []


class RemnawaveRegistry(BaseModel):
    panels: list[PanelEntry] = []
    active_panel_id: str = ""


# Stable id for the entry auto-migrated from a legacy single-panel config.
_MIGRATED_PANEL_ID = "primary"


class DeployDefaults(BaseModel):
    ssh_user: str = "root"
    email: str = ""
    cloudflare_api_key: str = ""
    current_ssh_port: int = 22
    new_ssh_port: int = 2222
    open_ports: str = "80,443,8443"
    change_ssh_port: bool = True
    remnanode_port: int = 2222
    xhttp_path: str = ""
    # Default firewall/fail2ban whitelist (IPs/CIDRs) that prefills the form.
    whitelist_ips: str = ""
    # HAProxy relay defaults
    haproxy_source_port: int = 443
    haproxy_dest_port: int = 443
    haproxy_maxconn: int = 200000
    haproxy_log: str = "global"
    haproxy_mode: str = "tcp"
    haproxy_timeout_connect: str = "5s"
    haproxy_timeout_client: str = "50s"
    haproxy_timeout_server: str = "50s"
    haproxy_timeout_tunnel: str = "1h"


class OptimizationSettings(BaseModel):
    network_tuning: bool = True
    bbr: bool = True
    system_limits: bool = True
    dns: bool = True
    dns_servers: str = "1.1.1.1,8.8.8.8"


class XrayCheckerConfig(BaseModel):
    """Config for the headless xray-checker container that node-assistant
    supervises. `subscription_url` is the Remnawave subscription the checker
    probes; the rest map 1:1 to the checker's env vars."""

    enabled: bool = True
    subscription_url: str = ""
    check_interval: int = 300  # PROXY_CHECK_INTERVAL (seconds)
    check_method: str = "ip"  # PROXY_CHECK_METHOD: ip|status|download
    metrics_port: int = 2112  # METRICS_PORT (host port we scrape)
    image: str = "kutovoys/xray-checker:latest"
    poll_interval: int = 60  # how often node-assistant samples the checker


class McpConfig(BaseModel):
    """Config for the node-installer MCP container (Ф3). The MCP_AUTH_TOKEN is
    stored Fernet-encrypted (`auth_token_enc`); the plaintext is returned only to
    the authenticated owner via the config endpoint so they can copy it into an
    external client. Remnawave creds + a node-assistant JWT are injected at
    container start from the active account's settings."""

    enabled: bool = False
    readonly: bool = True  # only read/list tools when true
    http_port: int = 3100  # MCP_HTTP_PORT (host + container)
    image: str = "node-installer-mcp:latest"
    auth_token_enc: str = ""  # Fernet ciphertext (base64); never plaintext


class AiConfig(BaseModel):
    """Config for the built-in AI agent (Ф4). The provider API key is stored
    Fernet-encrypted (`api_key_enc`) and NEVER returned to the client (masked)."""

    enabled: bool = False
    provider: str = "openai"  # openai (OpenAI-compatible) | anthropic
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_enc: str = ""  # Fernet ciphertext (base64); never plaintext
    max_steps: int = 6  # tool-calling loop cap (anti-runaway)
    readonly: bool = True  # only read-only tools exposed to the agent
    active_preset_id: str = ""  # active system-prompt preset (Plan I; "" = default)
    gateway: str = "none"  # Plan J: none | cliproxy (route via CLIProxyAPI gateway)
    gateway_internal: bool = False  # gateway runs on our node-assistant-net → SSRF-exempt
    # Wave-7 Plan E Ф2: borrow the MCP server's tools (the whole Remnawave
    # contract). Off by default — it only works when the shared MCP container
    # belongs to this account, and it makes every turn's prompt much larger.
    use_mcp: bool = False
    # Wave-7 Plan F: self-hosted CLIProxyAPI reached with OAuth provider accounts
    # instead of API keys. Both secrets are Fernet ciphertext, never plaintext.
    cliproxy_enabled: bool = False
    cliproxy_image: str = "eceasy/cli-proxy-api:v7.2.50"
    cliproxy_master_key_enc: str = ""   # client key our backend presents on /v1
    cliproxy_mgmt_key_enc: str = ""     # Management API key — NEVER to a browser
    cliproxy_owner_account_id: str = ""  # shared container: who configured it


class AppearanceConfig(BaseModel):
    """Per-account mirror of the UI appearance prefs (Wave-5 Plan B) so the look
    follows the account across devices. No secrets → plain JSON, no Fernet.
    localStorage stays the fast local cache; this is the seed on first login on a
    new device. Literal fields reject invalid values with 422."""

    skin: Literal["apple", "console", "neon"] = "apple"
    mode: Literal["light", "dark", "system"] = "system"
    accent: Literal["blue", "green", "violet", "amber", "cyan", "magenta", "lime"] = "blue"
    density: Literal["comfortable", "compact"] = "comfortable"
    animations: bool = True
    neon_glow: bool = True


class AppSettings(BaseModel):
    remnawave: RemnavaveConfig = RemnavaveConfig()
    remnawave_registry: RemnawaveRegistry = RemnawaveRegistry()
    deploy_defaults: DeployDefaults = DeployDefaults()
    optimization: OptimizationSettings = OptimizationSettings()
    xray_checker: XrayCheckerConfig = XrayCheckerConfig()
    mcp: McpConfig = McpConfig()
    ai: AiConfig = AiConfig()
    appearance: AppearanceConfig = AppearanceConfig()

    @model_validator(mode="after")
    def _resolve_active_panel(self):
        """Wave-5 Plan K: keep `remnawave` a computed view of the ACTIVE panel so
        the ~13 sites reading `.remnawave` stay untouched. If the registry is empty
        but a legacy single-panel `remnawave` is set, migrate it into the registry
        (stable id) in-memory. A bad/missing active_panel_id falls back to the
        first panel."""
        reg = self.remnawave_registry
        if not reg.panels:
            legacy = self.remnawave
            if legacy.panel_url or legacy.api_token:
                reg.panels = [PanelEntry(
                    id=_MIGRATED_PANEL_ID, name="Основная", kind="custom",
                    panel_url=legacy.panel_url, api_token=legacy.api_token,
                    default_internal_squad_ids=legacy.default_internal_squad_ids,
                    default_external_squad_ids=legacy.default_external_squad_ids,
                )]
                reg.active_panel_id = _MIGRATED_PANEL_ID
            else:
                return self  # both empty → Remnawave "not configured", as before
        # Pick the active panel (fallback: first) and project it into `remnawave`.
        active = next((p for p in reg.panels if p.id == reg.active_panel_id), None)
        if active is None:
            active = reg.panels[0]
            reg.active_panel_id = active.id
        self.remnawave = RemnavaveConfig(
            panel_url=active.panel_url,
            api_token=active.api_token,
            default_internal_squad_ids=active.default_internal_squad_ids,
            default_external_squad_ids=active.default_external_squad_ids,
        )
        return self


class Template(BaseModel):
    id: str
    name: str
    config: str
    is_default: bool = False
    # Local host-template ids (accounts/<id>/hosts.json) auto-created as Remnawave
    # hosts at deploy time (Ф6).
    host_template_ids: list[str] = []


class TemplateCreate(BaseModel):
    name: str
    config: str
    is_default: bool = False
    host_template_ids: list[str] = []


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[str] = None
    is_default: Optional[bool] = None
    host_template_ids: Optional[list[str]] = None
