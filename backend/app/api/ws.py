"""
WebSocket endpoint: GET /ws/logs/{task_id}

Message protocol (server → client):
  {"type": "log",  "line": str}
  {"type": "status", "step": int, "total": int, "status": str}
  {"type": "done", "status": str, "error": str|null}
  {"type": "ping"}   — heartbeat every 25 s of inactivity

The client subscribes and receives the full log replay immediately,
then live updates as they arrive.  The stream ends with "done".
"""
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.task_store import task_store

router = APIRouter()

_HEARTBEAT_INTERVAL = 25  # seconds


@router.websocket("/ws/logs/{task_id}")
async def log_stream(websocket: WebSocket, task_id: str):
    await websocket.accept()

    task = task_store.get(task_id)
    if not task:
        await websocket.send_text(
            json.dumps({"type": "error", "message": "Task not found"})
        )
        await websocket.close()
        return

    queue = task.subscribe()
    try:
        while True:
            # Block until next event, with a heartbeat timeout
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue

            kind = item[0]

            if kind == "log":
                await websocket.send_text(
                    json.dumps({"type": "log", "line": item[1]})
                )

            elif kind == "step":
                _, step, status, total = item
                await websocket.send_text(
                    json.dumps({
                        "type":   "status",
                        "step":   step,
                        "total":  total,
                        "status": status,
                    })
                )

            elif kind == "done":
                await websocket.send_text(
                    json.dumps({
                        "type":   "done",
                        "status": task.status.value,
                        "error":  task.error,
                    })
                )
                break  # graceful close

    except WebSocketDisconnect:
        pass
    finally:
        task.unsubscribe(queue)
        try:
            await websocket.close()
        except Exception:
            pass
