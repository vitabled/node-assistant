"""Per-account JSON-file persistence for settings, templates and traffic rules.

Each account's files live under `DATA_DIR/accounts/<id>/`. The account is
resolved from the `current_account` ContextVar (set per-request by the
`require_account` dependency); background callers with no request context (e.g.
the xray-checker poller) pass an explicit `account_id`.
"""

import json
from pathlib import Path
from typing import Any, Optional

from app.services import accounts


def _dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid)


def _read(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_settings(account_id: Optional[str] = None) -> dict:
    return _read(_dir(account_id) / "settings.json")


def save_settings(data: dict, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "settings.json", data)


def load_templates(account_id: Optional[str] = None) -> list:
    return _read(_dir(account_id) / "templates.json").get("templates", [])


def save_templates(templates: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "templates.json", {"templates": templates})


def load_traffic_rules(account_id: Optional[str] = None) -> list:
    raw = _read(_dir(account_id) / "traffic_rules.json")
    return raw if isinstance(raw, list) else []


def save_traffic_rules(rules: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "traffic_rules.json", rules)


def load_subscriptions(account_id: Optional[str] = None) -> list:
    return _read(_dir(account_id) / "subscriptions.json").get("subscriptions", [])


def save_subscriptions(subs: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "subscriptions.json", {"subscriptions": subs})


def load_domains(account_id: Optional[str] = None) -> list:
    return _read(_dir(account_id) / "domains.json").get("domains", [])


def save_domains(domains: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "domains.json", {"domains": domains})


def load_hosts(account_id: Optional[str] = None) -> list:
    return _read(_dir(account_id) / "hosts.json").get("hosts", [])


def save_hosts(hosts: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "hosts.json", {"hosts": hosts})


def load_checkers(account_id: Optional[str] = None) -> list:
    return _read(_dir(account_id) / "checkers.json").get("checkers", [])


def save_checkers(checkers: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "checkers.json", {"checkers": checkers})


def load_rules(account_id: Optional[str] = None) -> list:
    return _read(_dir(account_id) / "rules.json").get("rules", [])


def save_rules(rules: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "rules.json", {"rules": rules})


def load_testservers(account_id: Optional[str] = None) -> list:
    return _read(_dir(account_id) / "testservers.json").get("testservers", [])


def save_testservers(servers: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "testservers.json", {"testservers": servers})


def load_certwarden(account_id: Optional[str] = None) -> dict:
    """Single Certwarden-server registry object per account (or {} if none)."""
    return _read(_dir(account_id) / "certwarden.json")


def save_certwarden(data: dict, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "certwarden.json", data)


def load_netbird(account_id: Optional[str] = None) -> dict:
    """Single Netbird control-plane registry object per account (or {} if none)."""
    return _read(_dir(account_id) / "netbird.json")


def save_netbird(data: dict, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "netbird.json", data)


def load_api_tokens(account_id: Optional[str] = None) -> list:
    return _read(_dir(account_id) / "api_tokens.json").get("tokens", [])


def save_api_tokens(tokens: list, account_id: Optional[str] = None) -> None:
    _write(_dir(account_id) / "api_tokens.json", {"tokens": tokens})
