"""Per-account encrypted persistence for deployment dashboard cards."""
import json
import uuid
import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.api import deploy
from app.models.deploy import DeployRequest
from app.services import accounts, deploy_jobs_backfill, shared_task_store
from app.services.task_store import STEP_LABELS, TaskStatus, task_store

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


def _deploy_payload() -> dict:
    return {
        "mode": "haproxy",
        "ip": "203.0.113.10",
        "ssh_password": "secret-password",
        "open_ports": "22",
        "haproxy_dest_ip": "198.51.100.10",
    }


def test_post_deploy_immediately_creates_running_server_job(monkeypatch):
    _account_id, headers = _auth()

    async def pipeline_without_verdict(_req, _task):
        return None

    monkeypatch.setattr(deploy, "run_pipeline", pipeline_without_verdict)

    response = client.post("/api/deploy", headers=headers, json=_deploy_payload())

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    jobs = client.get("/api/deploy-jobs", headers=headers).json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["taskId"] == task_id
    assert jobs[0]["finalStatus"] == "running"
    assert jobs[0]["ip"] == "203.0.113.10"
    assert jobs[0]["newSshPort"] == 2222
    assert jobs[0]["savedForm"]["ssh_password"] == "secret-password"


def test_finished_pipeline_updates_deploy_job_final_status(monkeypatch):
    account_id, _headers = _auth()
    token = accounts.current_account.set(account_id)
    try:
        task = task_store.create(total_steps=len(STEP_LABELS))
        req = DeployRequest(**_deploy_payload())

        async def successful_pipeline(_req, pipeline_task):
            pipeline_task.finish(TaskStatus.SUCCESS)

        monkeypatch.setattr(deploy, "run_pipeline", successful_pipeline)
        deploy_jobs_backfill.create_deploy_job(req, task.task_id)
        asyncio.run(deploy._run_pipeline_safe(req, task.task_id))

        jobs = deploy_jobs_backfill.list_deploy_jobs(account_id)
        assert jobs[0]["taskId"] == task.task_id
        assert jobs[0]["finalStatus"] == "success"
    finally:
        accounts.current_account.reset(token)


def test_backfill_adds_missing_cards_without_duplicating_existing(tmp_path, monkeypatch):
    account_id, _headers = _auth()
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path)
    monkeypatch.setattr(shared_task_store, "_conn", None)
    token = accounts.current_account.set(account_id)
    try:
        missing_id = "backfill-missing"
        existing_id = "backfill-existing"
        encrypted_payload = shared_task_store._encrypt(_deploy_payload())
        conn = shared_task_store._connect()
        for task_id in (missing_id, existing_id):
            conn.execute(
                "INSERT INTO tasks (task_id, account_id, kind, total_steps, status, "
                "created_at, updated_at, payload_enc) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, account_id, "deploy", len(STEP_LABELS), "pending", 1, 1, encrypted_payload),
            )
        conn.commit()
        deploy_jobs_backfill.create_deploy_job(
            DeployRequest(**_deploy_payload()), existing_id, started_at=1.0
        )
    finally:
        accounts.current_account.reset(token)

    assert deploy_jobs_backfill.backfill_deploy_jobs() == 1
    assert deploy_jobs_backfill.backfill_deploy_jobs() == 0

    token = accounts.current_account.set(account_id)
    try:
        jobs = deploy_jobs_backfill.list_deploy_jobs(account_id)
        assert {job["taskId"] for job in jobs} == {missing_id, existing_id}
    finally:
        accounts.current_account.reset(token)
        shared_task_store.reset_for_tests()
