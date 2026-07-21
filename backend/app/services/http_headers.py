"""Pure HTTP-header + host-string validation (Wave-5 Plan F). No dependencies.

Header values must not contain CR/LF (response-splitting). Host/SNI/path strings
are validated against a shell-safe allowlist because they are (or will be)
interpolated into root-run configs (nginx/Xray) — closes the §5 «Forward note».
"""
from __future__ import annotations

import re

# RFC 7230 header field-name token.
HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

# Hostname / IP charset (host, sni) and a conservative URL-path charset.
_HOST_RE = re.compile(r"^[A-Za-z0-9.:_\-]*$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._/~%?=&:\-]*$")


def validate_header_name(name: str) -> bool:
    return bool(HEADER_NAME_RE.match(name or ""))


def validate_header_value(value: str) -> bool:
    v = value or ""
    return "\r" not in v and "\n" not in v


def is_safe_host(v: str) -> bool:
    return bool(_HOST_RE.match(v or ""))


def is_safe_path(v: str) -> bool:
    return bool(_PATH_RE.match(v or ""))
