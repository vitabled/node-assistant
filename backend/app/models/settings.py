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


#: Штатный адрес каждого провайдера. ЕДИНСТВЕННЫЙ источник правды: отсюда его
#: берут и агент, и форма настроек (через `/api/ai/config`). Копия на клиенте
#: отстала бы, а цена рассинхрона — запрос с верным ключом по чужому адресу и
#: неотличимое от «неверный ключ» 401.
PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


class AiConfig(BaseModel):
    """Config for the built-in AI agent (Ф4). The provider API key is stored
    Fernet-encrypted (`api_key_enc`) and NEVER returned to the client (masked)."""

    enabled: bool = False
    provider: str = "openai"  # openai (OpenAI-compatible) | anthropic
    base_url: str = "https://api.openai.com/v1"
    #: Адрес выводится из провайдера, а не хранится. Закрывает целый класс ошибок:
    #: сменил провайдера — адрес остался прежним — ключ уехал не туда — «провайдер
    #: отклонил ключ». Ручной режим нужен для OpenAI-совместимых сторонних
    #: эндпоинтов (OpenRouter, локальная llama.cpp), поэтому он остаётся, но
    #: включается осознанно.
    base_url_auto: bool = True
    model: str = "gpt-4o-mini"
    api_key_enc: str = ""  # Fernet ciphertext (base64); never plaintext
    # ⚠️ Шесть шагов мало для реальной работы: агент тратит их на разведку
    # (сводка → каталог ручек → чтение файла) и упирается в предел, не начав
    # дела. Потолок остаётся защитой от разгона, но не мешает задаче.
    # ⚠️ 0 = АВТО: потолка нет, агент идёт, пока есть прогресс (см.
    # `ai_agent.AUTO_MAX_STEPS` — физический предохранитель на 200 шагов).
    max_steps: int = 12  # tool-calling loop cap (anti-runaway); 0 = auto
    # ⚠️ Потолок ВЫВОДА за один тёрн. Был жёстко зашит в 1024 у Anthropic, и
    # этого не хватало на одно тело `panel_write` с тарифами и оценками: ответ
    # обрезался посреди JSON, разобрать его было нечем, и до пользователя
    # доходил пустой пузырь. Anthropic поле требует, OpenAI-совместимым не шлём
    # (у них своё умолчание, а имя параметра разъехалось по версиям API).
    # ⚠️ 0 = АВТО: стартуем с `ai_agent.AUTO_TOKENS_START` и на каждом обрыве по
    # длине поднимаем потолок ×1.5 сами, вместо просьбы «поднимите в настройках».
    max_tokens: int = 8192
    #: Суммарный бюджет токенов на ОДИН ответ агента (авто-режим). 0 = использовать
    #: `ai_agent.AUTO_TOKEN_BUDGET` (1M). Ниже — большие задачи (импорт архива,
    #: перенос каталога) обрываются на середине; выше — дорого при разгоне.
    auto_token_budget: int = 0
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
    # Веб-доступ ассистента (поиск + чтение страниц). Включён по умолчанию:
    # провайдер по умолчанию `duckduckgo` ключа не требует, поэтому включение не
    # заставляет никого ничего заводить. Ключ платного провайдера — Fernet, как
    # и остальные секреты модуля.
    web_enabled: bool = True
    web_provider: str = "duckduckgo"    # duckduckgo | tavily | brave | searxng
    web_api_key_enc: str = ""
    web_base_url: str = ""              # только для searxng (свой инстанс)
    web_max_results: int = 5
    # NOTE: the shared container's owner is tracked GLOBALLY in
    # DATA_DIR/cliproxy_owner.json (cliproxy_server._OWNER_FILE), NOT here — a
    # per-account field made every non-owner look like the owner (Wave-7 review).

    @model_validator(mode="after")
    def _infer_base_url_auto(self) -> "AiConfig":
        """Сохранить чужой эндпоинт при обновлении.

        ⚠️ Поле появилось позже самой настройки, поэтому у существующих установок
        его в `settings.json` нет — и умолчание `True` молча переключило бы
        OpenRouter или локальную llama.cpp на `api.openai.com`. Поэтому, когда
        значение не задано ЯВНО, режим выводится из адреса: штатный адрес (или
        пустой) — автоматический, любой другой — ручной.
        """
        if "base_url_auto" not in self.model_fields_set:
            url = (self.base_url or "").strip().rstrip("/")
            known = {v.rstrip("/") for v in PROVIDER_BASE_URLS.values()}
            self.base_url_auto = (not url) or url in known
        return self

    def effective_base_url(self) -> str:
        """Адрес, по которому реально надо ходить."""
        if self.base_url_auto:
            return PROVIDER_BASE_URLS.get(self.provider) or self.base_url
        return self.base_url


