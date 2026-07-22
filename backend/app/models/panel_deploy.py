"""Ф4 (wave1) — request model for the Remnawave panel / subscription-page deploy.

`PanelDeployRequest` mirrors `DeployRequest`'s hardening posture: every value that
reaches a root-run bash script (domains, email, cf token, extra .env keys) is
validated so a hostile field can't break out of the generated scripts. Panel and
subscription-page can target the SAME server (bundled) or DIFFERENT servers
(`sub_server` set → separate-server mode, officially supported by Remnawave).
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Shared with DeployRequest: a plain hostname allowlist (doubles as a shell-safety
# guard — only [A-Za-z0-9.-] can appear, so no shell metacharacters survive).
_DOMAIN_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
# .env keys: POSIX-shell-safe env-var names only.
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# Secrets/DSN generated server-side — an extra_env override must not weaken them.
_PROTECTED_ENV_KEYS = frozenset(
    {
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "JWT_AUTH_SECRET",
        "JWT_API_TOKENS_SECRET",
        "METRICS_PASS",
        "WEBHOOK_SECRET_HEADER",
    }
)

# ~512 KiB — an Orion subscription page is a single static index.html.
_SUBPAGE_HTML_MAX = 512 * 1024


def _valid_ipv4(v: str) -> bool:
    return bool(_IPV4_RE.fullmatch(v)) and all(int(p) <= 255 for p in v.split("."))


class SubServer(BaseModel):
    """Separate-server target for the subscription page (its own SSH creds).
    Same validators as the primary target; creds are transient (per-request)."""

    ip: str
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)

    @field_validator("ip")
    @classmethod
    def _validate_ip(cls, v: str) -> str:
        if not _valid_ipv4(v):
            raise ValueError("Invalid IPv4 address")
        return v


class PanelDeployRequest(BaseModel):
    # What to install: the panel, only the subscription page, or both.
    target: Literal["panel", "subpage", "both"] = "panel"

    ip: str = Field(
        ..., description="Primary server IPv4 (panel, or subpage in subpage-only mode)"
    )
    ssh_user: str = "root"
    ssh_password: str = Field(..., min_length=1)
    ssh_port: int = Field(default=22, ge=1, le=65535)

    # Domains (empty allowed at field level; the target validator enforces which
    # one is required). FQDN allowlist = shell-safety guard.
    panel_domain: str = ""
    sub_domain: str = ""
    email: str = ""  # ACME registration (letsencrypt/zerossl); optional for caddy

    # Reverse proxy: caddy (auto-SSL) or nginx (acme.sh via build_ssl_script).
    # NOTE (deviation): the plan listed 4 proxies (caddy/nginx/traefik/angie); this
    # wave ships caddy+nginx only — traefik/angie deferred to Wave 2.
    reverse_proxy: Literal["caddy", "nginx"] = "caddy"
    cert_provider: Literal["cloudflare", "letsencrypt", "zerossl"] = "letsencrypt"
    cf_api_key: str = ""

    # Webhooks: the HMAC secret is generated server-side (never taken from input).
    enable_webhooks: bool = False
    webhook_url: str = ""

    # Extra .env overrides (applied on top of the generated base). Keys are strict
    # POSIX env-var names; values must be single-line (they go into the .env).
    extra_env: dict[str, str] = Field(default_factory=dict)

    # Separate-server subscription page. None → subpage on the panel's server.
    sub_server: Optional[SubServer] = None

    # Raw Orion subscription-page HTML (from the Ф5 catalog later). Size-capped.
    #
    # ⚠️ Волна 6, План E Ф1 — ПРОВЕРЕНО НА ОБРАЗЕ 7.2.6: `/opt/app/frontend/`
    # это собранная Vite/React SPA (160 файлов), а её `index.html` — EJS-шаблон
    # с `<%- panelData %>`, `<%= metaTitle %>`, `<%= metaDescription %>`.
    # Монтирование произвольного HTML поверх него ТИХО УБИВАЕТ канал данных
    # страницы. Поле оставлено для legacy-Orion, но пайплайн теперь требует,
    # чтобы шаблон нёс эти теги (см. _validate_subpage_html).
    subpage_html: str = ""

    # Образ страницы подписок. Пиннится, а не `:latest`: overlay/шаблон верны
    # только для конкретной версии фронтенда.
    subpage_image: str = "remnawave/subscription-page:7.2.6"

    # Обязателен для target ∈ {subpage, both}: без него контейнер падает с
    # кодом 1 («Environment Configuration Errors»), проверено запуском образа.
    # Не персистится у нас — уходит в .env на целевом боксе тихим каналом.
    subpage_api_token: str = ""

    install_test_tools: bool = True

    # ── field validators ──────────────────────────────────────

    @field_validator("ip")
    @classmethod
    def _validate_ip(cls, v: str) -> str:
        if not _valid_ipv4(v):
            raise ValueError("Invalid IPv4 address")
        return v

    @field_validator("panel_domain", "sub_domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        if not v:
            return v
        if not _DOMAIN_RE.fullmatch(v):
            raise ValueError("Invalid domain (hostname expected)")
        return v

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        if not v:
            return v
        if not _EMAIL_RE.fullmatch(v):
            raise ValueError("Invalid email")
        return v

    @field_validator("cf_api_key")
    @classmethod
    def _validate_cf(cls, v: str) -> str:
        # Interpolated into `export CF_Token="..."` in build_ssl_script — reject the
        # quote/newline that could break out of that string.
        if any(c in v for c in ('"', "\n", "\r", "`", "$")):
            raise ValueError("Invalid Cloudflare token")
        return v

    @field_validator("webhook_url")
    @classmethod
    def _validate_webhook_url(cls, v: str) -> str:
        # Empty allowed at field level (presence enforced by the target validator).
        # When present: an http(s) URL with no control chars (it lands in the .env).
        if not v:
            return v
        if not re.match(r"^https?://", v) or any(c in v for c in ("\n", "\r", " ")):
            raise ValueError("webhook_url must be an http(s) URL")
        return v

    @field_validator("extra_env")
    @classmethod
    def _validate_extra_env(cls, v: dict) -> dict:
        for key, val in (v or {}).items():
            if not _ENV_KEY_RE.fullmatch(str(key)):
                raise ValueError(
                    f"Invalid .env key: {key!r} (expected [A-Z_][A-Z0-9_]*)"
                )
            if str(key) in _PROTECTED_ENV_KEYS:
                # These are CSPRNG-generated server-side; letting an override weaken
                # them (e.g. empty JWT_AUTH_SECRET) would be a footgun.
                raise ValueError(
                    f"{key} is generated automatically and cannot be overridden"
                )
            if "\n" in str(val) or "\r" in str(val):
                raise ValueError(f"Invalid .env value for {key}: newlines not allowed")
        return v

    @field_validator("subpage_html")
    @classmethod
    def _validate_subpage_html(cls, v: str) -> str:
        if len(v.encode("utf-8")) > _SUBPAGE_HTML_MAX:
            raise ValueError("subpage_html too large (>512 KiB)")
        # `<%- panelData %>` — единственный канал данных SPA. Шаблон без него
        # даёт визуально «рабочую», но пустую страницу, и это молчаливый отказ.
        if v.strip() and "panelData" not in v:
            raise ValueError(
                "subpage_html не содержит `<%- panelData %>` — страница подписок "
                "останется без данных. Шаблон должен быть EJS-совместим с "
                "remnawave/subscription-page."
            )
        return v

    # ── cross-field validation ────────────────────────────────

    @model_validator(mode="after")
    def validate_by_target(self) -> "PanelDeployRequest":
        if self.target in ("panel", "both") and not self.panel_domain:
            raise ValueError("panel_domain is required when installing the panel")
        if self.target in ("subpage", "both") and not self.sub_domain:
            raise ValueError(
                "sub_domain is required when installing the subscription page"
            )
        if self.target in ("subpage", "both") and not self.subpage_api_token.strip():
            raise ValueError(
                "subpage_api_token is required: без него контейнер страницы "
                "подписок завершается с ошибкой конфигурации (создайте токен в "
                "Remnawave → Settings → API Tokens)"
            )
        # Caddy manages TLS itself (built-in ACME) and ignores cert_provider/
        # cf_api_key/email — those only matter for the nginx (acme.sh) branch.
        if self.reverse_proxy == "nginx":
            if self.cert_provider == "cloudflare" and not self.cf_api_key.strip():
                raise ValueError(
                    "cf_api_key is required for the cloudflare cert provider"
                )
            if (
                self.cert_provider in ("letsencrypt", "zerossl")
                and not self.email.strip()
            ):
                raise ValueError(
                    "email is required for the letsencrypt/zerossl cert providers"
                )
        if self.enable_webhooks and not self.webhook_url.strip():
            raise ValueError("webhook_url is required when webhooks are enabled")
        # A separate subscription-page server only makes sense alongside the panel.
        if self.sub_server is not None and self.target != "both":
            raise ValueError("sub_server is only valid when target='both'")
        return self
