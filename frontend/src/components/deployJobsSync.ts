// Wire-вызовы карточек нод: GET /api/deploy-jobs — единственный источник
// истины, POST — upsert по taskId, DELETE — только явное удаление из UI.
// Состояние (память вкладки, миграция legacy-кэша, подписки) живёт в
// data/deployJobsStore.ts; localStorage в контуре карточек не используется.
//   GET    /api/deploy-jobs          -> { jobs: [...] }   (authoritative list)
//   POST   /api/deploy-jobs          -> { job, ignored? }
//   DELETE /api/deploy-jobs/{taskId} -> { ok: true }
// Auth-заголовки добавляет глобальный fetch-перехватчик (auth/apiClient.ts).
export async function fetchDeployJobs<T extends { taskId: string }>(): Promise<T[]> {
  const res = await fetch("/api/deploy-jobs", { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`deploy-jobs GET ${res.status}`);
  const data = await res.json();
  return Array.isArray(data?.jobs) ? (data.jobs as T[]) : [];
}

// Create or replace the single card identified by its taskId (upsert). Unlike a
// full-list PUT this never clobbers cards another client has pushed, so it is the
// only safe write while the server is the source of truth. `ignored: true` means
// the server refused the write because the card was deleted on purpose
// (tombstone) — the caller must drop it from its in-memory list.
export async function upsertDeployJob<T extends { taskId: string }>(
  job: T,
): Promise<{ ignored: boolean }> {
  const res = await fetch("/api/deploy-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(job),
  });
  if (!res.ok) throw new Error(`deploy-jobs POST ${res.status}`);
  const data = await res.json().catch(() => null);
  return { ignored: data?.ignored === true };
}

// Explicit user-initiated delete (the "delete" button in the UI). SYNC MUST NOT
// call this — an additive sync that deleted server cards it didn't recognize would
// destroy Fernet-encrypted SSH credentials. Idempotent: a 404 (already gone) is
// treated as success so a double-fire never surfaces a false error.
export async function deleteDeployJob(taskId: string): Promise<void> {
  const res = await fetch(`/api/deploy-jobs/${encodeURIComponent(taskId)}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw new Error(`deploy-jobs DELETE ${res.status}`);
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

// Слияние серверного списка с локальным кешем (reconcileJobs/syncDeployJobs)
// удалено вместе с самим локальным кешем: список берётся только с сервера
// (data/deployJobsStore.ts), поэтому сливать больше нечего.

