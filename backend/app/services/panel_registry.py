"""Resolving a Remnawave panel out of the per-account registry (Wave-7 Plan C).

The registry landed in Wave-5 Plan K, but only the settings screen used it: every
other caller read `AppSettings.remnawave`, which the model validator projects from
the ACTIVE panel. That was fine while there was one panel; it is not fine once a
screen wants to say "sync from THAT panel".

This module is the single place that turns an optional `panel_id` into a panel (or
a client). Callers pass "" to mean "the active one", which keeps every existing
route behaving exactly as before.

⚠️ An UNKNOWN id raises instead of falling back to the active panel. A silent
fallback would write a config template into the wrong panel on a typo — the one
failure mode that is expensive and invisible.
"""
from __future__ import annotations

from typing import Optional

from app.models.settings import AppSettings, PanelEntry
from app.services import storage
from app.services.remnawave_client import RemnavaveClient


class PanelNotFound(KeyError):
    """No panel with the requested id in this account's registry."""


class PanelNotConfigured(ValueError):
    """The resolved panel has no url/token yet."""


def _settings(account_id: Optional[str] = None) -> AppSettings:
    return AppSettings(**storage.load_settings(account_id))


def list_panels(account_id: Optional[str] = None) -> list[PanelEntry]:
    """All panels, with the legacy single-config migration already applied by the
    `AppSettings` validator."""
    return _settings(account_id).remnawave_registry.panels


def active_panel_id(account_id: Optional[str] = None) -> str:
    return _settings(account_id).remnawave_registry.active_panel_id


def resolve(panel_id: str = "", account_id: Optional[str] = None) -> PanelEntry:
    """Empty `panel_id` → the active panel. Unknown id → PanelNotFound."""
    reg = _settings(account_id).remnawave_registry
    if not reg.panels:
        raise PanelNotConfigured("Remnawave не настроен")
    if not panel_id:
        # The validator guarantees active_panel_id points at a real entry.
        return next((p for p in reg.panels if p.id == reg.active_panel_id), reg.panels[0])
    entry = next((p for p in reg.panels if p.id == panel_id), None)
    if entry is None:
        raise PanelNotFound(panel_id)
    return entry


def client_for(panel_id: str = "", account_id: Optional[str] = None) -> RemnavaveClient:
    entry = resolve(panel_id, account_id)
    if not entry.panel_url or not entry.api_token:
        raise PanelNotConfigured("Remnawave не настроен")
    return RemnavaveClient(entry.panel_url, entry.api_token)
