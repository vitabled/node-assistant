"""Simple JSON-file persistence for settings and templates."""
import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_SETTINGS_FILE       = DATA_DIR / "settings.json"
_TEMPLATES_FILE      = DATA_DIR / "templates.json"
_TRAFFIC_RULES_FILE  = DATA_DIR / "traffic_rules.json"


def _read(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_settings() -> dict:
    return _read(_SETTINGS_FILE)


def save_settings(data: dict) -> None:
    _write(_SETTINGS_FILE, data)


def load_templates() -> list:
    return _read(_TEMPLATES_FILE).get("templates", [])


def save_templates(templates: list) -> None:
    _write(_TEMPLATES_FILE, {"templates": templates})


def load_traffic_rules() -> list:
    raw = _read(_TRAFFIC_RULES_FILE)
    return raw if isinstance(raw, list) else []


def save_traffic_rules(rules: list) -> None:
    _write(_TRAFFIC_RULES_FILE, rules)
