from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field


class HostTemplateBody(BaseModel):
    """A LOCAL Remnawave-host template (Ф11). Purely stored — no Remnawave API is
    called; the template is applied later at deploy time. Mirrors the Remnawave
    host form 1:1. Required: remark, address, port (the «Сохранить» gate)."""
    # visibility + basics
    visible: bool = True
    remark: str = Field(..., min_length=1, max_length=200)
    inbound: str = ""                         # "" = «Инбаунд не выбран»
    address: str = Field(..., min_length=1)
    port: int = Field(..., ge=1, le=65535)
    tag: str = "ROUTING_HOST"
    nodes: list[str] = Field(default_factory=list)
    exclude_squads: list[str] = Field(default_factory=list)

    # advanced — connection overrides
    sni: str = ""
    sni_from_address: bool = False
    sni_empty: bool = False
    host: str = ""
    path: str = ""
    security_layer: str = "default"
    alpn: str = ""
    fingerprint: str = ""
    vless_route_id: int = Field(0, ge=0, le=65535)   # 0 = off, else 1..65535
    hide_host: bool = False
    exclude_sub_types: list[str] = Field(default_factory=list)  # xray_json/base64/mihomo/stash/singbox/clash

    # xray json & raw sub-config editors (stored opaque)
    xray_json_template: str = ""
    xhttp: Optional[dict[str, Any]] = None
    mux: Optional[dict[str, Any]] = None
    sockopt: Optional[dict[str, Any]] = None
    final_mask: Optional[dict[str, Any]] = None

    # misc
    server_description: str = Field("", max_length=30)
    shuffle_host: bool = False
    allow_insecure: bool = False
    x25519mlkem768: bool = False
