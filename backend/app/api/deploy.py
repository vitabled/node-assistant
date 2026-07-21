import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.models.deploy import DeployRequest
from app.services.task_store import task_store, STEP_LABELS, TaskStatus
from app.services.pipeline import run_pipeline
from app.services import job_runner
from app.config import settings

router = APIRouter(prefix="/api")

# Caps concurrent deploy requests at the API layer
_deploy_sem = asyncio.Semaphore(settings.max_ssh_sessions)

# Maps task_id → asyncio.Task so we can cancel on demand
_running_tasks: dict[str, asyncio.Task] = {}


class StopRequest(BaseModel):
    task_id: str


@router.post("/deploy")
async def deploy(req: DeployRequest):
    # The admission cap applies in BOTH modes, just against a different resource:
    # locally it is the SSH-session semaphore, offloaded it is the queue depth.
    # Without the second one the split would silently accept unbounded deploys
    # into an invisible backlog instead of answering the same clear 503.
    offloading = job_runner.offload_available("deploy")
    busy = (task_store.stats().get("queued", 0) >= settings.max_ssh_sessions
            if offloading else _deploy_sem._value == 0)
    if busy:
        raise HTTPException(
            status_code=503,
            detail=f"Server busy — max {settings.max_ssh_sessions} concurrent deploys reached",
        )
    task    = task_store.create(total_steps=len(STEP_LABELS))
    task_id = task.task_id

    # Hand off to the deploy-worker container when one is live; otherwise run it
    # right here, exactly as the monolith always has.
    if job_runner.offload(task, "deploy", req.model_dump(mode="json")):
        return {"task_id": task_id, "task_type": "deploy"}

    # create_task gives us a cancellable handle; BackgroundTasks does not
    loop_task = asyncio.create_task(_run_pipeline_safe(req, task_id))
    _running_tasks[task_id] = loop_task
    loop_task.add_done_callback(lambda _: _running_tasks.pop(task_id, None))

    return {"task_id": task_id, "task_type": "deploy"}


@router.post("/deploy/stop")
async def stop_deploy(req: StopRequest):
    loop_task = _running_tasks.get(req.task_id)
    if loop_task is not None and not loop_task.done():
        loop_task.cancel()
        return {"ok": True}
    # Not ours — it may belong to the deploy-worker process. Flag it in the
    # shared store; the worker polls that flag and cancels its own asyncio task.
    if task_store.request_cancel(req.task_id):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Task not found or already completed")


@router.get("/task/{task_id}")
async def get_task(task_id: str):
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    step_label = STEP_LABELS[task.current_step - 1] if task.current_step > 0 else ""
    return {
        "task_id":    task.task_id,
        "status":     task.status,
        "step":       task.current_step,
        "total":      task.total_steps,
        "step_label": step_label,
        "error":      task.error,
    }


async def _run_pipeline_safe(req: DeployRequest, task_id: str) -> None:
    task = task_store.get(task_id)
    if not task:
        return
    try:
        async with _deploy_sem:
            await run_pipeline(req, task)
    except asyncio.CancelledError:
        # run_pipeline re-raises CancelledError after logging; catch it here
        # only to mark the task FAILED if somehow it wasn't marked yet
        # (e.g. cancelled while waiting to acquire the semaphore)
        if task.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            task.add_log(
                "\n\x1b[1;33m[СИСТЕМА] Процесс деплоя принудительно остановлен "
                "пользователем. Соединение закрыто.\x1b[0m"
            )
            task.finish(TaskStatus.FAILED, "Остановлено пользователем")
        raise  # CancelledError must always be re-raised
    except Exception:
        pass  # status already set to FAILED inside run_pipeline


async def _job_deploy(payload: dict, task) -> None:
    """deploy-worker entry for a queued deploy. The 14-step pipeline is imported
    and run unchanged — the split moves WHERE it runs, never WHAT it does."""
    await run_pipeline(DeployRequest(**payload), task)


job_runner.register("deploy", _job_deploy)
