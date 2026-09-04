// Per-account deploy-job cards now persist server-side at /api/deploy-jobs, and
// the SERVER is the source of truth. localStorage is only an offline buffer for
// cards that haven't reached the server yet (plus a one-time migration source for
// browsers that predate server storage).
//   GET    /api/deploy-jobs          -> { jobs: [...] }   (authoritative list)
//   POST   /api/deploy-jobs          -> { job }           (upsert by taskId)
//   DELETE /api/deploy-jobs/{taskId} -> { ok: true }
// This module owns the wire calls and the merge rule; it is generic over the job
// shape (T extends { taskId: string }) so it has no import cycle back to
// DeployDashboard's DeployJobSummary type. Auth headers are added by the global
// fetch interceptor (auth/apiClient.ts) — no per-call setup here.

export async function fetchDeployJobs<T extends { taskId: string }>(): Promise<T[]> {
  const res = await fetch("/api/deploy-jobs", { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`deploy-jobs GET ${res.status}`);
  const data = await res.json();
  return Array.isArray(data?.jobs) ? (data.jobs as T[]) : [];
}

// Create or replace the single card identified by its taskId (upsert). Unlike a
// full-list PUT this never clobbers cards another client has pushed, so it is the
// only safe write while the server is the source of truth.
export async function upsertDeployJob<T extends { taskId: string }>(job: T): Promise<void> {
  const res = await fetch("/api/deploy-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(job),
  });
  if (!res.ok) throw new Error(`deploy-jobs POST ${res.status}`);
}

// Idempotent delete: a 404 (already gone) is treated as success so a double-fire
// (e.g. React StrictMode re-running an updater) never surfaces a false error.
export async function deleteDeployJob(taskId: string): Promise<void> {
  const res = await fetch(`/api/deploy-jobs/${encodeURIComponent(taskId)}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new Error(`deploy-jobs DELETE ${res.status}`);
}

// Merge the authoritative server list with the local offline buffer by taskId.
// The server wins for any taskId it already knows; local-only taskIds (cards that
// haven't reached the server yet) are returned as `localOnly` so the caller can
// push them. Render order: server jobs first, then pending local cards.
export function reconcileJobs<T extends { taskId: string }>(
  serverJobs: T[],
  localJobs: T[],
): { merged: T[]; localOnly: T[] } {
  const serverIds = new Set(serverJobs.map(j => j.taskId));
  const localOnly = localJobs.filter(j => !serverIds.has(j.taskId));
  return { merged: [...serverJobs, ...localOnly], localOnly };
}
