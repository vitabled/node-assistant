// Список карточек нод живёт НА СЕРВЕРЕ: GET /api/deploy-jobs — единственный
// источник истины. Модуль держит последний ответ сервера в памяти вкладки и
// раздаёт его синхронно (getJobs) всем панелям, которым нужны карточки: деплой,
// домены, SSL, тестовые серверы, REGRU, редактор страницы, доступность.
//
// localStorage в контуре карточек больше НЕ участвует: ни чтение, ни запись.
// Единственное касание — одноразовая миграция уже накопленных карточек в
// аккаунт на сервере: она выполняется только если сервер ответил пустым
// списком, после чего локальный ключ удаляется. Дальше кеш и память браузера на
// карточки не влияют никак — перезагрузка, другой браузер, очищенное хранилище
// или подсунутый старый кэш ничего не меняют, список всегда приходит с сервера.
//
// Почему не «локальный кеш для офлайна», как было: кеш показывал карточки,
// которых на сервере уже нет (или не было), и аддитивный синк заливал их
// обратно. Отказ от кеша убирает сам класс расхождений: показано ровно то, что
// лежит в аккаунте.
import { useSyncExternalStore } from "react";
import { deployJobsKey } from "../auth/store";
import { deleteDeployJob, fetchDeployJobs, upsertDeployJob } from "../components/deployJobsSync";

/** Карточка ноды в том виде, в каком её отдаёт /api/deploy-jobs. */
export interface DeployJobRecord {
  taskId: string;
  domain: string;
  ip: string;
  newSshPort?: number;
  startedAt: number;
  // Полная форма деплоя (креды, режим, компоненты) — на сервере шифруется Fernet.
  // Непрозрачна для стора: он её только хранит и отдаёт обратно.
  savedForm?: unknown;
  finalStatus?: string;
  color?: string;
}

let snapshot: readonly DeployJobRecord[] = [];
const listeners = new Set<() => void>();
let inFlight: Promise<readonly DeployJobRecord[]> | null = null;
// Одноразовая миграция legacy-кэша на процесс: второй раз уже накопленное из
// localStorage не заливаем (иначе удалённые на сервере карточки воскресают).
let migrationDone = false;
let loadError: string | null = null;

function emit(): void {
  for (const listener of listeners) {
    try { listener(); } catch { /* подписчик упал — остальные всё равно уведомим */ }
  }
}

/** Синхронный снимок серверного списка (пустой до первой загрузки). */
export function getJobs(): readonly DeployJobRecord[] {
  return snapshot;
}

export function subscribeJobs(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Подписка React на серверный список: перерисовка при каждом обновлении. */
export function useDeployJobs(): readonly DeployJobRecord[] {
  return useSyncExternalStore(subscribeJobs, getJobs, getJobs);
}

/** Последняя ошибка загрузки (null — всё хорошо). */
export function jobsLoadError(): string | null {
  return loadError;
}

/** Заменить снимок в памяти (после разбора серверного ответа). */
export function setJobs(next: readonly DeployJobRecord[]): void {
  snapshot = [...next];
  emit();
}

/** Забыть состояние при смене аккаунта/инстанса. */
export function resetJobs(): void {
  snapshot = [];
  inFlight = null;
  migrationDone = false;
  loadError = null;
  emit();
}

function dropLegacyCache(): void {
  try { localStorage.removeItem(deployJobsKey()); } catch { /* приватный режим */ }
}

function readLegacyCache(): DeployJobRecord[] {
  try {
    const raw = localStorage.getItem(deployJobsKey());
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((job): job is DeployJobRecord =>
      Boolean(job) && typeof job === "object" && typeof (job as { taskId?: unknown }).taskId === "string");
  } catch { return []; }
}

/**
 * Перечитать карточки с сервера. Сервер — единственный источник: при
 * недоступности API список остаётся пустым (никакой подстановки из
 * localStorage), а ошибка уходит в `jobsLoadError()`.
 *
 * Одноразовая миграция: если сервер отдал пустой список, а в браузере остался
 * старый `deploy_jobs_<account>` — заливаем его POST-ами и перечитываем. Ключ
 * удаляется в любом случае, поэтому после первого успешного ответа кеш больше
 * ни на что не влияет.
 */
export async function refreshJobs(): Promise<readonly DeployJobRecord[]> {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    try {
      let jobs = await fetchDeployJobs<DeployJobRecord>();

      if (!migrationDone) {
        migrationDone = true;
        if (jobs.length === 0) {
          const legacy = readLegacyCache();
          if (legacy.length) {
            let uploaded = 0;
            for (const job of legacy) {
              try {
                const res = await upsertDeployJob(job);
                if (!res.ignored) uploaded += 1;
              } catch { /* карточку не приняли — она останется только в удаляемом кэше */ }
            }
            if (uploaded > 0) {
              try { jobs = await fetchDeployJobs<DeployJobRecord>(); } catch { jobs = legacy; }
            }
          }
        }
      }

      dropLegacyCache(); // кеш браузера больше не источник карточек
      loadError = null;
      setJobs(jobs);
      return snapshot;
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
      throw err;
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

/** Создать или обновить карточку: сервер пишет, память отражает ответ. */
export async function saveJob(job: DeployJobRecord): Promise<boolean> {
  const res = await upsertDeployJob(job);
  if (res.ignored) {
    // Сервер отказал: карточка удалена на его стороне (tombstone) — убираем и у себя.
    setJobs(snapshot.filter(item => item.taskId !== job.taskId));
    return false;
  }
  setJobs([...snapshot.filter(item => item.taskId !== job.taskId), job]);
  return true;
}

/** Изменить поля карточки (цвет и прочее) через серверный upsert. */
export async function patchJob(taskId: string, patch: Partial<DeployJobRecord>): Promise<boolean> {
  const current = snapshot.find(item => item.taskId === taskId);
  if (!current) return false;
  return saveJob({ ...current, ...patch });
}

/** Удалить карточку (явное действие пользователя). */
export async function removeJob(taskId: string): Promise<void> {
  await deleteDeployJob(taskId);
  setJobs(snapshot.filter(item => item.taskId !== taskId));
}
