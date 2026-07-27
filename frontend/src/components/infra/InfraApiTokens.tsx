// «Инфра-биллинг → API токены» — доступы к API хостингов.
//
// Экран больше НЕ хранит один секрет под зашитый список из шести провайдеров:
// креды живут в общем Хранилище (`/api/vault`, kind `provider_creds`), а форма
// полей строится из `GET /api/infra-billing/adapters` — у адаптеров от 2 до 5
// полей, и новый адаптер на бэкенде появляется здесь без правок фронтенда.
//
// Записи старого формата (`/api/infra-billing/api-tokens`) показываются отдельной
// секцией только на чтение и удаление: заводить их заново незачем.
//
// Проверки соединения тут нет намеренно — она живёт в «Провайдерах» (кнопка
// «Синхронизировать»), где известно, к какому провайдеру привязана запись.
import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound, Plus, Loader2, Trash2, RefreshCw, Pencil, ShieldCheck, Archive } from "lucide-react";
import { infraApi, type ApiToken, type ProviderAdapterInfo } from "./api";
import { toast } from "./Toast";
import { Page, PageHeader, Field, Modal, fmtDate } from "./ui";
import { SecretField } from "../vault/SecretField";
import {
  listEntries, createEntry, updateEntry, deleteEntry,
  type VaultEntry, type VaultEntryBody, type VaultEntryPatch,
} from "../vault/api";

const CAP_LABELS: Record<string, string> = {
  balance: "баланс", services: "услуги", payments: "платежи",
};
const capsText = (caps: string[]) =>
  caps.map(c => CAP_LABELS[c] ?? c).join(", ") || "—";

/** Название адаптера по его kind; неизвестный kind показываем как есть. */
const adapterTitle = (adapters: ProviderAdapterInfo[], kind: string) =>
  adapters.find(a => a.kind === kind)?.title ?? (kind || "—");

