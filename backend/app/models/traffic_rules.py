from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field
import uuid as _uuid
from datetime import datetime, timezone

TrafficPeriod = Literal["DAY", "WEEK", "MONTH", "NO_RESET"]
TrafficScope  = Literal["ALL", "SQUAD"]
SyncStatus    = Literal["pending", "synced", "error"]

GiB = 1_073_741_824  # bytes per GiB


class TrafficRule(BaseModel):
    id:             str = Field(default_factory=lambda: str(_uuid.uuid4()))
    node_uuid:      str
    node_name:      str
    scope:          TrafficScope
    squad_uuids:    list[str] = Field(default_factory=list)
    squad_names:    list[str] = Field(default_factory=list)
    limit_gb:       float            # 0 = unlimited
    period:         TrafficPeriod
    created_at:     str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_synced_at: Optional[str] = None
    sync_status:    SyncStatus = "pending"
    sync_error:     Optional[str] = None


class TrafficRuleCreate(BaseModel):
    node_uuid:   str
    node_name:   str
    scope:       TrafficScope
    squad_uuids: list[str] = Field(default_factory=list)
    squad_names: list[str] = Field(default_factory=list)
    limit_gb:    float = Field(ge=0)
    period:      TrafficPeriod


class TrafficRuleUpdate(BaseModel):
    scope:       Optional[TrafficScope]  = None
    squad_uuids: Optional[list[str]]     = None
    squad_names: Optional[list[str]]     = None
    limit_gb:    Optional[float]         = Field(default=None, ge=0)
    period:      Optional[TrafficPeriod] = None
