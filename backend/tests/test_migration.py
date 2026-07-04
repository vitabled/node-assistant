"""Legacy-data migration: the FIRST account created inherits the pre-auth
root-level files, with the originals kept as a backup."""
import json

from app.services import accounts


def _fresh_data_dir(tmp_path, monkeypatch):
    """Repoint the accounts module at an empty temp DATA_DIR so the next
    create_account() is genuinely 'the first account' (registry empty)."""
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path)
    monkeypatch.setattr(accounts, "_REGISTRY_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_ACCOUNTS_DIR", tmp_path / "accounts")


def test_first_account_inherits_legacy_files(tmp_path, monkeypatch):
    _fresh_data_dir(tmp_path, monkeypatch)

    # Pre-auth panel data sitting at the DATA_DIR root.
    (tmp_path / "settings.json").write_text(
        json.dumps({"remnawave": {"panel_url": "https://legacy.example", "api_token": "leg"}}),
        encoding="utf-8",
    )
    (tmp_path / "templates.json").write_text(json.dumps({"templates": [{"id": "t1"}]}), encoding="utf-8")

    acc = accounts.create_account("firstuser", "pw-123")
    dest = tmp_path / "accounts" / acc["id"]

    migrated = json.loads((dest / "settings.json").read_text(encoding="utf-8"))
    assert migrated["remnawave"]["panel_url"] == "https://legacy.example"
    assert (dest / "templates.json").exists()

    # Originals remain as a backup.
    assert (tmp_path / "settings.json").exists()


def test_second_account_starts_empty(tmp_path, monkeypatch):
    _fresh_data_dir(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text(json.dumps({"remnawave": {"panel_url": "https://legacy.example"}}), encoding="utf-8")

    first = accounts.create_account("one", "pw")
    second = accounts.create_account("two", "pw")

    # First inherited legacy; second is empty (no settings.json seeded).
    assert (tmp_path / "accounts" / first["id"] / "settings.json").exists()
    assert not (tmp_path / "accounts" / second["id"] / "settings.json").exists()
