"""Shared node-testing toolkit (Ф1, wave1): bash generators + xray-link parser.

`test_tools_install_script` is the SINGLE source for installing the test tools
(iperf3 + Ookla speedtest CLI + xray-core binary) — consumed by the test-server
deploy (Ф1), node deploy (Ф2) and panel deploy (Ф4). All installs are
idempotent and non-fatal: a missing optional tool logs `[warn]` instead of
failing the whole run.

`parse_xray_link` turns a vless/trojan/vmess/ss share-link into a full
xray-client config (socks inbound 127.0.0.1:10808 + the parsed outbound).
Share-links carry credentials — NEVER log the raw link, and keep it out of
every error message and generated script (only the parsed config, written on
the target and removed by trap, contains its fields).
"""

from __future__ import annotations

import base64
import json
import shlex
from urllib.parse import parse_qs, unquote, urlsplit


# ── install scripts ───────────────────────────────────────────


def test_tools_install_script() -> str:
    """Idempotent install of iperf3 + Ookla speedtest (python fallback) + xray."""
    return """\
export DEBIAN_FRONTEND=noninteractive
echo "[test-tools] Установка iperf3..."
apt-get install -y -qq iperf3 || { apt-get update -qq; apt-get install -y -qq iperf3; }

echo "[test-tools] Установка Ookla speedtest CLI..."
if ! command -v speedtest >/dev/null 2>&1; then
    { curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash \\
        && apt-get install -y -qq speedtest; } \\
        || echo "[warn] Ookla speedtest не установился — пробую python-версию"
fi
if ! command -v speedtest >/dev/null 2>&1; then
    apt-get install -y -qq speedtest-cli \\
        || echo "[warn] speedtest недоступен (ни Ookla, ни python-версия)"
fi

echo "[test-tools] Установка xray-core..."
if [ ! -x /usr/local/bin/xray ]; then
    TMPD=$(mktemp -d)
    if curl -fL -o "$TMPD/xray.zip" \\
        https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip; then
        command -v unzip >/dev/null 2>&1 || apt-get install -y -qq unzip || true
        { unzip -o "$TMPD/xray.zip" xray -d /usr/local/bin/ >/dev/null \\
            && chmod +x /usr/local/bin/xray; } \\
            || echo "[warn] xray-тест недоступен (распаковка не удалась)"
    else
        echo "[warn] xray-тест недоступен (не удалось скачать Xray-core)"
    fi
    rm -rf "$TMPD"
fi
echo "[test-tools] Готово."
"""


def iperf_server_script(port: int) -> str:
    """systemd unit `iperf3-server.service` listening on `port`, enabled now."""
    port = int(port)
    return f"""\
echo "[iperf3] Настройка сервиса iperf3-server (порт {port})..."
cat > /etc/systemd/system/iperf3-server.service <<EOF
[Unit]
Description=iperf3 server (node-assistant test tools)
After=network.target

[Service]
ExecStart=/usr/bin/iperf3 -s -p {port}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now iperf3-server
if systemctl is-active --quiet iperf3-server; then
    echo "[iperf3] Сервис запущен на порту {port}."
else
    echo "[err] iperf3-server не запустился — порт {port} возможно занят"
    exit 1
fi
"""


def iperf_client_script(
    host: str, port: int, with_ping: bool = False, with_traceroute: bool = False
) -> str:
    """iperf3 client run (JSON, marker-delimited) + optional ping/traceroute."""
    h = shlex.quote(host)
    p = int(port)
    parts = [
        f"""\
echo "IPERF_JSON_START"
iperf3 -c {h} -p {p} -J -t 10 || true
echo "IPERF_JSON_END"
"""
    ]
    if with_ping:
        parts.append(
            f"""\
echo "PING_START"
ping -c 10 -q {h} || true
echo "PING_END"
"""
        )
    if with_traceroute:
        parts.append(
            f"""\
command -v traceroute >/dev/null 2>&1 || apt-get install -y -qq traceroute >/dev/null 2>&1 || true
echo "TRACEROUTE_START"
traceroute -n -w 2 -m 20 {h} || true
echo "TRACEROUTE_END"
"""
        )
    return "\n".join(parts)


# ── xray share-link parser ────────────────────────────────────

# Parsers raise _LinkError with FIXED messages only; parse_xray_link converts
# anything else (int(), base64, json, urlsplit errors — whose default messages
# embed the offending value) into a generic ValueError so no link fragment can
# leak into logs.


class _LinkError(ValueError):
    pass