class LatencyLabConfig(BaseModel):
    """Latency Lab (console.latencylab.ru) personal API key + scan defaults.

    Тот же контракт секрета, что у `AiConfig`: ключ лежит Fernet-шифротекстом
    (`api_key_enc`) и НИКОГДА не отдаётся клиенту — наружу уходят только
    `has_key` и маска последних символов.
    """

    enabled: bool = False
    api_key_enc: str = ""  # Fernet ciphertext (base64); never plaintext
    base_url: str = "https://console.latencylab.ru"
    #: Узел-агент, с которого идут замеры. По умолчанию единственный — orel.
    node_id: str = "orel"
    #: Пусто = мультискан по всем online-операторам (1 запрос к лимиту).
    default_operator: str = ""
    #: Лимит запусков сканов за окно; 0 — без лимита.
    scan_limit: int = 0
    #: Окно лимита в часах (1…720).
    scan_window_hours: int = 24
    #: Метки времени (time.time) успешных запусков сканов — счётчик лимита.
    scan_history: list[float] = Field(default_factory=list)


class HaproxyConfig(BaseModel):
    """Wave-7: connection to a NodeFlow HAProxy panel (deploy + proxy integration).

    Two modes:
    - `local` (default) — node-installer auto-deploys a SHARED local NodeFlow stack
      (services/nodeflow_server.py) and proxies it. The admin token + PKI live GLOBALLY
      (not here); `base_url`/`admin_token_enc` are ignored.
    - `remote` — the account registers an EXISTING panel (URL + PANEL_ADMIN_TOKEN). The
      admin token is an infra-control secret → Fernet-encrypted (`admin_token_enc`), like
      the MCP/cliproxy vaults; the plaintext is NEVER returned (only `has_token`)."""

    enabled: bool = False
    mode: Literal["local", "remote"] = "local"
    base_url: str = ""  # remote mode: NodeFlow panel base URL
    admin_token_enc: str = ""  # remote mode: Fernet ciphertext of PANEL_ADMIN_TOKEN


class CloudflareConfig(BaseModel):
    """Wave-9 Plan B — connection to a Cloudflare account for the billing/domains
    sections. The API token needs billing/registrar scopes, so it is an
    account-control secret → Fernet-encrypted (`api_token_enc`), never returned to
    the client (only `has_token`), like the MCP/haproxy vaults.

    ⚠️ This is NOT `deploy_defaults.cloudflare_api_key`: that one is a DNS-edit
    token the deploy pipeline uses for A-records and stays untouched here — the
    scopes don't overlap, so the two connections are deliberately separate.

    `default_contact` is the registrant contact prefilled into a domain purchase:
    PII, not a secret → plain JSON, so a restored account keeps its contact."""

    enabled: bool = False
    account_id: str = ""
    api_token_enc: str = ""  # Fernet ciphertext (base64); never plaintext
    default_contact: dict = {}


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


class AutoBackupConfig(BaseModel):
    """Wave-8 §4 — scheduled per-account export shipped to Telegram. The bot token
    is Fernet-encrypted (`bot_token_enc`) and NEVER returned to the client (only
    `has_token`). `include_secrets` toggles whether credential fields are kept in
    the archive (privacy: with it on, secrets land in the Telegram chat)."""

    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=8760)
    include_secrets: bool = False
    chat_id: str = ""
    bot_token_enc: str = ""  # Fernet ciphertext (base64); never plaintext
    last_run: int = 0
    last_error: str = ""


class AppSettings(BaseModel):
    remnawave: RemnavaveConfig = RemnavaveConfig()
    remnawave_registry: RemnawaveRegistry = RemnawaveRegistry()
    deploy_defaults: DeployDefaults = DeployDefaults()
    optimization: OptimizationSettings = OptimizationSettings()
    xray_checker: XrayCheckerConfig = XrayCheckerConfig()
    mcp: McpConfig = McpConfig()
    ai: AiConfig = AiConfig()
    haproxy: HaproxyConfig = HaproxyConfig()
    cloudflare: CloudflareConfig = CloudflareConfig()
    appearance: AppearanceConfig = AppearanceConfig()
    auto_backup: AutoBackupConfig = AutoBackupConfig()
    latency: LatencyLabConfig = LatencyLabConfig()

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
