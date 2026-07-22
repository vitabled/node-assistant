"""Models for the «Хостинги» catalogue (Wave-4 Plan A)."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class Tariff(BaseModel):
    name: str = ""
    specs: str = ""          # free-text spec summary (CPU/RAM/disk/…)
    # Network channel width. Free text, not a number: providers quote a port
    # speed, a guarantee and a traffic cap together ("1 Гбит/с, 20 ТБ",
    # "10G unmetered") — there is no single numeric semantic to store.
    bandwidth: str = ""
    price: float = Field(default=0, ge=0)
    currency: str = "USD"
    period: str = "mo"       # mo | yr | hr | once (free-text label)


class Location(BaseModel):
    city: str = ""
    country_code: str = Field(default="", max_length=2)
    lat: float = Field(default=0, ge=-90, le=90)
    lng: float = Field(default=0, ge=-180, le=180)
    note: str = ""


class HostingBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    website: str = ""
    notes: str = ""
    features: str = ""
    tariffs: list[Tariff] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    # Optional link to an infra-billing provider (kept loose — independent module).
    provider_ref: Optional[str] = None
