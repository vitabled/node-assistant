import { useState, useEffect, useCallback } from "react";
import { FolderKanban, Plus, Loader2, Pencil, Trash2, RefreshCw, Boxes } from "lucide-react";
import { infraApi, type Project } from "./api";
import { toast } from "./Toast";
import { MultiSelect } from "../MultiSelect";
import { Page, PageHeader, Field, Modal, fmtNum, loadDeployNodes } from "./ui";

export function InfraProjects() {
  const [rows, setRows] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<null | { edit?: Project }>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await infraApi.listProjects()); }
    catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const del = async (p: Project) => {
    if (!confirm(`Удалить проект «${p.name}»?`)) return;
    try { await infraApi.deleteProject(p.id); toast("Проект удалён", "success"); load(); }
    catch (e) { toast((e as Error).message, "error"); }
  };

  return (
    <Page>
      <PageHeader icon={<FolderKanban size={16} className="text-blue-400" />} title="Проекты"
        subtitle="Логическая группировка нод и ресурсов"
        actions={<>
          <button onClick={load} className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><RefreshCw size={13} /></button>
          <button onClick={() => setModal({})} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white"><Plus size={13} /> Проект</button>
        </>} />

      {loading ? (
        <div className="py-16 text-center text-gray-600"><Loader2 size={18} className="animate-spin inline" /></div>
      ) : rows.length === 0 ? (
        <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-8 text-center text-gray-600 text-sm">Проектов пока нет.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {rows.map(p => (
            <div key={p.id} className="rounded-xl border border-gray-800 bg-gray-900/40 p-4 flex flex-col gap-2">
              <div className="flex items-start justify-between gap-2">
                <span className="text-sm font-medium text-gray-100 truncate">{p.name}</span>
                <div className="flex shrink-0">
                  <button onClick={() => setModal({ edit: p })} className="p-1 text-gray-500 hover:text-blue-400"><Pencil size={12} /></button>
                  <button onClick={() => del(p)} className="p-1 text-gray-500 hover:text-red-400"><Trash2 size={12} /></button>
                </div>
              </div>
              {p.description && <p className="text-xs text-gray-500 line-clamp-2">{p.description}</p>}
              <div className="flex items-center justify-between mt-auto pt-2 text-xs">
                <span className="text-gray-500 flex items-center gap-1"><Boxes size={12} /> {p.nodeCount} нод</span>
                <span className="text-gray-300 tabular-nums">{fmtNum(p.monthlyCost)}/мес</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {modal && <ProjectModal edit={modal.edit} onClose={() => setModal(null)} onSaved={() => { setModal(null); load(); }} />}
    </Page>
  );
}

function ProjectModal({ edit, onClose, onSaved }: { edit?: Project; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(edit?.name ?? "");
  const [desc, setDesc] = useState(edit?.description ?? "");
  const [nodes, setNodes] = useState<string[]>(edit?.node_uuids ?? []);
  const [saving, setSaving] = useState(false);
  const options = loadDeployNodes().map(n => ({ value: n.value, label: n.label }));

  const submit = async () => {
    if (!name.trim()) { toast("Укажите название проекта", "error"); return; }
    setSaving(true);
    const body = { name: name.trim(), description: desc, node_uuids: nodes };
    try {
      if (edit) await infraApi.updateProject(edit.id, body);
      else await infraApi.createProject(body);
      toast(edit ? "Проект обновлён" : "Проект создан", "success"); onSaved();
    } catch (e) { toast((e as Error).message, "error"); setSaving(false); }
  };

  return (
    <Modal title={edit ? "Редактировать проект" : "Новый проект"} onClose={onClose}
      footer={<>
        <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-gray-400 hover:text-gray-200">Отмена</button>
        <button onClick={submit} disabled={saving} className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
          {saving && <Loader2 size={13} className="animate-spin" />} Сохранить
        </button>
      </>}>
      <Field label="Название" value={name} onChange={setName} placeholder="Production" />
      <Field label="Описание" value={desc} onChange={setDesc} placeholder="Клиент / окружение…" />
      <MultiSelect label="Ноды деплоя" selected={nodes} onChange={setNodes} options={options}
        placeholder={options.length ? "— выберите ноды —" : "нет развёрнутых нод"} />
    </Modal>
  );
}
