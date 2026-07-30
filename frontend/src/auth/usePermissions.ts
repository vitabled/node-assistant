// Привилегии текущего пользователя (GET /api/auth/me) для сборки интерфейса.
//
// ⚠️ ЭТО КОСМЕТИКА, А НЕ ГРАНИЦА ДОСТУПА. Скрытый пункт меню НИЧЕГО не защищает:
// список привилегий приходит в браузер, лежит в памяти страницы и правится в
// консоли за секунду. Единственная граница — сервер: `api/auth.py::
// require_identity` проверяет привилегию по таблице `services/permissions.py` на
// КАЖДЫЙ запрос. Поэтому появление новой ручки требует записи в той таблице, и
// «в UI кнопки всё равно нет» — не причина пропустить серверную проверку.
//
// Модуль-синглтон, а не состояние в компоненте: личность нужна одновременно
// сайдбару (что показывать), App (куда откатить недоступную вкладку) и меню
// аккаунта (роли). Три копии запроса дали бы три разных состояния загрузки.

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { getActiveId, getActiveToken, forget } from "./store";

export interface IdentityRole { id: string; name: string }

export interface Identity {
  id: string;
  login: string;
  is_superuser: boolean;
  disabled: boolean;
  role_ids: string[];
  roles: IdentityRole[];
  permissions: string[];
}

interface State {
  user: Identity | null;
  permissions: string[];
  loading: boolean;
  /** Личность успешно получена. Пока false — состав прав НЕИЗВЕСТЕН. */
  known: boolean;
}

const EMPTY: State = { user: null, permissions: [], loading: true, known: false };

let state: State = EMPTY;
let loadedFor: string | null = null;      // для какого аккаунта устройства загружено
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function commit(next: State) {
  state = next;
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}
function getSnapshot(): State {
  return state;
}

function load(): Promise<void> {
  const id = getActiveId();
  if (!id) {
    loadedFor = null;
    if (state !== EMPTY) commit({ ...EMPTY, loading: false });
    return Promise.resolve();
  }
  if (loadedFor === id) return Promise.resolve();
  if (inflight) return inflight;

  inflight = (async () => {
    try {
      // ⚠️ Заголовок ставим руками: глобальный перехват fetch (apiClient.ts)
      // намеренно пропускает /api/auth/* — иначе он сбрасывал бы сессию на 401
      // от самого входа. Значит /me своей авторизации не получит.
      const res = await fetch("/api/auth/me", {
        headers: { Authorization: `Bearer ${getActiveToken()}` },
      });
      if (res.status === 401) {
        // Токен мёртв: смена пароля или ролей бампит token_version на сервере.
        // Оставить человека в панели без прав нельзя — возвращаем на экран входа.
        forget(id);
        loadedFor = null;
        commit({ ...EMPTY, loading: false });
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      const me = (await res.json()) as Identity;
      loadedFor = id;
      commit({
        user: me,
        permissions: Array.isArray(me.permissions) ? me.permissions : [],
        loading: false,
        known: true,
      });
    } catch {
      // Сеть или сервер отказали. Спрятать всё — значит показать пустую панель,
      // причём навсегда; поэтому остаёмся в состоянии «права неизвестны», где
      // `can` разрешает. Реальный отказ придёт с сервера как 403.
      loadedFor = id;
      commit({ user: null, permissions: [], loading: false, known: false });
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/** Сброс кэша. В приложении не нужен (кэш ключуется активным аккаунтом), но
 *  тестам нужен: модуль — синглтон и живёт между проверками одного файла. */
export function resetIdentity() {
  loadedFor = null;
  inflight = null;
  state = EMPTY;
}

export function usePermissions() {
  const snap = useSyncExternalStore(subscribe, getSnapshot);
  useEffect(() => { void load(); }, []);

  // Права НЕИЗВЕСТНЫ (грузим или запрос не удался) → разрешаем. Иначе панель
  // мигала бы пустым сайдбаром на каждой загрузке. Суперпользователю сервер
  // отдаёт полный список привилегий, поэтому отдельной ветки для него нет.
  const can = useCallback(
    (permission: string) => !snap.known || snap.permissions.includes(permission),
    [snap],
  );

  return { user: snap.user, permissions: snap.permissions, can, loading: snap.loading };
}
