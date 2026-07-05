"""subs-aggregator — a tiny stdlib HTTP server that merges every account's
tracked subscriptions into ONE combined subscription for the shared xray-checker.

The shared xray-checker container points its SUBSCRIPTION_URL at this service
(`http://subs-aggregator:8080/sub`). We fetch the active subscription set from
the backend's internal endpoint (`AGG_SOURCE_URL`, only reachable on
node-assistant-net), fetch each upstream subscription, tag every proxy entry
`account:sub` in its remark, and return the concatenated base64 list.

Error policy: if an upstream subscription fails, we record its `last_error` and
serve the LAST-GOOD configs (or skip it) — we do NOT retry that upstream until a
`POST /refresh` clears it (a refresh button in the dashboard triggers this).

No third-party deps — stdlib only, so the image stays tiny.
"""
import base64
import ipaddress
import json
import os
import socket
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGG_SOURCE_URL = os.getenv("AGG_SOURCE_URL", "http://backend:8000/internal/agg-subs")
AGG_TOKEN = os.getenv("AGG_TOKEN", "").strip()  # shared secret for source/refresh
PORT = int(os.getenv("PORT", "8080"))
FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "12"))
MAX_SUB_BYTES = int(os.getenv("MAX_SUB_BYTES", str(4 * 1024 * 1024)))  # cap upstream reads
# Test-only escape hatch: the SSRF guard blocks private/loopback hosts, but the
# functional smoke serves its mock upstream on 127.0.0.1. NEVER set in prod.
_ALLOW_PRIVATE = os.getenv("ALLOW_PRIVATE_HOSTS", "").strip() == "1"


