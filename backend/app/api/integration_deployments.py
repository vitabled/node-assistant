"""Versioned, service-token-only storefront deployment integration API."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from app.api import deploy as deploy_api
from app.models.deploy import DeployRequest
from app.models.integration_deployments import IntegrationDeploymentRequest
from app.services import api_tokens, integration_deployments_store as store
from app.services.task_store import STEP_LABELS, TaskStatus, task_store


def require_service_token() -> None:
    if not api_tokens.current_token_id.get():
        raise HTTPException(403, "Integration API requires a service API token")


router = APIRouter(
    prefix="/api/integrations/v1/deployments",
    tags=["storefront-integration"],
    dependencies=[Depends(require_service_token)],
)


async def submit_to_pipeline(request: DeployRequest) -> dict:
    """Narrow seam over the existing, unchanged admin deploy pipeline."""
    return await deploy_api.deploy(request)


async def cancel_pipeline_task(request: deploy_api.StopRequest) -> dict:
    return await deploy_api.stop_deploy(request)


def _pipeline_request(body: IntegrationDeploymentRequest) -> DeployRequest:
    ports = sorted({80, 443, body.new_ssh_port, body.remnanode_port})
    return DeployRequest(
        mode="remnanode",
        ip=body.target.address,
        ssh_user=body.target.ssh_user,
        ssh_key_ref=body.target.ssh_key_ref,
        domain=body.reserved_domain,
        cert_provider="letsencrypt",
        email=body.acme_email,
        open_ports=",".join(str(port) for port in ports),
        current_ssh_port=body.target.ssh_port,
        new_ssh_port=body.new_ssh_port,
        remnanode_port=body.remnanode_port,
        country_code=body.country_code,
        create_in_remnawave=True,
        template_id=body.template_id,
        install_hysteria2=body.install_hysteria2,
    )


def _public(record: dict, *, replay: bool = False) -> dict:
    result = dict(record)
    for field in ("request", "request_hash", "idempotency_key"):
        result.pop(field, None)
    result["idempotent_replay"] = replay
    return result


def _success_result(record: dict) -> dict:
    request = record["request"]
    return {
        # The legacy pipeline does not yet publish the UUID it creates.  Keep the
        # field explicit and nullable rather than scraping logs or inventing it.
        "node_uuid": None,
        "domain": request["reserved_domain"],
        "address": request["target"]["address"],
        "effective_ports": {
            "ssh": request["new_ssh_port"],
            "remnanode": request["remnanode_port"],
        },
        "installed_components": ["remnanode", "ssl"] + (
            ["hysteria2"] if request.get("install_hysteria2", True) else []
        ),
        "pipeline_completed_at": int(time.time()),
        "endpoint_verified_at": None,
    }


def _refresh(record: dict) -> dict:
    if record["state"] in ("succeeded", "failed", "cancelled"):
        return record
    task_id = record.get("task_id")
    if not task_id:
        return record
    task = task_store.get(task_id)
    if task is None:
        return store.update(
            record["deployment_id"], state="unknown", error_code="task_unavailable",
            error_message="Underlying task is unavailable; reconciliation is required",
            warnings=["Task state expired or is not available in this process."],
        )

    step = int(task.current_step)
    label = STEP_LABELS[step - 1] if 0 < step <= len(STEP_LABELS) else ""
    common = {
        "progress_step": step,
        "progress_total": int(task.total_steps),
        "progress_label": label,
    }
    if task.status == TaskStatus.SUCCESS:
        return store.update(
            record["deployment_id"], **common, state="succeeded",
            result=_success_result(record), completed_at=int(time.time()),
            warnings=["node_uuid is unavailable from the legacy pipeline; operator reconciliation is required."],
        )
    if task.status == TaskStatus.FAILED:
        cancelled = record["state"] == "cancellation_pending"
        return store.update(
            record["deployment_id"], **common,
            state="cancelled" if cancelled else "failed",
            error_code="cancelled" if cancelled else "deploy_failed",
            error_message=task.error or ("Deployment cancelled" if cancelled else "Deployment failed"),
            completed_at=int(time.time()),
        )
    return store.update(
        record["deployment_id"], **common,
        state="deploying" if task.status == TaskStatus.RUNNING else "queued",
    )


@router.post("", status_code=202)
async def create_deployment(
    body: IntegrationDeploymentRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
):
    normalized = body.model_dump(mode="json")
    request_hash = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        record, replay = store.create_or_replay(
            idempotency_key=idempotency_key, request_hash=request_hash, request=normalized
        )
    except store.IdempotencyConflict:
        raise HTTPException(409, "Idempotency-Key was already used with a different request")
    except store.ExternalOrderConflict:
        raise HTTPException(409, "external_order_id already has a deployment")

    if replay:
        return JSONResponse(_public(_refresh(record), replay=True), status_code=200)

    try:
        submitted = await submit_to_pipeline(_pipeline_request(body))
    except TimeoutError:
        unknown = store.update(
            record["deployment_id"], state="unknown", error_code="submission_timeout",
            error_message="Pipeline acceptance is ambiguous; do not retry with a new key",
            warnings=["Submission timed out. Reconcile before retrying."],
        )
        raise HTTPException(
            504,
            {"code": "submission_timeout", "deployment_id": record["deployment_id"],
             "state": "unknown"},
        )
    except HTTPException as exc:
        store.update(
            record["deployment_id"], state="failed", error_code=f"pipeline_http_{exc.status_code}",
            error_message="Pipeline rejected the deployment", completed_at=int(time.time()),
        )
        raise
    except Exception:
        store.update(
            record["deployment_id"], state="failed", error_code="pipeline_submission_failed",
            error_message="Pipeline submission failed", completed_at=int(time.time()),
        )
        raise HTTPException(502, "Pipeline submission failed")

    created = store.update(
        record["deployment_id"], task_id=submitted["task_id"], state="queued"
    )
    return _public(created)


@router.get("/{deployment_id}")
async def get_deployment(deployment_id: str):
    record = store.get(deployment_id)
    if not record:
        raise HTTPException(404, "Deployment not found")
    return _public(_refresh(record))


@router.post("/{deployment_id}/cancel", status_code=202)
async def cancel_deployment(deployment_id: str):
    record = store.get(deployment_id)
    if not record:
        raise HTTPException(404, "Deployment not found")
    record = _refresh(record)
    if record["state"] in ("succeeded", "failed", "cancelled"):
        raise HTTPException(409, f"Deployment is already {record['state']}")
    if not record.get("task_id"):
        raise HTTPException(409, "Deployment has no known task to cancel")
    try:
        await cancel_pipeline_task(deploy_api.StopRequest(task_id=record["task_id"]))
    except HTTPException as exc:
        if exc.status_code == 404:
            store.update(
                deployment_id, state="unknown", error_code="task_unavailable",
                error_message="Task could not be cancelled because it is unavailable",
            )
        raise
    return _public(store.update(deployment_id, state="cancellation_pending"))


@router.post("/{deployment_id}/reconcile")
async def reconcile_deployment(deployment_id: str):
    record = store.get(deployment_id)
    if not record:
        raise HTTPException(404, "Deployment not found")
    if record["state"] == "unknown" and not record.get("task_id"):
        record = store.update(
            deployment_id,
            warnings=["No task correlation was returned. Operator/provider reconciliation is required."],
        )
    else:
        record = _refresh(record)
    return _public(record)
