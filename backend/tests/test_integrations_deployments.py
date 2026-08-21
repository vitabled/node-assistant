"""Phase A customer-storefront deployment integration contract."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import api_tokens

client = TestClient(app)


def _register(prefix: str = "integration") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"login": f"{prefix}-{uuid.uuid4().hex[:8]}", "password": "test-password"},
    )
    assert response.status_code == 201
    return response.json()


def _service_headers(*, readonly: bool = False) -> dict[str, str]:
    owner = _register()
    _record, token = api_tokens.create(
        "storefront", readonly=readonly, account_id=owner["id"], user_id=owner["id"]
    )
    return {"Authorization": f"Bearer {token}"}


def _payload(order: str | None = None) -> dict:
    return {
        "external_order_id": order or f"order-{uuid.uuid4().hex}",
        "target": {
            "address": "203.0.113.10",
            "ssh_user": "root",
            "ssh_key_ref": uuid.uuid4().hex,
            "ssh_port": 22,
        },
        "reserved_domain": "node.customer.example",
        "sku_version": "vpn-egames@1",
        "template_id": str(uuid.uuid4()),
        "country_code": "DE",
        "acme_email": "ops@example.com",
        "new_ssh_port": 2222,
        "remnanode_port": 2222,
    }


def test_integration_requires_authentication():
    response = client.get(f"/api/integrations/v1/deployments/{uuid.uuid4()}")
    assert response.status_code == 401


def test_integration_rejects_browser_session_even_for_superuser():
    owner = _register("browser")
    response = client.post(
        "/api/integrations/v1/deployments",
        headers={
            "Authorization": f"Bearer {owner['token']}",
            "Idempotency-Key": uuid.uuid4().hex,
        },
        json=_payload(),
    )
    assert response.status_code == 403


def test_readonly_service_token_cannot_submit():
    response = client.post(
        "/api/integrations/v1/deployments",
        headers={**_service_headers(readonly=True), "Idempotency-Key": uuid.uuid4().hex},
        json=_payload(),
    )
    assert response.status_code == 403


def test_submit_validates_idempotency_key_and_normalized_dto():
    headers = _service_headers()
    assert client.post(
        "/api/integrations/v1/deployments", headers=headers, json=_payload()
    ).status_code == 422

    invalid = _payload()
    invalid["target"]["address"] = "not-an-ip"
    response = client.post(
        "/api/integrations/v1/deployments",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json=invalid,
    )
    assert response.status_code == 422


def test_submit_is_idempotent_and_conflicting_reuse_is_409(monkeypatch):
    from app.api import integration_deployments

    calls = []

    async def fake_submit(request):
        calls.append(request)
        return {"task_id": str(uuid.uuid4()), "task_type": "deploy"}

    monkeypatch.setattr(integration_deployments, "submit_to_pipeline", fake_submit)
    headers = {**_service_headers(), "Idempotency-Key": uuid.uuid4().hex}
    payload = _payload()

    first = client.post("/api/integrations/v1/deployments", headers=headers, json=payload)
    replay = client.post("/api/integrations/v1/deployments", headers=headers, json=payload)
    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["deployment_id"] == first.json()["deployment_id"]
    assert replay.json()["idempotent_replay"] is True
    assert len(calls) == 1

    changed = dict(payload, reserved_domain="other.customer.example")
    conflict = client.post(
        "/api/integrations/v1/deployments", headers=headers, json=changed
    )
    assert conflict.status_code == 409


def test_external_order_id_is_unique(monkeypatch):
    from app.api import integration_deployments

    async def fake_submit(request):
        return {"task_id": str(uuid.uuid4()), "task_type": "deploy"}

    monkeypatch.setattr(integration_deployments, "submit_to_pipeline", fake_submit)
    headers = _service_headers()
    payload = _payload()
    first = client.post(
        "/api/integrations/v1/deployments",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json=payload,
    )
    assert first.status_code == 202
    duplicate_order = client.post(
        "/api/integrations/v1/deployments",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json=payload,
    )
    assert duplicate_order.status_code == 409


def test_timeout_is_durable_unknown_and_can_be_reconciled(monkeypatch):
    from app.api import integration_deployments

    async def timeout(_request):
        raise TimeoutError("ambiguous transport timeout")

    monkeypatch.setattr(integration_deployments, "submit_to_pipeline", timeout)
    headers = {**_service_headers(), "Idempotency-Key": uuid.uuid4().hex}
    response = client.post(
        "/api/integrations/v1/deployments", headers=headers, json=_payload()
    )
    assert response.status_code == 504
    deployment_id = response.json()["detail"]["deployment_id"]

    status = client.get(
        f"/api/integrations/v1/deployments/{deployment_id}", headers=headers
    )
    assert status.status_code == 200
    assert status.json()["state"] == "unknown"
    assert status.json()["task_id"] is None

    reconciled = client.post(
        f"/api/integrations/v1/deployments/{deployment_id}/reconcile", headers=headers
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["state"] == "unknown"
    assert reconciled.json()["warnings"]


def test_status_projects_task_progress_and_structured_result(monkeypatch):
    from app.api import integration_deployments
    from app.services.task_store import TaskStatus, task_store

    task = task_store.create(total_steps=14)

    async def fake_submit(_request):
        return {"task_id": task.task_id, "task_type": "deploy"}

    monkeypatch.setattr(integration_deployments, "submit_to_pipeline", fake_submit)
    headers = {**_service_headers(), "Idempotency-Key": uuid.uuid4().hex}
    payload = _payload()
    created = client.post(
        "/api/integrations/v1/deployments", headers=headers, json=payload
    ).json()

    task.set_step(3, TaskStatus.RUNNING)
    running = client.get(
        f"/api/integrations/v1/deployments/{created['deployment_id']}", headers=headers
    ).json()
    assert running["state"] == "deploying"
    assert running["progress"]["step"] == 3
    assert running["progress"]["total"] == 14

    task.finish(TaskStatus.SUCCESS)
    done = client.get(
        f"/api/integrations/v1/deployments/{created['deployment_id']}", headers=headers
    ).json()
    assert done["state"] == "succeeded"
    assert done["result"]["domain"] == payload["reserved_domain"]
    assert done["result"]["address"] == payload["target"]["address"]
    assert done["result"]["effective_ports"] == {"ssh": 2222, "remnanode": 2222}
    assert done["result"]["node_uuid"] is None


def test_cancel_is_correlated_to_underlying_task(monkeypatch):
    from app.api import integration_deployments

    task_id = str(uuid.uuid4())

    async def fake_submit(_request):
        return {"task_id": task_id, "task_type": "deploy"}

    cancelled = []

    async def fake_cancel(value):
        cancelled.append(value.task_id)
        return {"ok": True}

    monkeypatch.setattr(integration_deployments, "submit_to_pipeline", fake_submit)
    monkeypatch.setattr(integration_deployments, "cancel_pipeline_task", fake_cancel)
    headers = {**_service_headers(), "Idempotency-Key": uuid.uuid4().hex}
    created = client.post(
        "/api/integrations/v1/deployments", headers=headers, json=_payload()
    ).json()
    response = client.post(
        f"/api/integrations/v1/deployments/{created['deployment_id']}/cancel", headers=headers
    )
    assert response.status_code == 202
    assert response.json()["state"] == "cancellation_pending"
    assert cancelled == [task_id]


def test_unknown_deployment_is_404():
    response = client.get(
        f"/api/integrations/v1/deployments/{uuid.uuid4()}", headers=_service_headers()
    )
    assert response.status_code == 404