export function InfraApiTokens() {
  const [entries, setEntries] = useState<VaultEntry[]>([]);
  const [adapters, setAdapters] = useState<ProviderAdapterInfo[]>([]);
  const [legacy, setLegacy] = useState<ApiToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<null | { edit?: VaultEntry }>(null);
  // Двухкликовое подтверждение удаления: ключ с префиксом, потому что в списке
  // соседствуют записи Хранилища и токены старого формата.
  const [confirmKey, setConfirmKey] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const [ent, ad, old] = await Promise.allSettled([
      listEntries(), infraApi.listAdapters(), infraApi.listTokens(),
    ]);
    if (ent.status === "fulfilled") setEntries(ent.value);
    else { setEntries([]); toast(String((ent.reason as Error)?.message ?? "Не удалось загрузить Хранилище"), "error"); }
    // Реестр адаптеров и устаревший волт не критичны для экрана: их сбой
    // сворачивает свой блок, но не прячет список кредов.
    setAdapters(ad.status === "fulfilled" ? ad.value : []);
    setLegacy(old.status === "fulfilled" ? old.value : []);
    setConfirmKey("");
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => entries.filter(e => e.kind === "provider_creds"), [entries]);

  const removeEntry = async (e: VaultEntry) => {
    const key = `v:${e.id}`;
    if (confirmKey !== key) { setConfirmKey(key); return; }
    try {
      await deleteEntry(e.id);
      toast("Запись удалена", "success");
      load();
    } catch (ex) { toast(String((ex as Error).message), "error"); }
  };

  const removeLegacy = async (t: ApiToken) => {
    const key = `t:${t.id}`;
    if (confirmKey !== key) { setConfirmKey(key); return; }
    try {
      await infraApi.deleteToken(t.id);
      toast("Ключ удалён", "success");
      load();
    } catch (ex) { toast(String((ex as Error).message), "error"); }
  };

  return (
    <Page>
      <PageHeader icon={<KeyRound size={16} className="text-[var(--accent-hi)]" />}
        title="Доступы к API хостингов"
        subtitle="Креды провайдеров в Хранилище — их читает сервер при синхронизации баланса"
        actions={<>
          <button onClick={load} className="iconbtn" title="Обновить"><RefreshCw size={13} /></button>
          <button onClick={() => setModal({})} className="btn btn-primary" disabled={adapters.length === 0}>
            <Plus size={13} /> Добавить
          </button>
        </>} />

      <div className="mb-4 flex items-start gap-2 px-3 py-2 rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] text-[11px] text-[var(--t-low)]">
        <ShieldCheck size={13} className="text-[var(--ok)] mt-0.5 shrink-0" />
        <span>
          Секреты шифруются (Fernet) и на фронтенд не возвращаются — видна только маска.
          Запись выбирается в разделе «Провайдеры», там же кнопка «Синхронизировать» проверяет
          доступ и подтягивает баланс.
        </span>
      </div>

      {adapters.length === 0 && !loading && (
        <p className="mb-4 text-xs text-[var(--t-low)]">
          Список адаптеров недоступен — сервер не отдал <code>/adapters</code>. Новую запись
          завести нельзя, пока он не ответит.
        </p>
      )}

      <div className="rounded-xl border border-[var(--line-soft)] overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-[var(--raised)] text-[var(--t-low)] text-[11px] uppercase tracking-widest">
            <tr>
              <th className="text-left font-medium px-4 py-2.5">Название</th>
              <th className="text-left font-medium px-4 py-2.5">Хостинг</th>
              <th className="text-left font-medium px-4 py-2.5">Секрет</th>
              <th className="text-left font-medium px-4 py-2.5">Изменён</th>
              <th className="text-right font-medium px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--line-soft)]">
            {loading ? (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-[var(--t-faint)]"><Loader2 size={16} className="animate-spin inline" /></td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-[var(--t-faint)] text-xs">
                Доступов нет. Добавьте креды хостинга — потом их можно выбрать у провайдера.
              </td></tr>
            ) : rows.map(e => (
              <tr key={e.id} className="hover:bg-[var(--row-hover)]">
                <td className="px-4 py-2.5 text-[var(--t-hi)]">
                  {e.name}
                  {e.note && <p className="text-[11px] text-[var(--t-faint)] mt-0.5">{e.note}</p>}
                </td>
                <td className="px-4 py-2.5 text-[var(--t-mid)]">{adapterTitle(adapters, e.resource)}</td>
                <td className="px-4 py-2.5 text-xs">
                  {e.broken ? (
                    <span className="text-[var(--err)]">не расшифровывается — сменился ENCRYPTION_KEY</span>
                  ) : e.has_secret ? (
                    <>
                      <span className="font-mono text-[var(--t-low)]">{e.hint}</span>
                      {e.field_names.length > 0 && (
                        <p className="text-[11px] text-[var(--t-faint)] mt-0.5">
                          поля: {e.field_names.join(", ")}
                        </p>
                      )}
                    </>
                  ) : (
                    <span className="text-[var(--warn)]">секрет не задан</span>
                  )}
                </td>
                {/* fmtDate сам умножает число на 1000 — тут именно секунды. */}
                <td className="px-4 py-2.5 text-[var(--t-low)] tabular-nums">
                  {fmtDate(e.updated_at || e.created_at)}
                </td>
                <td className="px-4 py-2.5 text-right whitespace-nowrap">
                  <button onClick={() => setModal({ edit: e })} title="Изменить"
                    className="p-1.5 text-[var(--t-low)] hover:text-[var(--accent-hi)]"><Pencil size={13} /></button>
                  <button onClick={() => removeEntry(e)} title="Удалить"
                    className="p-1.5 text-[var(--t-low)] hover:text-[var(--err)]">
                    <Trash2 size={13} className="inline" />
                    {confirmKey === `v:${e.id}` && <span className="ml-1 text-[11px] text-[var(--err)]">ещё раз?</span>}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {legacy.length > 0 && (
        <section className="mt-7">
          <div className="flex items-center gap-2 mb-2">
            <Archive size={13} className="text-[var(--t-faint)]" />
            <h2 className="text-xs font-semibold text-[var(--t-mid)]">Устаревший формат</h2>
          </div>
          <p className="text-[11px] text-[var(--t-low)] mb-2">
            Ключи из старого хранилища биллинга: одно поле-секрет, адаптеру их не передать.
            Заведите доступ заново выше и удалите запись отсюда.
          </p>
          <div className="rounded-xl border border-[var(--line-soft)] overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-[var(--raised)] text-[var(--t-low)] text-[11px] uppercase tracking-widest">
                <tr>
                  <th className="text-left font-medium px-4 py-2.5">Название</th>
                  <th className="text-left font-medium px-4 py-2.5">Провайдер</th>
                  <th className="text-left font-medium px-4 py-2.5">Ключ</th>
                  <th className="text-left font-medium px-4 py-2.5">Добавлен</th>
                  <th className="text-right font-medium px-4 py-2.5"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--line-soft)]">
                {legacy.map(t => (
                  <tr key={t.id} className="hover:bg-[var(--row-hover)]">
                    <td className="px-4 py-2.5 text-[var(--t-mid)]">{t.name}</td>
                    <td className="px-4 py-2.5 text-[var(--t-low)]">{t.providerKind}</td>
                    <td className="px-4 py-2.5 font-mono text-[var(--t-low)] text-xs">{t.masked}</td>
                    <td className="px-4 py-2.5 text-[var(--t-low)] tabular-nums">{fmtDate(t.createdAt)}</td>
                    <td className="px-4 py-2.5 text-right whitespace-nowrap">
                      <button onClick={() => removeLegacy(t)} title="Удалить"
                        className="p-1.5 text-[var(--t-low)] hover:text-[var(--err)]">
                        <Trash2 size={13} className="inline" />
                        {confirmKey === `t:${t.id}` && <span className="ml-1 text-[11px] text-[var(--err)]">ещё раз?</span>}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {modal && (
        <CredsModal
          edit={modal.edit}
          adapters={adapters}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); load(); }}
        />
      )}
    </Page>
  );
}

// ── Модалка: адаптер + его динамические поля ──────────────────
function CredsModal({ edit, adapters, onClose, onSaved }: {
  edit?: VaultEntry;
  adapters: ProviderAdapterInfo[];
  onClose: () => void;
  onSaved: () => void;
}) {
  // `resource` записи = kind адаптера: так видно, к какому API она подходит.
  const [kind, setKind] = useState(edit?.resource ?? adapters[0]?.kind ?? "");
  const [name, setName] = useState(edit?.name ?? "");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const adapter = adapters.find(a => a.kind === kind);
  // Запись могла быть заведена под адаптер, которого в этой сборке уже нет —
  // не теряем её значение из селектора, иначе редактирование молча его подменит.
  const options = useMemo(() => {
    const known = adapters.map(a => ({ v: a.kind, l: a.title }));
    return kind && !adapters.some(a => a.kind === kind)
      ? [{ v: kind, l: `${kind} (адаптер недоступен)` }, ...known]
      : known;
  }, [adapters, kind]);

  const filled = Object.entries(fields).filter(([, v]) => v.trim() !== "");
  const missing = (adapter?.fields ?? []).filter(f => f.required && !(fields[f.key] ?? "").trim());
  // При редактировании пустые поля секрета = «не менять», поэтому required
  // блокирует только создание.
  const canSave = name.trim() !== "" && kind !== "" && (edit ? true : missing.length === 0);

  const submit = async () => {
    if (!canSave) {
      toast(name.trim() ? "Заполните обязательные поля кредов" : "Укажите название связки", "error");
      return;
    }
    setSaving(true);
    try {
      if (edit) {
        // PUT — патч: без ключа `fields` сохранённый секрет остаётся нетронутым,
        // а username/note/tags записи не затираются.
        const patch: VaultEntryPatch = { name: name.trim(), resource: kind };
        if (filled.length) patch.fields = Object.fromEntries(filled);
        await updateEntry(edit.id, patch);
        toast("Доступ обновлён", "success");
      } else {
        const body: VaultEntryBody = {
          name: name.trim(), kind: "provider_creds", resource: kind,
          username: "", note: "", tags: [], fields: Object.fromEntries(filled),
        };
        await createEntry(body);
        toast("Доступ сохранён (зашифрован)", "success");
      }
      onSaved();
    } catch (e) {
      toast(String((e as Error).message), "error");
      setSaving(false);
    }
  };

  return (
    <Modal title={edit ? "Изменить доступ" : "Новый доступ к API хостинга"} onClose={onClose} wide
      footer={<>
        <button onClick={onClose} className="btn btn-ghost">Отмена</button>
        <button onClick={submit} disabled={saving || !canSave} className="btn btn-primary">
          {saving && <Loader2 size={13} className="animate-spin" />} Сохранить
        </button>
      </>}>

      <div className="flex flex-col gap-1">
        <label className="label">Хостинг</label>
        <select value={kind} className="selectbox"
          onChange={e => { setKind(e.target.value); setFields({}); }}>
          {options.length === 0 && <option value="">— адаптеры недоступны —</option>}
          {options.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
        </select>
        {adapter && (
          <p className="hint">
            Умеет: {capsText(adapter.caps)}
            {!adapter.caps.includes("balance") && " — баланс этот API не отдаёт, вводите вручную"}
          </p>
        )}
      </div>

      <Field label="Название связки" value={name} onChange={setName}
        placeholder={adapter ? `${adapter.title} — прод` : "прод"} />

      {(adapter?.fields ?? []).map(f => (
        <SecretField
          key={`${kind}:${f.key}`}
          label={f.label + (f.required && !edit ? " *" : "")}
          kind={f.kind}
          value={fields[f.key] ?? ""}
          saved={!!edit && edit.has_secret}
          onChange={v => setFields(prev => ({ ...prev, [f.key]: v }))}
        />
      ))}

      {adapter && adapter.fields.length === 0 && (
        <p className="hint">Этому адаптеру поля кредов не нужны.</p>
      )}

      {edit && edit.field_names.length > 0 && (
        <p className="hint">
          Сохранённые поля: {edit.field_names.join(", ")}. Пустые поля не меняют сохранённый секрет.
        </p>
      )}

      <p className="hint">
        Эту запись можно выбрать в «Провайдерах» и синхронизировать баланс — там же
        проверяется доступ к API. Секрет читает сервер, в браузер он не возвращается.
      </p>
    </Modal>
  );
}
