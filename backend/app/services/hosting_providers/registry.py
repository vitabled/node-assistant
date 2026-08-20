"""Adapter registry — `kind` → adapter instance (Wave-9 Plan C, Ф1).

The single lookup point for the routes and the background sync: nothing outside
this package imports a vendor module directly, so adding a provider is one line
in `_MODULES`.

**Every module is imported under its own guard.** An adapter can bring an
optional dependency (yandex/oracle sign their requests with `cryptography`) or
simply be half-written, and one such module must never take the whole provider
list — and with it the infra-billing screen — down with it. A module that fails
to load is logged once and its `kind` is just absent from `schemas()`, which the
frontend already tolerates (it renders whatever kinds it is handed).
"""
from __future__ import annotations

import importlib
import logging
from typing import Optional

from app.services.hosting_providers.base import ProviderAdapter

log = logging.getLogger("hosting.registry")

# Module order = the order the UI lists the providers in, so it is stable and not
# dict-insertion luck. One module may export several kinds (regru: cloudvps API
# token + control-panel account are different credentials entirely).
_MODULES = ("ruvds", "beget", "veesp", "regru", "yandex", "openstack", "oracle",
            "aeza", "timeweb", "vdsina", "netangels",
            "digitalocean", "hetzner", "selectel",
            # Волна биллинг-адаптеров: ЕС, большие облака, хостеры со счетами.
            "ionos", "ovhcloud", "infomaniak", "latitude",
            "aws", "alibaba", "cloudru",
            "ishosting", "hostkey", "billmanager", "servers_com",
            # Wave-10 новые адаптеры
            "vultr", "linode")


def _exports(mod) -> list:
    """Adapters a module publishes: `ADAPTERS` (list) or a single `ADAPTER`."""
    found = getattr(mod, "ADAPTERS", None)
    if isinstance(found, (list, tuple)):
        return list(found)
    single = getattr(mod, "ADAPTER", None)
    return [single] if single is not None else []


def _load() -> dict[str, ProviderAdapter]:
    out: dict[str, ProviderAdapter] = {}
    for name in _MODULES:
        try:
            mod = importlib.import_module(f"app.services.hosting_providers.{name}")
        except Exception as exc:
            # ImportError covers the missing-dependency case, but a module can
            # also blow up while initialising — same outcome for us: skip it.
            log.warning("hosting-адаптер %s не загружен: %s", name, exc)
            continue
        for adapter in _exports(mod):
            kind = str(getattr(adapter, "KIND", "") or "")
            if not isinstance(adapter, ProviderAdapter) or not kind:
                log.warning("hosting-адаптер %s: экспорт без KIND пропущен", name)
                continue
            if kind in out:
                log.warning("hosting-адаптер %s: kind %r уже занят", name, kind)
                continue
            out[kind] = adapter
    return out


ADAPTERS: dict[str, ProviderAdapter] = _load()


def get(kind: str) -> Optional[ProviderAdapter]:
    """Adapter by `kind`, or None — an unknown kind is a caller's 404, not a raise."""
    return ADAPTERS.get(str(kind or "").strip())


def kinds() -> list[str]:
    return list(ADAPTERS)


def schemas() -> list[dict]:
    """Credential form + capabilities per provider, for the frontend."""
    return [
        {
            "kind": a.KIND,
            "title": a.TITLE or a.KIND,
            "caps": sorted(a.CAPS),
            "fields": [
                {"key": f.key, "label": f.label, "kind": f.kind,
                 "required": f.required}
                for f in a.FIELDS
            ],
        }
        for a in ADAPTERS.values()
    ]
