"""Per-account encrypted persistence for deployment dashboard cards."""
import json
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import accounts

client = TestClient(app)


def _auth() -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={"login": f"deploy-jobs-{uuid.uuid4().hex[:8]}", "password": "pw"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["id"], {"Authorization": f"Bearer {body['token']}"}


def _job(task_id: str, password: str = "secret-password") -> dict:
    return {
        "taskId": task_id,
        "domain": "node.example.com",
        "ip": "203.0.113.10",
        "newSshPort": 2222,
        "startedAt": 1720000000000,
        "savedForm": {
            "domain": "node.example.com",
            "ssh_user": "root",
            "ssh_password": password,
            "remnanode_token": "panel-secret",
            "unrecognised_frontend_field": {"keep": True},
        },
    }


def test_get_deploy_jobs_is_empty_for_new_account():
    _account_id, headers = _auth()

    response = client.get("/api/deploy-jobs", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"jobs": []}


def test_put_then_get_round_trips_and_encrypts_saved_form():
    account_id, headers = _auth()
    job = _job("task-put")

    response = client.put("/api/deploy-jobs", headers=headers, json={"jobs": [job]})
    assert response.status_code == 200, response.text
    assert response.json() == {"jobs": [job]}

    response = client.get("/api/deploy-jobs", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"jobs": [job]}

    disk_text = (accounts.account_dir(account_id) / "deploy_jobs.json").read_text(
        encoding="utf-8"
    )
    assert "secret-password" not in disk_text
    assert "panel-secret" not in disk_text
    stored = json.loads(disk_text)
    assert "savedForm" not in stored["jobs"][0]
    assert isinstance(stored["jobs"][0]["savedForm_enc"], str)


def test_post_upserts_deploy_job_by_task_id():
    _account_id, headers = _auth()
    original = _job("task-upsert", password="old-password")
    replacement = _job("task-upsert", password="new-password")
    replacement["color"] = "violet"

    assert client.post("/api/deploy-jobs", headers=headers, json=original).status_code == 200
    response = client.post("/api/deploy-jobs", headers=headers, json=replacement)

    assert response.status_code == 200, response.text
    assert response.json() == {"job": replacement}
    assert client.get("/api/deploy-jobs", headers=headers).json() == {
        "jobs": [replacement]
    }


def test_delete_deploy_job():
    _account_id, headers = _auth()
    job = _job("task-delete")
    assert client.post("/api/deploy-jobs", headers=headers, json=job).status_code == 200

    response = client.delete("/api/deploy-jobs/task-delete", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert client.get("/api/deploy-jobs", headers=headers).json() == {"jobs": []}
