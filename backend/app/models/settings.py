from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class RemnavaveConfig(BaseModel):
    panel_url: str = ""
    api_token: str = ""
    default_internal_squad_ids: list[str] = []
    default_external_squad_ids: list[str] = []


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

    enabled: bool = False
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


class AppSettings(BaseModel):
    remnawave: RemnavaveConfig = RemnavaveConfig()
    deploy_defaults: DeployDefaults = DeployDefaults()
    optimization: OptimizationSettings = OptimizationSettings()
    xray_checker: XrayCheckerConfig = XrayCheckerConfig()
    mcp: McpConfig = McpConfig()
    ai: AiConfig = AiConfig()


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
