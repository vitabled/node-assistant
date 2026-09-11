// Per-account deploy-job cards persist server-side at /api/deploy-jobs, and
// the SERVER is the source of truth. localStorage is only a local cache — a
// mirror of the merged list for offline rendering; it never drives deletions.
//   GET    /api/deploy-jobs          -> { jobs: [...] }   (authoritative list)
//   POST   /api/deploy-jobs          -> { job }           (upsert by taskId)
//   DELETE /api/deploy-jobs/{taskId} -> { ok: true }      (explicit user delete ONLY)
// Sync is ADDITIVE: it uploads local-only cards and merges server cards into the
// local cache, but it never calls DELETE. The only thing allowed to delete a card
// on the server is the explicit "delete" action in the UI — losing a card would
// lose its Fernet-encrypted SSH credentials (prod incident 29 -> 1 cards).
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

// Explicit user-initiated delete (the "delete" button in the UI). SYNC MUST NOT
// call this — an additive sync that deleted server cards it didn't recognize would
// destroy Fernet-encrypted SSH credentials. Idempotent: a 404 (already gone) is
// treated as success so a double-fire never surfaces a false error.
export async function deleteDeployJob(taskId: string): Promise<void> {
  const res = await fetch(`/api/deploy-jobs/${encodeURIComponent(taskId)}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new Error(`deploy-jobs DELETE ${res.status}`);
}

// Merge the authoritative server list with the local cache by taskId. The server
// wins for any taskId it already knows; local-only taskIds (cards that haven't
// reached the server yet) are returned as `localOnly` so the caller can push them.
// Render order: server jobs first, then pending local cards.
export function reconcileJobs<T extends { taskId: string }>(
  serverJobs: T[],
  localJobs: T[],
): { merged: T[]; localOnly: T[] } {
  const serverIds = new Set(serverJobs.map(j => j.taskId));
  const localOnly = localJobs.filter(j => !serverIds.has(j.taskId));
  return { merged: [...serverJobs, ...localOnly], localOnly };
}

// One-shot additive sync: pull the authoritative server list, merge it with the
// local cache (server wins on taskId conflicts), upload any local-only cards, and
// write the merged list back to the local cache. It NEVER deletes anything on the
// server — deletion is only the explicit UI "delete" button (deleteDeployJob).
// If the API is unreachable it degrades to the local cache (offline) without
// touching the server, so local data is never lost.
export interface SyncDeployJobsResult<T> {
  jobs: T[];
  /** true when the server was unreachable and the local cache was returned as-is. */
  offline: boolean;
  /** true when at least one local-only card failed to upload. */
  pushFailed: boolean;
}

export async function syncDeployJobs<T extends { taskId: string }>(
  opts: { loadLocal: () => T[]; saveLocal: (jobs: T[]) => void },
): Promise<SyncDeployJobsResult<T>> {
  let serverJobs: T[];
  try {
    serverJobs = await fetchDeployJobs<T>();
  } catch {
    return { jobs: opts.loadLocal(), offline: true, pushFailed: false };
  }

  const local = opts.loadLocal();
  const { merged, localOnly } = reconcileJobs(serverJobs, local);

  let pushFailed = false;
  for (const j of localOnly) {
    try {
      await upsertDeployJob(j);
    } catch {
      pushFailed = true; // keep the card in the local cache; it retries next sync
    }
  }

  opts.saveLocal(merged); // cache = authoritative merged list (server + pending local)
  return { jobs: merged, offline: false, pushFailed };
}
