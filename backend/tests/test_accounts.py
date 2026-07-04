"""Unit tests for services/accounts.py — hashing, JWT, registry, the data_dir
traversal guard, and legacy migration (marker-gated, first-account only)."""
import json
import uuid

import pytest

from app.services import accounts


def _uniq(p="u"):
    return f"{p}-{uuid.uuid4().hex[:8]}"


# ── password hashing ──────────────────────────────────────────
def test_password_hash_roundtrip_and_reject():
    h = accounts._hash_password("hunter2")
    assert accounts._verify_password("hunter2", h)
    assert not accounts._verify_password("wrong", h)


def test_password_hash_handles_oversized_input():
    # >72 bytes must still hash/verify (sha256 pre-hash sidesteps bcrypt's limit).
    pw = "x" * 200
    h = accounts._hash_password(pw)
    assert accounts._verify_password(pw, h)


def test_verify_password_on_corrupt_hash_fails_closed():
    assert accounts._verify_password("anything", "not-a-bcrypt-hash") is False


# ── JWT sessions ──────────────────────────────────────────────
def test_token_roundtrip():
    tok = accounts.issue_token("acc-123")
    assert accounts.account_id_from_token(tok) == "acc-123"


def test_tampered_or_garbage_token_rejected():
    assert accounts.account_id_from_token("garbage.jwt.value") is None
    tok = accounts.issue_token("acc-1")
    assert accounts.account_id_from_token(tok + "x") is None  # signature broken


# ── registry ──────────────────────────────────────────────────
def test_create_account_and_authenticate():
    login = _uniq("acc")
    acc = accounts.create_account(login, "pw-1")
    assert acc["login"] == login and acc["id"]
    assert accounts.authenticate(login, "pw-1")["id"] == acc["id"]
    assert accounts.authenticate(login, "bad") is None
    assert accounts.authenticate(_uniq("ghost"), "pw") is None


def test_duplicate_login_case_insensitive():
    login = _uniq("Dup")
    accounts.create_account(login.lower(), "pw")
    with pytest.raises(ValueError):
        accounts.create_account(login.upper(), "pw")


def test_get_returns_none_for_unknown():
    assert accounts.get(str(uuid.uuid4())) is None


# ── data_dir traversal guard ──────────────────────────────────
def test_data_dir_rejects_traversal():
    for bad in ["../evil", "a/b", "a\\b", "..", ""]:
        with pytest.raises(ValueError):
            accounts.data_dir(bad)


def test_data_dir_ok_for_uuid():
    aid = str(uuid.uuid4())
    d = accounts.data_dir(aid)
    assert d.name == aid and d.exists()


# ── legacy migration (isolated temp DATA_DIR) ─────────────────
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path)
    monkeypatch.setattr(accounts, "_REGISTRY_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_ACCOUNTS_DIR", tmp_path / "accounts")


def test_first_account_inherits_legacy_second_is_empty(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text(
        json.dumps({"remnawave": {"panel_url": "https://legacy.example"}}), encoding="utf-8")

    first = accounts.create_account("one", "pw")
    second = accounts.create_account("two", "pw")

    migrated = tmp_path / "accounts" / first["id"] / "settings.json"
    assert json.loads(migrated.read_text(encoding="utf-8"))["remnawave"]["panel_url"] == "https://legacy.example"
    assert (tmp_path / "settings.json").exists()  # original kept as backup
    assert not (tmp_path / "accounts" / second["id"] / "settings.json").exists()
    assert (tmp_path / ".legacy_migrated").exists()


def test_migration_marker_prevents_rerun(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    (tmp_path / ".legacy_migrated").write_text("done", encoding="utf-8")
    (tmp_path / "settings.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    acc = accounts.create_account("solo", "pw")
    # marker already present → no migration even though legacy file exists
    assert not (tmp_path / "accounts" / acc["id"] / "settings.json").exists()