def _b64_str(data: str) -> str:
    data = data.strip()
    data += "=" * (-len(data) % 4)
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        try:
            raw = base64.urlsafe_b64decode(data)
        except Exception:
            raise _LinkError("Некорректная base64-часть ссылки")
    try:
        return raw.decode("utf-8")
    except Exception:
        raise _LinkError("Некорректная base64-часть ссылки")


def _port(value) -> int:
    try:
        p = int(value)
    except Exception:
        raise _LinkError("Некорректный порт в ссылке")
    if not 1 <= p <= 65535:
        raise _LinkError("Некорректный порт в ссылке")
    return p


def _split_hostport(s: str) -> tuple[str, int]:
    s = s.split("?", 1)[0].split("/", 1)[0]
    host, sep, port = s.rpartition(":")
    if not sep or not host:
        raise _LinkError("В ссылке нет host:port")
    return host, _port(port)


def _stream_settings(q: dict) -> dict:
    net = q.get("type") or "tcp"
    if net == "raw":  # xray's new name for tcp
        net = "tcp"
    if net not in ("tcp", "ws", "grpc", "xhttp"):
        raise _LinkError("Неподдерживаемый транспорт в ссылке")
    ss: dict = {"network": net}
    sec = q.get("security") or "none"
    if sec == "tls":
        ss["security"] = "tls"
        tls: dict = {}
        if q.get("sni"):
            tls["serverName"] = q["sni"]
        if q.get("fp"):
            tls["fingerprint"] = q["fp"]
        ss["tlsSettings"] = tls
    elif sec == "reality":
        ss["security"] = "reality"
        rs: dict = {}
        if q.get("sni"):
            rs["serverName"] = q["sni"]
        if q.get("pbk"):
            rs["publicKey"] = q["pbk"]
        if q.get("sid"):
            rs["shortId"] = q["sid"]
        if q.get("fp"):
            rs["fingerprint"] = q["fp"]
        ss["realitySettings"] = rs
    elif sec in ("none", ""):
        ss["security"] = "none"
    else:
        raise _LinkError("Неподдерживаемый security-режим в ссылке")
    if net == "ws":
        ws: dict = {"path": q.get("path") or "/"}
        if q.get("host"):
            ws["headers"] = {"Host": q["host"]}
        ss["wsSettings"] = ws
    elif net == "grpc":
        ss["grpcSettings"] = {"serviceName": q.get("serviceName") or ""}
    elif net == "xhttp":
        xh: dict = {"path": q.get("path") or "/"}
        if q.get("host"):
            xh["host"] = q["host"]
        ss["xhttpSettings"] = xh
    return ss


def _query(u) -> dict:
    return {k: v[0] for k, v in parse_qs(u.query).items()}


def _parse_vless(link: str) -> dict:
    u = urlsplit(link)
    uid = unquote(u.username or "")
    host = u.hostname or ""
    if not uid or not host:
        raise _LinkError("В vless-ссылке нет uuid@host")
    port = _port(u.port)
    q = _query(u)
    user: dict = {"id": uid, "encryption": q.get("encryption") or "none"}
    if q.get("flow"):
        user["flow"] = q["flow"]
    return {
        "protocol": "vless",
        "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
        "streamSettings": _stream_settings(q),
    }


def _parse_trojan(link: str) -> dict:
    u = urlsplit(link)
    password = unquote(u.username or "")
    host = u.hostname or ""
    if not password or not host:
        raise _LinkError("В trojan-ссылке нет password@host")
    port = _port(u.port)
    q = _query(u)
    return {
        "protocol": "trojan",
        "settings": {
            "servers": [{"address": host, "port": port, "password": password}]
        },
        "streamSettings": _stream_settings(q),
    }


def _parse_vmess(link: str) -> dict:
    payload = link[len("vmess://") :].split("#", 1)[0]
    try:
        data = json.loads(_b64_str(payload))
    except _LinkError:
        raise
    except Exception:
        raise _LinkError("vmess-ссылка не содержит валидный JSON")
    if not isinstance(data, dict):
        raise _LinkError("vmess-ссылка не содержит валидный JSON")
    host = str(data.get("add") or "")
    uid = str(data.get("id") or "")
    if not host or not uid:
        raise _LinkError("В vmess-ссылке нет add/id")
    port = _port(data.get("port"))
    try:
        alter_id = int(data.get("aid") or 0)
    except Exception:
        alter_id = 0
    tls_on = str(data.get("tls") or "").lower() in ("tls", "1", "true")
    q = {
        "type": str(data.get("net") or "tcp"),
        "security": "tls" if tls_on else "none",
        "sni": str(data.get("sni") or data.get("host") or ""),
        "path": str(data.get("path") or ""),
        "host": str(data.get("host") or ""),
    }
    return {
        "protocol": "vmess",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": port,
                    "users": [{"id": uid, "alterId": alter_id, "security": "auto"}],
                }
            ]
        },
        "streamSettings": _stream_settings(q),
    }


