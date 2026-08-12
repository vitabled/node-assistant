"""Wave-9 Ф2 — SSH auth: password OR a private key taken from the Хранилище.

The whole point of the feature is that a key NEVER reaches the browser (it would
land in `savedForm` in localStorage), so the tests assert both halves: the
resolver returns key material to the SSH layer, and a bad/foreign entry fails
with a flat 400 instead of leaking which entry it was.
"""
import asyncio
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import HTTPException

from app.models.deploy import DeployRequest
from app.services import ssh_auth, vault_store
from app.services.ssh_manager import SSHSession


def _key_pem(passphrase: str = "") -> str:
    """A real OpenSSH-format ed25519 key — asyncssh must be able to import it."""
    enc = (serialization.BestAvailableEncryption(passphrase.encode())
           if passphrase else serialization.NoEncryption())
    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=enc,
    ).decode()


class _Req:
    """Stand-in for the ~20 request models that carry SSH creds (resolve() is
    duck-typed on purpose — see its docstring)."""

    def __init__(self, password: str = "", key_ref: str = ""):
        self.ssh_password = password
        self.ssh_key_ref = key_ref


@pytest.fixture()
def account(tmp_path, monkeypatch):
    from app.services import accounts

    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path, raising=False)
    aid = f"acc-{uuid.uuid4().hex[:8]}"
    (tmp_path / "accounts" / aid).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(accounts, "data_dir", lambda a=None: tmp_path / "accounts" / (a or aid))
    return aid


def _entry(account, *, kind="ssh_key", fields=None):
    return vault_store.create_entry(
        name="prod-key", kind=kind, fields=fields if fields is not None else {},
        account_id=account)["id"]


def test_password_path_is_unchanged(account):
    got = asyncio.run(ssh_auth.resolve(_Req(password="pw"), account))
    assert got == {"password": "pw"}


def test_key_from_the_vault_reaches_the_ssh_layer(account):
    pem = _key_pem()
    ref = _entry(account, fields={"private_key": pem, "passphrase": ""})

    got = asyncio.run(ssh_auth.resolve(_Req(key_ref=ref), account))

    assert got["private_key"] == pem and got["key_passphrase"] == ""
    assert "password" not in got, "с ключом пароль не должен подмешиваться"
    # And the SSH layer accepts it without any network involved.
    SSHSession("192.0.2.1", 22, "root", **got)


def test_passphrase_protected_key_round_trips(account):
    pem = _key_pem("hunter2")
    ref = _entry(account, fields={"private_key": pem, "passphrase": "hunter2"})
    got = asyncio.run(ssh_auth.resolve(_Req(key_ref=ref), account))
    SSHSession("192.0.2.1", 22, "root", **got)


def test_wrong_passphrase_is_a_400_without_key_material(account):
    pem = _key_pem("correct-horse")
    ref = _entry(account, fields={"private_key": pem, "passphrase": "wrong"})

    with pytest.raises(HTTPException) as err:
        asyncio.run(ssh_auth.resolve(_Req(key_ref=ref), account))

    assert err.value.status_code == 400
    detail = str(err.value.detail)
    # The message must be readable Russian and must not carry the key itself.
    assert "PRIVATE KEY" not in detail and pem[:40] not in detail
    assert detail.strip()


def test_missing_wrong_kind_and_empty_key_all_fail_the_same_way(account):
    cases = [
        "no-such-entry-id",
        _entry(account, kind="ssh_password", fields={"password": "x"}),  # foreign kind
        _entry(account, fields={"passphrase": "only"}),                  # no private_key
    ]
    details = set()
    for ref in cases:
        with pytest.raises(HTTPException) as err:
            asyncio.run(ssh_auth.resolve(_Req(key_ref=ref), account))
        assert err.value.status_code == 400
        details.add(str(err.value.detail))
    # One indistinguishable message: which failure it was is not actionable and
    # telling them apart would probe other accounts' entry ids.
    assert len(details) == 1


def _deploy_body(**over):
    body = {
        "ip": "192.0.2.1", "ssh_user": "root",
        "domain": "n1.example.com", "email": "a@b.co",
        "cloudflare_api_key": "cf", "remnanode_token": "tok",
        "country_code": "NL", "open_ports": "80,443",
    }
    body.update(over)
    return body


def test_deploy_request_demands_password_or_key():
    with pytest.raises(Exception) as err:
        DeployRequest(**_deploy_body())
    # Same 422-shaped rejection as the old `ssh_password: Field(..., min_length=1)`
    assert "парол" in str(err.value).lower() or "password" in str(err.value).lower()

    assert DeployRequest(**_deploy_body(ssh_password="pw")).ssh_password == "pw"
    assert DeployRequest(**_deploy_body(ssh_key_ref="ref-1")).ssh_key_ref == "ref-1"


def test_connect_uses_full_default_alg_set(monkeypatch):
    """asyncssh 2.14: None в server_host_key_algs падает с «No host key
    algorithms selected»; () = полный набор (rsa-sha2/ecdsa/ed25519). Регрессия
    по бою: сервер на OpenSSH ≥ 8.8 отклонял рукопожатие."""
    import asyncio
    from app.services.ssh_manager import SSHSession

    seen = {}

    async def fake_connect(host, **kwargs):
        seen.update(kwargs)
        class _C:
            pass
        return _C()

    monkeypatch.setattr("app.services.ssh_manager.asyncssh.connect", fake_connect)
    ssh = SSHSession("1.2.3.4", 22, "root", "pw")
    asyncio.run(ssh.connect())
    assert seen["server_host_key_algs"] == ()
    assert seen["known_hosts"] is None
