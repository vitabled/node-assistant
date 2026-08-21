"""Workspace instances: API lifecycle, request partitioning and legacy compatibility."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts, bedolaga_store, infra_billing_store, storage

client = TestClient(app)


def _owner():
    response = client.post(
        "/api/auth/register",
        json={"login": f"instance-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    assert response.status_code == 201
    return response.json()


def _headers(owner, instance_id=None):
    headers = {"Authorization": f"Bearer {owner['token']}"}
    if instance_id:
        headers["X-Instance-Id"] = instance_id
    return headers


def test_existing_workspace_gets_default_instance_using_legacy_directory():
    owner = _owner()
    storage.save_settings({"legacy": True}, account_id=owner["id"])

    response = client.get("/api/instances", headers=_headers(owner))

    assert response.status_code == 200
    assert response.json() == [
        {"id": "default", "name": "Default", "account_id": owner["id"]}
    ]
    assert storage.load_settings(account_id=owner["id"]) == {"legacy": True}
    assert (accounts.data_dir(owner["id"]) / "settings.json").exists()


def test_instance_header_partitions_settings_inside_account():
    owner = _owner()
    created = client.post(
        "/api/instances", headers=_headers(owner), json={"name": "Production"}
    )
    assert created.status_code == 201
    instance_id = created.json()["id"]

    default_put = client.post(
        "/api/settings/deploy-defaults",
        headers={**_headers(owner), "Content-Type": "application/json"},
        json={"ssh_user": "root", "ssh_port": 22},
    )
    isolated_put = client.post(
        "/api/settings/deploy-defaults",
        headers={**_headers(owner, instance_id), "Content-Type": "application/json"},
        json={"ssh_user": "ubuntu", "ssh_port": 2222},
    )
    assert default_put.status_code == 200
    assert isolated_put.status_code == 200

    default_settings = client.get("/api/settings", headers=_headers(owner)).json()
    isolated_settings = client.get(
        "/api/settings", headers=_headers(owner, instance_id)
    ).json()
    assert default_settings["deploy_defaults"]["ssh_user"] == "root"
    assert isolated_settings["deploy_defaults"]["ssh_user"] == "ubuntu"
    assert accounts.data_dir(owner["id"], instance_id).parent.name == "instances"


def test_backend_settings_databases_follow_the_instance_partition():
    owner = _owner()
    instance_id = client.post(
        "/api/instances", headers=_headers(owner), json={"name": "DB partition"}
    ).json()["id"]
    account_token = accounts.current_account.set(owner["id"])
    instance_token = accounts.current_instance.set(instance_id)
    try:
        partition = accounts.data_dir(owner["id"], instance_id)
        assert infra_billing_store._db_path() == partition / "infra_billing.db"
        assert bedolaga_store._db_path() == partition / "bedolaga.db"
        storage.save_settings({"remnawave": {"panels": [{"id": "p2"}]}})
        assert storage.load_settings()["remnawave"]["panels"][0]["id"] == "p2"
    finally:
        accounts.current_instance.reset(instance_token)
        accounts.current_account.reset(account_token)


def test_instance_from_another_account_is_rejected():
    first, second = _owner(), _owner()
    foreign = client.post(
        "/api/instances", headers=_headers(first), json={"name": "Private"}
    ).json()["id"]

    response = client.get("/api/settings", headers=_headers(second, foreign))

    assert response.status_code == 404
