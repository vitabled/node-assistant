"""Models for the «Хостинги» catalogue (Wave-4 Plan A)."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator


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


class AsnRef(BaseModel):
    """An autonomous system a hosting operates on (Wave-8 §6). Structured rather
    than a bare string because §7 «Анализ подписки» fills name/website from
    RDAP/PeeringDB — the number alone would lose that."""
    number: int = Field(default=0, ge=0)
    name: str = ""
    website: str = ""


class HostingBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    website: str = ""
    notes: str = ""
    features: str = ""
    # Account-level free-form tags (Wave-8 §1). Normalised: trimmed, CR/LF-free,
    # ≤24 chars each, deduped, ≤10 per hosting.
    tags: list[str] = Field(default_factory=list)
    tariffs: list[Tariff] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    asns: list[AsnRef] = Field(default_factory=list)
    # Optional link to an infra-billing provider (kept loose — independent module).
    provider_ref: Optional[str] = None

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v or []:
            # split() collapses any whitespace run (incl. CR/LF) and trims.
            t = " ".join((raw or "").split())[:24]
            if t and t not in out:
                out.append(t)
            if len(out) >= 10:
                break
        return out