def _host_is_public(host: str) -> bool:
    if _ALLOW_PRIVATE:
        return True
    """Resolve `host` and require EVERY resolved IP to be a public (routable)
    address — blocks SSRF to loopback / link-local (IMDS 169.254.169.254) /
    private / reserved ranges (e.g. other services on node-assistant-net)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _safe_fetch(url: str, timeout: float = FETCH_TIMEOUT) -> bytes:
    """Fetch a USER-supplied upstream subscription with SSRF guards: only
    http/https, only public hosts, size-capped read. (The trusted internal
    source URL uses plain `_fetch`, not this.)"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"scheme not allowed: {parsed.scheme or '(none)'}")
    host = parsed.hostname or ""
    if not host or not _host_is_public(host):
        raise ValueError("host not allowed (non-public / unresolvable)")
    req = urllib.request.Request(url, headers={"User-Agent": "subs-aggregator/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_SUB_BYTES + 1)
    if len(data) > MAX_SUB_BYTES:
        raise ValueError("subscription too large")
    return data

# Per-subscription cache: key -> {configs: [str], error, tag, count, url}
# `configs` are already tagged. Survives across /sub calls; re-fetched only on a
# first-ever fetch, a URL change (auto-detected vs the source), or /refresh — so
# a broken upstream isn't hammered. Guarded by _LOCK (ThreadingHTTPServer).
_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()


def _sub_key(account_id: str, sub_id: str) -> str:
    return f"{account_id}:{sub_id}"


def _fetch_source() -> bytes:
    """Fetch the TRUSTED internal source list from the backend (an internal
    host, so no SSRF guard) — carries the shared AGG_TOKEN header when set."""
    headers = {"User-Agent": "subs-aggregator/1"}
    if AGG_TOKEN:
        headers["X-Agg-Token"] = AGG_TOKEN
    req = urllib.request.Request(AGG_SOURCE_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read()


def _decode_sub_body(raw: bytes) -> list[str]:
    """A subscription body is a base64-encoded newline list of proxy URIs (or,
    less commonly, already-plaintext). Return the non-empty config lines."""
    text = raw.decode("utf-8", "replace").strip()
    # Try base64 first (standard subscription encoding); fall back to plaintext.
    try:
        padded = text + "=" * (-len(text) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", "replace")
        if "://" in decoded:
            text = decoded
    except Exception:
        pass
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _tag_config(line: str, tag: str) -> str:
    """Prefix a proxy URI's remark with `tag` (account:sub) so the checker's
    per-proxy name carries the owner. vmess:// stores its remark as the JSON
    "ps" field (base64 body); every other scheme uses the `#remark` fragment."""
    if line.startswith("vmess://"):
        try:
            body = line[len("vmess://"):]
            padded = body + "=" * (-len(body) % 4)
            obj = json.loads(base64.b64decode(padded).decode("utf-8", "replace"))
            obj["ps"] = f"{tag}|{obj.get('ps', '')}"
            reenc = base64.b64encode(json.dumps(obj).encode()).decode()
            return "vmess://" + reenc
        except Exception:
            return line
    base, sep, frag = line.partition("#")
    orig = urllib.parse.unquote(frag) if sep else ""
    new_remark = f"{tag}|{orig}" if orig else tag
    return f"{base}#{urllib.parse.quote(new_remark)}"


def _load_sub(account_id: str, sub_id: str, url: str) -> dict:
    """Fetch + tag one upstream subscription (network I/O OUTSIDE the lock).
    Stores `url` in the entry so a later URL change is auto-detected. On error
    keeps any previously-cached configs and records `error` (no-retry)."""
    key = _sub_key(account_id, sub_id)
    tag = key  # tag == "account:sub"
    try:
        lines = _decode_sub_body(_safe_fetch(url))
        tagged = [_tag_config(ln, tag) for ln in lines]
        entry = {"configs": tagged, "error": None, "tag": tag, "count": len(tagged), "url": url}
    except Exception as exc:  # upstream down / malformed
        with _LOCK:
            prev = _CACHE.get(key, {})
        entry = {
            "configs": prev.get("configs", []),
            "error": str(exc)[:200],
            "tag": tag,
            "count": len(prev.get("configs", [])),
            "url": url,
        }
    with _LOCK:
        _CACHE[key] = entry
    return entry


def _source_set() -> list[dict]:
    """The active subscription set from the backend: [{account_id, sub_id, url}]."""
    raw = _fetch_source()
    data = json.loads(raw.decode("utf-8", "replace"))
    return data if isinstance(data, list) else data.get("subscriptions", [])


def _aggregate() -> tuple[str, list[dict]]:
    """Build the combined base64 subscription + a per-sub status list. An upstream
    is (re)fetched when it's never been cached, cleared by /refresh, OR its URL
    changed in the source (auto-invalidation — doesn't rely on the backend's
    best-effort notify). Cache entries for subs no longer in the source are
    evicted (bounds memory)."""
    status = []
    all_configs: list[str] = []
    live_keys = set()
    for item in _source_set():
        aid = str(item.get("account_id", ""))
        sid = str(item.get("sub_id", ""))
        url = item.get("url", "")
        if not url:
            continue
        key = _sub_key(aid, sid)
        live_keys.add(key)
        with _LOCK:
            entry = _CACHE.get(key)
        if entry is None or entry.get("url") != url:  # new / refreshed / URL changed
            entry = _load_sub(aid, sid, url)
        all_configs.extend(entry["configs"])
        status.append({"key": key, "error": entry["error"], "count": entry["count"]})
    # Evict cache entries whose sub is no longer in the active source set.
    with _LOCK:
        for k in [k for k in _CACHE if k not in live_keys]:
            del _CACHE[k]
    combined = "\n".join(all_configs)
    b64 = base64.b64encode(combined.encode()).decode()
    return b64, status


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._send(200, b"ok")
        elif path == "/sub":
            try:
                b64, _ = _aggregate()
                self._send(200, b64.encode())
            except Exception as exc:
                self._send(502, f"aggregate error: {exc}".encode())
        elif path == "/status":
            try:
                _, status = _aggregate()
                self._send(200, json.dumps({"subscriptions": status}).encode(), "application/json")
            except Exception as exc:
                self._send(502, json.dumps({"error": str(exc)}).encode(), "application/json")
        else:
            self._send(404, b"not found")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/refresh":
            # Shared-secret guard (when AGG_TOKEN set): only the backend, which
            # holds the token, may bust the cache — not any container on the net.
            if AGG_TOKEN and self.headers.get("X-Agg-Token", "") != AGG_TOKEN:
                self._send(403, b"forbidden")
                return
            # Body {sub_key} clears one upstream; empty body clears all → next
            # /sub re-fetches (this is the ONLY way a failed upstream is retried).
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
            key = body.get("sub_key")
            if key:
                _CACHE.pop(key, None)
            else:
                _CACHE.clear()
            self._send(200, json.dumps({"ok": True}).encode(), "application/json")
        else:
            self._send(404, b"not found")

    def log_message(self, *_args) -> None:  # quiet — no per-request stderr spam
        pass


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[subs-aggregator] listening on :{PORT}, source={AGG_SOURCE_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
