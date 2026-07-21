"""Wave-4 Plan C (E5) — lightweight scrape of the Remnawave panel's Prometheus
metrics (`:3001/metrics`, basic-auth) over SSH.

R1/R2 (recon, documented in CLAUDE.md §9e):
  - The panel exposes Prometheus metrics on **127.0.0.1:METRICS_PORT** (default
    **3001**), behind HTTP basic-auth `METRICS_USER`/`METRICS_PASS` (both in the
    panel `/opt/remnawave/.env`; `METRICS_PASS` is a protected secret key).
  - Not published externally → we scrape it **on the panel box over SSH**
    (`curl -u user:pass 127.0.0.1:3001/metrics`). Creds are read from the .env on
    the box and used on the box; they never leave it and never hit our logs
    (the whole thing runs through the SILENT `get_script_output` channel).
  - Known metric names (Remnawave ≥2.x): `remnawave_users_online_stats` (online
    distinct users), `remnawave_users_status{...}` (users per status
    ACTIVE/DISABLED/LIMITED/EXPIRED), `remnawave_node_status{...}` (per-node
    1=connected/0=disconnected). ~30 more gauges exist (per-node CPU/RAM/traffic)
    — we surface a curated few + the raw metric count.

Only the pure parser/summariser lives here; the SSH round-trip is in the route.
"""
from __future__ import annotations

import re
from typing import Any

# `name{label="v",...} value [timestamp]` — timestamp optional and ignored.
_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>[^\s]+)"
)
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def _to_float(raw: str) -> float | None:
    """Parse a Prometheus value token (handles Nan/+Inf/-Inf → None)."""
    try:
        v = float(raw)
    except ValueError:
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN/±Inf
        return None
    return v


def parse_prometheus(text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse Prometheus text-exposition into {metric_name: [{labels, value}]}.
    Comment/blank lines and unparseable/NaN samples are skipped. Pure — no I/O."""
    out: dict[str, list[dict[str, Any]]] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        value = _to_float(m.group("value"))
        if value is None:
            continue
        labels = {k: v for k, v in _LABEL_RE.findall(m.group("labels") or "")}
        out.setdefault(m.group("name"), []).append({"labels": labels, "value": value})
    return out


def _samples(parsed: dict[str, list[dict[str, Any]]], *names: str) -> list[dict[str, Any]]:
    """Samples for the first present metric name (exact match)."""
    for n in names:
        if n in parsed:
            return parsed[n]
    return []


def summarize(parsed: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Curated indicators from the parsed metrics. Missing metric → empty/zero
    (the panel version may rename gauges; the UI degrades gracefully)."""
    # Online users — sum across samples (usually a single gauge).
    online = _samples(parsed, "remnawave_users_online_stats", "remnawave_users_online")
    users_online = int(round(sum(s["value"] for s in online))) if online else None

    # Users by status — group by the `status` label (fallback: first label value).
    by_status: dict[str, int] = {}
    for s in _samples(parsed, "remnawave_users_status"):
        lbl = s["labels"]
        key = lbl.get("status") or (next(iter(lbl.values()), None)) or "unknown"
        by_status[key] = by_status.get(key, 0) + int(round(s["value"]))

    # Per-node connection status (1=connected).
    node_samples = _samples(parsed, "remnawave_node_status")
    nodes = [
        {
            "name": s["labels"].get("node_name")
            or s["labels"].get("name")
            or s["labels"].get("node_uuid")
            or "?",
            "online": s["value"] >= 1,
        }
        for s in node_samples
    ]
    nodes_total = len(nodes)
    nodes_online = sum(1 for n in nodes if n["online"])

    return {
        "users_online": users_online,
        "users_by_status": by_status,
        "nodes_online": nodes_online,
        "nodes_total": nodes_total,
        "nodes": nodes[:50],  # cap for the UI
        "metric_count": len(parsed),
    }


# ── SSH scrape script ─────────────────────────────────────────────
# Runs on the panel box via the SILENT channel (stdin, not argv). Reads the
# basic-auth creds from the panel .env and curls the loopback metrics port. The
# creds are used on the box and NEVER echoed; only the metrics text is returned.
_METRICS_SCRAPE_SCRIPT = r"""
set -u
ENV=/opt/remnawave/.env
if [ ! -f "$ENV" ]; then echo __NO_ENV__; exit 0; fi
_val() { grep -E "^$1=" "$ENV" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"; }
MU=$(_val METRICS_USER)
MP=$(_val METRICS_PASS)
MPORT=$(_val METRICS_PORT | tr -dc '0-9'); [ -z "$MPORT" ] && MPORT=3001
echo __METRICS_BEGIN__
curl -fsS --max-time 10 -u "$MU:$MP" "http://127.0.0.1:$MPORT/metrics" 2>/dev/null || echo __CURL_FAIL__
"""


def metrics_scrape_script() -> str:
    return _METRICS_SCRAPE_SCRIPT
