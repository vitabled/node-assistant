// Create/edit a vault entry (Wave-9 Plan A Ф6).
//
// The secret half of the form is built from GET /api/vault/schemas, so adding a
// credential shape on the backend needs no change here. On edit the secret
// fields start EMPTY and an empty submit omits `fields` entirely — the stored
// blob survives a rename without the browser ever holding the plaintext.
import { useEffect, useMemo, useState } from "react";
import { Modal, Field } from "../infra/ui";
import { TagInput } from "../hostings/TagInput";
import { SecretField } from "./SecretField";
import {
  createEntry, updateEntry, getSchemas, KIND_LABELS,
  type VaultEntry, type VaultKind, type VaultSchema,
} from "./api";

export function EntryModal({ entry, onClose, onSaved }: {
  entry: VaultEntry | null;          // null = создание
  onClose: () => void;
  onSaved: () => void;
}) {
  const [schemas, setSchemas] = useState<VaultSchema[]>([]);
  const [kind, setKind] = useState<VaultKind>(entry?.kind ?? "api_key");
  const [name, setName] = useState(entry?.name ?? "");
  const [resource, setResource] = useState(entry?.resource ?? "");
  const [username, setUsername] = useState(entry?.username ?? "");
  const [note, setNote] = useState(entry?.note ?? "");
  const [tags, setTags] = useState<string[]>(entry?.tags ?? []);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { getSchemas().then(setSchemas).catch(() => setSchemas([])); }, []);

  const schema = useMemo(() => schemas.find(s => s.kind === kind), [schemas, kind]);
  const filled = Object.entries(fields).filter(([, v]) => v.trim() !== "");
  const missing = (schema?.fields ?? []).filter(
    f => f.required && !(fields[f.key] ?? "").trim());
  // On edit, leaving every secret field blank is the "keep it" path, so required
  // fields only block a NEW entry.
  const canSave = name.trim() !== "" && (entry ? true : missing.length === 0);

  const save = async () => {
    setBusy(true); setErr("");
    try {
      const base = { name: name.trim(), kind, resource, username, note, tags };
      if (entry) {
        await updateEntry(entry.id, filled.length
          ? { ...base, fields: Object.fromEntries(filled) }
          : base);
      } else {
        await createEntry({ ...base, fields: Object.fromEntries(filled) });
      }
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={entry ? "Изменить запись" : "Новая запись"}
      onClose={onClose}
      wide
      footer={<>
        <button className="btn" onClick={onClose}>Отмена</button>
        <button className="btn btn-primary" disabled={!canSave || busy} onClick={save}>
          {busy ? "Сохранение…" : "Сохранить"}
        </button>
      </>}
    >
      <label className="flex flex-col gap-1">
        <span className="micro">Тип</span>
        <select className="input" value={kind} disabled={!!entry}
          onChange={e => { setKind(e.target.value as VaultKind); setFields({}); }}>
          {(schemas.length
            ? schemas.map(s => ({ kind: s.kind, title: s.title }))
            : (Object.keys(KIND_LABELS) as VaultKind[]).map(k => ({ kind: k, title: KIND_LABELS[k] }))
          ).map(o => <option key={o.kind} value={o.kind}>{o.title}</option>)}
        </select>
        {entry && <span className="micro" style={{ color: "var(--t-low)" }}>
          Тип нельзя изменить — создайте новую запись
        </span>}
      </label>

      <Field label="Название" value={name} onChange={setName} placeholder="prod-root" />
      <Field label="Ресурс" value={resource} onChange={setResource}
        placeholder="10.0.0.1 / api.provider.com" />
      <Field label="Логин" value={username} onChange={setUsername} placeholder="root" />

      {(schema?.fields ?? []).map(f => (
        <SecretField
          key={f.key}
          label={f.label + (f.required && !entry ? " *" : "")}
          kind={f.kind}
          value={fields[f.key] ?? ""}
          saved={!!entry && entry.has_secret}
          onChange={v => setFields(prev => ({ ...prev, [f.key]: v }))}
        />
      ))}
      {schema && schema.fields.length === 0 && (
        <p className="micro" style={{ color: "var(--t-low)" }}>
          Для этого типа поля секрета задаёт модуль-потребитель.
        </p>
      )}

      <TagInput label="Теги" value={tags} onChange={setTags} />
      <Field label="Заметка" value={note} onChange={setNote} placeholder="" />

      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}
    </Modal>
  );
}
