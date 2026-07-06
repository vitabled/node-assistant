import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.models.deploy import DeployRequest
from app.services.task_store import task_store, STEP_LABELS, TaskStatus
from app.services.pipeline import run_pipeline
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
    if _deploy_sem._value == 0:
        raise HTTPException(
            status_code=503,
            detail=f"Server busy — max {settings.max_ssh_sessions} concurrent deploys reached",
        )
    task    = task_store.create(total_steps=len(STEP_LABELS))
    task_id = task.task_id

    # create_task gives us a cancellable handle; BackgroundTasks does not
    loop_task = asyncio.create_task(_run_pipeline_safe(req, task_id))
    _running_tasks[task_id] = loop_task
    loop_task.add_done_callback(lambda _: _running_tasks.pop(task_id, None))

    return {"task_id": task_id, "task_type": "deploy"}


@router.post("/deploy/stop")
async def stop_deploy(req: StopRequest):
    loop_task = _running_tasks.get(req.task_id)
    if loop_task is None or loop_task.done():
        raise HTTPException(status_code=404, detail="Task not found or already completed")
    loop_task.cancel()
    return {"ok": True}


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
