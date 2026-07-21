"""Wave-5 Plan D — user config templates, modelled after Remnawave
subscription-templates. Six client-core kinds; JSON cores store a JSON object,
YAML cores store human-readable YAML text (base64-encoded only at the Remnawave
export/import boundary)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

TemplateKind = Literal["xray-json", "xray-base64", "mihomo", "stash", "clash", "singbox"]

JSON_KINDS = {"xray-json", "xray-base64", "singbox"}
YAML_KINDS = {"mihomo", "clash", "stash"}

# local kind ↔ Remnawave templateType enum (UPPERCASE)
KIND_TO_ENUM = {
    "xray-json": "XRAY_JSON", "xray-base64": "XRAY_BASE64", "mihomo": "MIHOMO",
    "stash": "STASH", "clash": "CLASH", "singbox": "SINGBOX",
}
ENUM_TO_KIND = {v: k for k, v in KIND_TO_ENUM.items()}


class ConfigTemplateBody(BaseModel):
    name: str
    kind: TemplateKind
    content_json: Optional[dict] = None
    content_yaml: Optional[str] = None
    note: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Имя не может быть пустым")
        if len(v) > 255:
            raise ValueError("Имя слишком длинное (макс 255)")
        return v

    @model_validator(mode="after")
    def _content_matches_kind(self):
        if self.content_json is not None and self.content_yaml is not None:
            raise ValueError("content_json и content_yaml взаимоисключающи")
        if self.kind in YAML_KINDS and self.content_json is not None:
            raise ValueError("для YAML-ядра задавайте content_yaml, не content_json")
        if self.kind in JSON_KINDS and self.content_yaml is not None:
            raise ValueError("для JSON-ядра задавайте content_json, не content_yaml")
        return self
