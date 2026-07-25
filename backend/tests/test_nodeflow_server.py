"""Tests for the local NodeFlow orchestrator (services/nodeflow_server.py).

Docker orchestration is not exercised (no daemon assumptions); the deterministic,
security-critical parts ARE: Ed25519 PKI generation (CA → server cert w/ correct
SAN, signed by the CA), idempotency, the global token/password vault (encrypted at
rest), and the docker argv builders (mounts/ports/env)."""

import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.services import nodeflow_server as nf


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(nf, "_ROOT", tmp_path)
    monkeypatch.setattr(nf, "_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(nf, "_PKI", tmp_path / "pki")
    monkeypatch.setattr(nf, "_TLS", tmp_path / "tls")


def test_pki_generates_ca_server_and_signing_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    nf.generate_pki("203.0.113.7")

    ca = x509.load_pem_x509_certificate((tmp_path / "pki" / "ca.crt").read_bytes())
    srv = x509.load_pem_x509_certificate((tmp_path / "tls" / "server.crt").read_bytes())

    # server cert is signed by the CA
    srv.verify_directly_issued_by(ca)
    # CA is a CA
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    # SAN is the IP we asked for
    san = srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("203.0.113.7") in san.get_values_for_type(x509.IPAddress)
    # signing key is Ed25519
    key = __import__("cryptography.hazmat.primitives.serialization", fromlist=["load_pem_private_key"]) \
        .load_pem_private_key((tmp_path / "pki" / "update-signing.key").read_bytes(), password=None)
    assert isinstance(key, ed25519.Ed25519PrivateKey)


def test_pki_dns_san(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    nf.generate_pki("panel.example.com")
    srv = x509.load_pem_x509_certificate((tmp_path / "tls" / "server.crt").read_bytes())
    san = srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "panel.example.com" in san.get_values_for_type(x509.DNSName)


def test_pki_is_idempotent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    nf.generate_pki("203.0.113.7")
    before = (tmp_path / "pki" / "ca.crt").read_bytes()
    nf.generate_pki("203.0.113.7")  # second call must NOT regenerate the CA
    assert (tmp_path / "pki" / "ca.crt").read_bytes() == before


def test_ensure_state_encrypts_and_is_stable(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    s1 = nf.ensure_state()
    assert s1["admin_token"] and s1["pg_password"]
    # stable across calls
    s2 = nf.ensure_state()
    assert s2 == s1
    # encrypted at rest: plaintext never in state.json
    raw = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert s1["admin_token"] not in raw and s1["pg_password"] not in raw
    assert "admin_token_enc" in raw and "pg_password_enc" in raw
    assert nf.admin_token() == s1["admin_token"]


def test_argv_builders_carry_env_mounts_ports():
    pg = nf.postgres_run_argv("pgpw")
    assert "postgres:17-alpine" in pg and "POSTGRES_PASSWORD=pgpw" in pg
    assert "--network" in pg and nf._NETWORK in pg

    mig = nf.migrate_run_argv("pgpw")
    assert nf.MIGRATE_IMAGE in mig and "PGPASSWORD=pgpw" in mig and "--rm" in mig

    panel = nf.panel_run_argv("tok-abc", "pgpw", "203.0.113.7", "node-installer_node-data")
    joined = " ".join(panel)
    assert nf.PANEL_IMAGE in panel
    assert "PANEL_ADMIN_TOKEN=tok-abc" in panel
    assert "0.0.0.0:4200:4200" in panel  # only the agent port is host-published
    assert "PANEL_AGENT_PUBLIC_URL=https://203.0.113.7:4200" in panel
    # PKI/TLS mounted via volume-subpath (never the whole data volume)
    assert "type=volume,src=node-installer_node-data,dst=/pki,volume-subpath=nodeflow/pki,readonly" in panel
    assert "type=volume,src=node-installer_node-data,dst=/tls,volume-subpath=nodeflow/tls,readonly" in panel
    assert "postgres://nodeflow:pgpw@nodeflow-postgres:5432/nodeflow" in joined
