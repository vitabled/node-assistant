import pytest
from pydantic import ValidationError

from app.api.regru_vip import (
    RegruVipDeployRequest,
    build_htaccess,
    build_nginx_config,
    build_xray_inbound,
)


def _request(**overrides):
    values = {
        "ip": "203.0.113.10",
        "ssh_password": "secret",
        "your_site": "vip.example.com",
        "ws_path": "/api/v3/media/ws/",
        "xray_port": 12080,
        "xray_host": "origin.example.com",
    }
    values.update(overrides)
    return RegruVipDeployRequest(**values)


def test_request_normalizes_domains_and_websocket_path():
    req = _request(your_site=" VIP.Example.COM ", ws_path="api/v3/media/ws/")
    assert req.your_site == "vip.example.com"
    assert req.ws_path == "/api/v3/media/ws"


@pytest.mark.parametrize(
    "field,value",
    [
        ("your_site", "bad domain"),
        ("xray_host", "bad;host"),
        ("ws_path", "/bad path"),
        ("ws_path", "/"),
    ],
)
def test_request_rejects_shell_or_nginx_injection(field, value):
    with pytest.raises(ValidationError):
        _request(**{field: value})


def test_generated_xray_inbound_matches_vless_ws_contract():
    inbound = build_xray_inbound(_request())
    assert inbound["tag"] == "regru-vip-ws"
    assert inbound["listen"] == "127.0.0.1"
    assert inbound["port"] == 12080
    assert inbound["protocol"] == "vless"
    assert inbound["settings"] == {"clients": [], "decryption": "none"}
    assert inbound["streamSettings"]["wsSettings"] == {
        "path": "/api/v3/media/ws",
        "host": "origin.example.com",
    }


def test_generated_nginx_has_both_ws_locations_and_origin_names():
    config = build_nginx_config(_request())
    assert "server_name vip.example.com 203.0.113.10;" in config
    assert "server 127.0.0.1:12080;" in config
    assert "location = /api/v3/media/ws {" in config
    assert "location /api/v3/media/ws/ {" in config
    assert config.count("proxy_set_header Host origin.example.com;") == 2


def test_generated_htaccess_targets_origin_and_selected_path():
    text = build_htaccess(_request())
    assert "DirectorySlash Off" in text
    assert "RewriteRule ^api/v3/media/ws/ - [E=DOWS:1]" in text
    assert (
        "RewriteRule ^api/v3/media/ws/(.*)$ "
        "ws://203.0.113.10/api/v3/media/ws/$1 [P,L,QSA,NE]"
    ) in text
