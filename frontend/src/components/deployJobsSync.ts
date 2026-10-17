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

// taskIds the SERVER deleted on purpose (tombstones). A card removed there but
// still present in this browser's cache must be dropped locally — otherwise the
// additive sync uploads it back and "resurrects" it. GET /api/deploy-jobs
// already hides them; this pulls the list so the cache can be pruned too.
export async function fetchDeployJobTombstones(): Promise<string[]> {
  const res = await fetch("/api/deploy-jobs/tombstones", { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`deploy-jobs tombstones GET ${res.status}`);
  const data = await res.json();
  return Array.isArray(data?.taskIds) ? (data.taskIds as string[]) : [];
}

// ── Remnawave reconciliation (server-side) ───────────────────────────────────
// POST /api/deploy-jobs/sync-remnawave reconciles the cards with Remnawave: it
// adds a card for every node without one and removes remnanode cards whose node
// is gone (HAProxy relay cards are never touched). dry_run=true only previews.
// The removal lives on the SERVER (it writes tombstones); this client never
// deletes anything itself.
export interface RemnawaveSyncEntry { taskId: string; ip: string; domain: string }
export interface RemnawaveSyncSkipped { ip: string; reason: string }
export interface RemnawaveSyncReport {
  ok: boolean;
  dry_run: boolean;
  added: RemnawaveSyncEntry[];
  removed: RemnawaveSyncEntry[];
  skipped: RemnawaveSyncSkipped[];
  nodes_total: number;
  cards_before: number;
  cards_after: number;
}

export async function syncRemnawaveCards(dryRun: boolean): Promise<RemnawaveSyncReport> {
  const res = await fetch("/api/deploy-jobs/sync-remnawave", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dry_run: dryRun }),
  });
  if (!res.ok) throw new Error(`sync-remnawave ${res.status}`);
  return (await res.json()) as RemnawaveSyncReport;
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

// One-shot additive sync: pull the authoritative server list, drop cards the
// server has tombstoned (deleted on purpose) from the local cache, merge server
// cards with the remaining local cache (server wins on taskId conflicts), upload
// any local-only cards, and write the merged list back to the local cache. It
// NEVER deletes anything on the server — deletion is only the explicit UI
// "delete" button (deleteDeployJob) or the server-side Remnawave sync (which
// writes its OWN tombstones). If the API is unreachable it degrades to the local
// cache (offline) without touching the server, so local data is never lost.
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

  // Tombstones are best-effort: an old backend without the route must not break
  // the sync (no tombstones → the previous behaviour, unchanged).
  let tombstones: string[] = [];
  try {
    tombstones = await fetchDeployJobTombstones();
  } catch { /* no tombstone list available → skip the cache prune */ }
  const tombstoned = new Set(tombstones);

  // Purge cards the server deleted on purpose BEFORE merging, so a stale cache
  // can't re-upload them (the additive-sync resurrection).
  const local = opts.loadLocal().filter(j => !tombstoned.has(j.taskId));
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
