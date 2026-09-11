import { useEffect, useRef, useCallback } from "react";

export type TaskStatus = "pending" | "running" | "success" | "failed";

export interface StatusFrame {
  status: TaskStatus;
  current_step: number;
  total_steps: number;
}

// The backend reports a missing task in several shapes — a WS `error` frame with
// "Task not found", a REST 404 `detail: "Task not found"`, or "Task not found or
// already completed". All share the same phrase and mean the SAME thing: the task
// is gone from the in-memory store (backend restart / a card restored by another
// process) and can never come back. This is a FINAL state — no reconnect, no retry.
export function isTaskNotFound(message: unknown): boolean {
  return typeof message === "string" && /task not found/i.test(message);
}

interface UseTaskStreamOptions {
  taskId: string | null;
  onLog: (line: string) => void;
  onStatus: (frame: StatusFrame) => void;
  onDone?: (status: TaskStatus, error: string | null) => void;
  /** Fired when the server answers that the task no longer exists — a terminal
   *  state. The hook closes the socket and never reconnects for this taskId. */
  onUnavailable?: () => void;
}

export function useTaskStream({ taskId, onLog, onStatus, onDone, onUnavailable }: UseTaskStreamOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  // Latches once the server reported the task gone, so a late error/close event
  // (or any future reconnect path) can't resurrect the stream.
  const unavailableRef = useRef(false);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  useEffect(() => {
    if (!taskId) return;
    disconnect();
    unavailableRef.current = false;

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/logs/${taskId}`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data as string) as Record<string, unknown>;

      switch (msg.type) {
        case "log":
          onLog(msg.line as string);
          break;

        case "status":
          // Server sends this on each step change
          onStatus({
            status:       msg.status as TaskStatus,
            current_step: msg.step as number,
            total_steps:  msg.total as number,
          });
          break;

        case "done":
          // Final frame — update status then call onDone
          onStatus({
            status:       msg.status as TaskStatus,
            current_step: -1,   // signal: use last known step
            total_steps:  -1,
          });
          onDone?.(msg.status as TaskStatus, (msg.error as string) ?? null);
          break;

        case "ping":
          break; // heartbeat, ignore

        case "error":
          if (isTaskNotFound(msg.message)) {
            // Final state: the task is gone for good. Close, notify, and never
            // reconnect — no raw "Task not found" text, no red terminal line.
            unavailableRef.current = true;
            onUnavailable?.();
            disconnect();
            return;
          }
          onLog(`\x1b[31m[WS error] ${msg.message}\x1b[0m`);
          break;
      }
    };

    ws.onerror = () => {
      if (unavailableRef.current) return;
      onLog("\x1b[31m[WebSocket connection error]\x1b[0m");
    };

    ws.onclose = () => { wsRef.current = null; };

    return disconnect;
  }, [taskId]); // eslint-disable-line react-hooks/exhaustive-deps
}
