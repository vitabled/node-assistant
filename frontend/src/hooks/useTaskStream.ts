import { useEffect, useRef, useCallback } from "react";

export type TaskStatus = "pending" | "running" | "success" | "failed";

export interface StatusFrame {
  status: TaskStatus;
  current_step: number;
  total_steps: number;
}

interface UseTaskStreamOptions {
  taskId: string | null;
  onLog: (line: string) => void;
  onStatus: (frame: StatusFrame) => void;
  onDone?: (status: TaskStatus, error: string | null) => void;
}

export function useTaskStream({ taskId, onLog, onStatus, onDone }: UseTaskStreamOptions) {
  const wsRef = useRef<WebSocket | null>(null);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  useEffect(() => {
    if (!taskId) return;
    disconnect();

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
          onLog(`\x1b[31m[WS error] ${msg.message}\x1b[0m`);
          break;
      }
    };

    ws.onerror = () => onLog("\x1b[31m[WebSocket connection error]\x1b[0m");

    return disconnect;
  }, [taskId]); // eslint-disable-line react-hooks/exhaustive-deps
}
