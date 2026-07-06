from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, field_validator


def _validate_sub_url(v: str) -> str:
    """Subscription URLs are fetched server-side by the aggregator, so restrict
    the scheme to http/https here (defence-in-depth alongside the aggregator's
    SSRF host guard — blocks file://, ftp://, gopher://, etc. at creation)."""
    v = v.strip()
    if not v:
        raise ValueError("URL не может быть пустым")
    if not (v.startswith("http://") or v.startswith("https://")):
        raise ValueError("URL подписки должен начинаться с http:// или https://")
    return v


class Subscription(BaseModel):
    id: str
    url: str
    # `background` = always part of the aggregate the shared checker probes.
    # Non-background subs are only aggregated while transiently selected (Ф9).
    background: bool = False
    enabled: bool = True
    last_error: Optional[str] = None


class SubscriptionCreate(BaseModel):
    url: str = Field(..., min_length=1)
    background: bool = False

    _v_url = field_validator("url")(_validate_sub_url)


class SubscriptionUpdate(BaseModel):
    url: Optional[str] = None
    background: Optional[bool] = None
    enabled: Optional[bool] = None

    @field_validator("url")
    @classmethod
    def _v_url(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _validate_sub_url(v)