def _parse_ss(link: str) -> dict:
    body = link[len("ss://") :].split("#", 1)[0]
    if "@" in body:
        userinfo, _, hostpart = body.rpartition("@")
        creds = _b64_str(unquote(userinfo))
    else:
        decoded = _b64_str(unquote(body))
        creds, sep, hostpart = decoded.rpartition("@")
        if not sep:
            raise _LinkError("В ss-ссылке нет host:port")
    method, sep, password = creds.partition(":")
    if not sep or not method or not password:
        raise _LinkError("В ss-ссылке нет method:password")
    host, port = _split_hostport(hostpart)
    return {
        "protocol": "shadowsocks",
        "settings": {
            "servers": [
                {"address": host, "port": port, "method": method, "password": password}
            ]
        },
    }


_PARSERS = {
    "vless": _parse_vless,
    "trojan": _parse_trojan,
    "vmess": _parse_vmess,
    "ss": _parse_ss,
}


def parse_xray_link(link: str) -> dict:
    """Share-link → full xray-client config. ValueError on unsupported/garbage
    input; error messages never contain link fragments (links can carry creds)."""
    link = (link or "").strip()
    scheme = link.split("://", 1)[0].lower() if "://" in link else ""
    parser = _PARSERS.get(scheme)
    if parser is None:
        raise ValueError(
            "Неподдерживаемая схема ссылки (ожидается vless/trojan/vmess/ss)"
        )
    try:
        outbound = parser(link)
    except _LinkError as e:
        raise ValueError(str(e)) from None
    except Exception:
        raise ValueError("Некорректная ссылка") from None
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True},
            }
        ],
        "outbounds": [outbound],
    }


def xray_link_speedtest_script(link: str) -> str:
    """Speedtest through the link's tunnel on the target box: write the parsed
    config (heredoc), start xray, wait for the socks tunnel, measure down/up/ping
    via speed.cloudflare.com, always kill xray + remove the config (trap). The
    raw link is NOT embedded — only the parsed config."""
    cfg_json = json.dumps(parse_xray_link(link), indent=2)
    return f"""\
set -u
CFG="/tmp/xray-test-$$-$RANDOM.json"
cat > "$CFG" <<'XRAYCFG'
{cfg_json}
XRAYCFG
chmod 600 "$CFG"
if [ ! -x /usr/local/bin/xray ]; then
    echo "[warn] xray-тест недоступен (нет /usr/local/bin/xray)"
    rm -f "$CFG"
    exit 0
fi
nohup /usr/local/bin/xray run -c "$CFG" >/dev/null 2>&1 &
XRAY_PID=$!
trap 'kill $XRAY_PID 2>/dev/null || true; rm -f "$CFG"' EXIT
echo "[xray] Ожидание туннеля..."
if ! timeout 15 bash -c 'until curl -s --socks5 127.0.0.1:10808 -o /dev/null --max-time 2 http://cp.cloudflare.com; do sleep 1; done'; then
    echo "[warn] туннель не поднялся за 15 секунд"
    exit 0
fi
echo "[xray] Замер download..."
DOWN=$(curl --socks5-hostname 127.0.0.1:10808 -o /dev/null -sS -w '%{{speed_download}}' --max-time 60 'https://speed.cloudflare.com/__down?bytes=104857600' || echo 0)
echo "XRAY_DOWN=$DOWN"
echo "[xray] Замер upload..."
UP=$(head -c 20971520 /dev/zero | curl --socks5-hostname 127.0.0.1:10808 -sS -o /dev/null -w '%{{speed_upload}}' --max-time 60 -X POST --data-binary @- 'https://speed.cloudflare.com/__up' || echo 0)
echo "XRAY_UP=$UP"
PINGT=$(curl --socks5-hostname 127.0.0.1:10808 -sS -o /dev/null -w '%{{time_connect}}' --max-time 10 http://cp.cloudflare.com || echo 0)
echo "XRAY_PING=$PINGT"
echo "[xray] Тест завершён."
"""
