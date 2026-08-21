from __future__ import annotations

import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntegrationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    ssh_user: str = Field(default="root", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    ssh_key_ref: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    ssh_port: int = Field(default=22, ge=1, le=65535)

    @field_validator("address")
    @classmethod
    def ipv4_only(cls, value: str) -> str:
        try:
            return str(ipaddress.IPv4Address(value))
        except ipaddress.AddressValueError as exc:
            raise ValueError("target.address must be an IPv4 address") from exc


class IntegrationDeploymentRequest(BaseModel):
    """Narrow storefront contract: one eGames template and reserved-FQDN strategy."""

    model_config = ConfigDict(extra="forbid")

    external_order_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    target: IntegrationTarget
    reserved_domain: str = Field(min_length=4, max_length=253)
    sku_version: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._@:-]*$")
    template_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    country_code: str = Field(default="XX", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    acme_email: str = Field(min_length=3, max_length=254)
    new_ssh_port: int = Field(default=2222, ge=1, le=65535)
    remnanode_port: int = Field(default=2222, ge=1, le=65535)
    install_hysteria2: bool = True

    @field_validator("reserved_domain")
    @classmethod
    def fqdn(cls, value: str) -> str:
        pattern = (
            r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,}$"
        )
        if not re.fullmatch(pattern, value):
            raise ValueError("reserved_domain must be an FQDN")
        return value.lower()

    @field_validator("acme_email")
    @classmethod
    def email(cls, value: str) -> str:
        if not re.fullmatch(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", value):
            raise ValueError("invalid acme_email")
        return value

    @field_validator("country_code")
    @classmethod
    def uppercase_country(cls, value: str) -> str:
        return value.upper()


DeploymentState = Literal[
    "submitting", "queued", "deploying", "succeeded", "failed",
    "unknown", "cancellation_pending", "cancelled",
]
