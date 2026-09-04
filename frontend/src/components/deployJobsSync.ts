// Per-account deploy-job cards now persist server-side at /api/deploy-jobs:
//   GET    /api/deploy-jobs          -> { jobs: [...] }
//   PUT    /api/deploy-jobs          -> { jobs: [...] }        (full replace)
//   POST   /api/deploy-jobs          -> { job }                (upsert by taskId)
//   DELETE /api/deploy-jobs/{taskId}
// localStorage stays as an offline cache + the source of truth while the server
// is unreachable. This module owns the wire calls and the merge rule; it is
// generic over the job shape (T extends { taskId: string }) so it has no import
// cycle back to DeployDashboard's DeployJobSummary type. Auth headers are added
// by the global fetch interceptor (auth/apiClient.ts) — no per-call setup here.

export async function fetchDeployJobs<T extends { taskId: string }>(): Promise<T[]> {
  const res = await fetch("/api/deploy-jobs", { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`deploy-jobs GET ${res.status}`);
  const data = await res.json();
  return Array.isArray(data?.jobs) ? (data.jobs as T[]) : [];
}

export async function putDeployJobs<T extends { taskId: string }>(jobs: T[]): Promise<void> {
  const res = await fetch("/api/deploy-jobs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jobs }),
  });
  if (!res.ok) throw new Error(`deploy-jobs PUT ${res.status}`);
}

// Merge the server list with the local cache by taskId. The server wins for any
// taskId it already knows; local-only taskIds (a change that hasn't reached the
// server yet — new card, or a retry that swapped taskId) are kept and returned in
// `localOnly` so the caller can push them. Order: unsynced local-only jobs first
// (newest), then the server's order. The empty-server case falls out naturally:
// every local job is "local-only", so `merged === localJobs` and the caller
// pushes the whole list (the local→server migration).
export function reconcileJobs<T extends { taskId: string }>(
  serverJobs: T[],
  localJobs: T[],
): { merged: T[]; localOnly: T[] } {
  const serverIds = new Set(serverJobs.map(j => j.taskId));
  const localOnly = localJobs.filter(j => !serverIds.has(j.taskId));
  return { merged: [...localOnly, ...serverJobs], localOnly };
}
