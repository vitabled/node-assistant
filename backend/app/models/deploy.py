import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class DeployRequest(BaseModel):
    # Deploy mode — "remnanode" (default, full Xray/Remnawave stack) or "haproxy"
    # (isolated TCP relay; skips pipeline steps 7–11).
    mode: Literal["remnanode", "haproxy"] = Field(default="remnanode")

    ip: str = Field(..., description="Server IPv4 address")
    ssh_user: str = Field(default="root")
    ssh_password: str = Field(..., min_length=1)
    # domain/email/cloudflare are required only in remnanode mode (validated below)
    domain: str = Field(default="", description="Node domain, e.g. node1.example.com")
    cloudflare_api_key: str = Field(default="")
    email: str = Field(default="", description="Email for Let's Encrypt registration")
    remnanode_token: Optional[str] = Field(default=None)
    bandwidth_mbps: int = Field(..., gt=0, description="Channel bandwidth in Mbps")
    open_ports: str = Field(..., description="Comma-separated ports to open in UFW")
    current_ssh_port: int = Field(default=22, ge=1, le=65535)
    new_ssh_port: int = Field(default=2222, ge=1, le=65535)
    change_ssh_port: bool = Field(default=True)
    remnanode_port: int = Field(default=2222, ge=1, le=65535)
    xhttp_path: str = Field(default="")
    # No field-level length constraint: in haproxy mode the form sends "".
    # The 2-char requirement is enforced only for remnanode mode (validator below).
    country_code: str = Field(default="XX", max_length=2)
    behind_cdn: bool = Field(default=False)
    install_warp: bool = Field(default=False)
    update_system: bool = Field(default=False)
    # OS optimization (node-accelerator)
    optimize: bool = Field(default=True)
    opt_network_tuning: bool = Field(default=True)
    opt_bbr: bool = Field(default=True)
    opt_system_limits: bool = Field(default=True)
    opt_dns: bool = Field(default=True)
    opt_dns_servers: str = Field(default="1.1.1.1,8.8.8.8")
    # Remnawave integration (optional)
    create_in_remnawave: bool = Field(default=False)
    internal_squad_ids: list[str] = Field(default_factory=list)
    external_squad_ids: list[str] = Field(default_factory=list)
    plugin_uuid: Optional[str] = Field(default=None)
    template_id: Optional[str] = Field(default=None)

    # ── HAProxy relay mode ────────────────────────────────────
    haproxy_source_port: int = Field(default=443, ge=1, le=65535)
    haproxy_dest_ip: str = Field(default="")
    haproxy_dest_port: int = Field(default=443, ge=1, le=65535)
    haproxy_maxconn: int = Field(default=200000, ge=1)
    haproxy_log: str = Field(default="global")
    haproxy_mode: str = Field(default="tcp")
    haproxy_timeout_connect: str = Field(default="5s")
    haproxy_timeout_client: str = Field(default="50s")
    haproxy_timeout_server: str = Field(default="50s")
    haproxy_timeout_tunnel: str = Field(default="1h")

    @model_validator(mode="after")
    def validate_by_mode(self) -> "DeployRequest":
        if self.mode == "remnanode":
            # Domain/email/Cloudflare are needed for DNS + SSL in remnanode mode.
            if not self.domain:
                raise ValueError("domain is required in remnanode mode")
            if not self.cloudflare_api_key:
                raise ValueError("cloudflare_api_key is required in remnanode mode")
            if not self.email:
                raise ValueError("email is required in remnanode mode")
            if not self.create_in_remnawave and not self.remnanode_token:
                raise ValueError(
                    "remnanode_token is required when create_in_remnawave is False"
                )
            if self.create_in_remnawave and not self.template_id:
                raise ValueError(
                    "template_id is required when create_in_remnawave is True"
                )
            if len(self.country_code) != 2:
                raise ValueError("country_code must be a 2-letter code in remnanode mode")
        else:  # haproxy
            if not self.haproxy_dest_ip.strip():
                raise ValueError("haproxy_dest_ip is required in haproxy mode")
        return self

    @field_validator("ip")
    @classmethod
    def validate_ipv4(cls, v: str) -> str:
        pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
        if not re.match(pattern, v):
            raise ValueError("Invalid IPv4 address")
        parts = v.split(".")
        if any(int(p) > 255 for p in parts):
            raise ValueError("Invalid IPv4 address octets")
        return v

    @field_validator("open_ports")
    @classmethod
    def validate_ports(cls, v: str) -> str:
        ports = [p.strip() for p in v.split(",") if p.strip()]
        for p in ports:
            if not p.isdigit() or not (1 <= int(p) <= 65535):
                raise ValueError(f"Invalid port: {p}")
        return ",".join(ports)


class RenewCertsRequest(BaseModel):
    ip: str
    ssh_user: str = "root"
    ssh_password: str
    ssh_port: int = 22
    domain: str
    cf_api_key: Optional[str] = None  # override stored acme.sh token if needed
