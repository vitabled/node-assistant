// ============================================================
// «Профили» — Xray config editor (rw-profiles section).
//
// Ported & re-skinned from bropines/xray-config-ui-editor (MIT, © 2026 Sergey
// Pinus) into the node-installer design system (var-token theme, project modals).
// Core (store + ajv schemas/validators + generators + link tools + CodeMirror
// editor) is a faithful port; the shell/UI is authored in our style.
//
// Deferred (not in this phase): the graph TOPOLOGY view (@xyflow/react + dagre)
// and geo/proto WEB-WORKERS — shown as «недоступно». Remnawave SYNC must go
// through our backend proxy (never a direct browser→panel CORS call); the
// backend route does not exist yet → the button is a documented TODO stub.
// ============================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  FileCode2, Upload, Download, Trash2, Wand2, Plus, Pencil, Braces,
  Stethoscope, CloudUpload, ArrowDownToLine, ArrowUpFromLine, ChevronDown, ChevronRight, Layers,
} from 'lucide-react';
import { toast } from '../infra/Toast';
import { useConfigStore } from './store/configStore';
import { emptyConfig } from './core/factories';
import { SectionJsonModal } from './SectionJsonModal';
import { ItemModal } from './ItemModal';
import { GeneratorsModal } from './GeneratorsModal';
import { DiagnosticsPanel, collectDiagnostics } from './DiagnosticsPanel';
import type { SchemaMode } from './core/schema';
import type { Inbound, Outbound, XrayConfig } from './core/types';

type Modal =
  | { type: 'section'; key: keyof XrayConfig; title: string; schemaMode: SchemaMode; data: unknown }
  | { type: 'item'; kind: 'inbound' | 'outbound'; index: number | null }
  | { type: 'generators' }
  | null;

const OTHER_SECTIONS: { key: keyof XrayConfig; title: string; schemaMode: SchemaMode }[] = [
  { key: 'log', title: 'log', schemaMode: 'full' },
  { key: 'api', title: 'api', schemaMode: 'full' },
  { key: 'policy', title: 'policy', schemaMode: 'full' },
  { key: 'stats', title: 'stats', schemaMode: 'full' },
  { key: 'reverse', title: 'reverse', schemaMode: 'full' },
  { key: 'fakedns', title: 'fakedns', schemaMode: 'full' },
  { key: 'metrics', title: 'metrics', schemaMode: 'full' },
  { key: 'observatory', title: 'observatory', schemaMode: 'full' },
  { key: 'burstObservatory', title: 'burstObservatory', schemaMode: 'full' },
  { key: 'transport', title: 'transport', schemaMode: 'full' },
];

export function Profiles() {
  const { config, dirty, hydrate, loadConfig, clearConfig, setConfig,
    addItem, updateItem, deleteItem, updateSection } = useConfigStore();
  const [modal, setModal] = useState<Modal>(null);
  const [showDiag, setShowDiag] = useState(true);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // Hydrate the active account's draft on mount (App is keyed by activeId).
  useEffect(() => { hydrate(); }, [hydrate]);

  const { rows, blockers } = useMemo(() => collectDiagnostics(config), [config]);

  const MAX_IMPORT_BYTES = 5 * 1024 * 1024; // 5 MiB — a sane ceiling for an Xray config

  const readFile = (file: File) => {
    if (file.size > MAX_IMPORT_BYTES) {
      toast('Файл слишком большой (лимит 5 МБ) — импорт отклонён', 'error');
      return;
    }
    const reader = new FileReader();
    reader.onload = e => {
      try {
        const json = JSON.parse(e.target?.result as string);
        const { warnings } = loadConfig(json);
        toast(warnings ? `Загружено с замечаниями (${warnings})` : 'Конфиг загружен', warnings ? 'info' : 'success');
      } catch {
        toast('Некорректный JSON — импорт отклонён', 'error');
      }
    };
    reader.readAsText(file);
  };

  const exportConfig = () => {
    if (!config) return;
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'config.json'; a.click();
    URL.revokeObjectURL(url);
  };

  const sync = () => {
    if (blockers > 0) { toast('Есть критические ошибки — синхронизация заблокирована', 'error'); return; }
    // Backend proxy route (POST → remnawave_client) is not built yet (Волна 2).
    toast('Синхронизация с Remnawave через бэкенд появится позже', 'info');
  };

  // ── Empty state ──────────────────────────────────────────────
  if (!config) {
    return (
      <div className="flex-1 overflow-y-auto" style={{ padding: 24 }}>
        <div style={{ maxWidth: 560, margin: '0 auto' }}>
          <div className="mb-5">
            <h1 className="h1">Профили</h1>
            <p className="sub">Локальный редактор конфигурации Xray (импорт/экспорт JSON, валидация, генераторы)</p>
          </div>
          <div
            className="card"
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files[0]) readFile(e.dataTransfer.files[0]); }}
            style={{
              padding: '40px 28px', textAlign: 'center', display: 'flex', flexDirection: 'column',
              alignItems: 'center', gap: 16, borderStyle: 'dashed',
              borderColor: dragOver ? 'var(--accent)' : 'var(--line)',
              background: dragOver ? 'var(--accent-dim)' : 'var(--bg2)',
            }}
          >
            <span style={{ width: 56, height: 56, borderRadius: 'var(--r-md)', background: 'var(--accent-dim)', color: 'var(--accent)', display: 'grid', placeItems: 'center' }}>
              <FileCode2 size={28} />
            </span>
            <div>
              <p style={{ fontSize: 15, fontWeight: 600, color: 'var(--t-hi)' }}>Загрузите или создайте конфиг</p>
              <p style={{ fontSize: 13, color: 'var(--t-low)', marginTop: 4 }}>Перетащите сюда config.json или начните с пустого</p>
            </div>
            <div className="flex gap-2">
              <button className="btn btn-primary" onClick={() => fileRef.current?.click()}><Upload size={13} /> Открыть файл</button>
              <button className="btn btn-soft" onClick={() => setConfig(emptyConfig() as XrayConfig)}><Plus size={13} /> Пустой конфиг</button>
            </div>
            <input ref={fileRef} type="file" accept=".json,application/json" style={{ display: 'none' }}
              onChange={e => e.target.files?.[0] && readFile(e.target.files[0])} />
          </div>
        </div>
      </div>
    );
  }

  const inbounds = config.inbounds || [];
  const outbounds = config.outbounds || [];

  const openSection = (key: keyof XrayConfig, title: string, schemaMode: SchemaMode) =>
    setModal({ type: 'section', key, title, schemaMode, data: (config as any)[key] ?? {} });

  // ── Editor ───────────────────────────────────────────────────
  return (
    <div className="flex-1 overflow-y-auto ni-pagebody">
      <div style={{ maxWidth: 1040, margin: '0 auto', padding: '20px 24px' }}>
        {/* Header + toolbar */}
        <div className="ni-pagehead" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
          <div>
            <h1 className="h1">Профили{dirty && <span className="chip accent" style={{ marginLeft: 8, fontSize: 10 }}>не синхронизировано</span>}</h1>
            <p className="sub">Редактор Xray-конфига</p>
          </div>
          <div className="ni-pagehead-actions flex flex-wrap gap-2">
            <button className="btn btn-soft" onClick={() => fileRef.current?.click()}><ArrowDownToLine size={13} /> Импорт</button>
            <button className="btn btn-soft" onClick={exportConfig}><ArrowUpFromLine size={13} /> Экспорт</button>
            <button className="btn btn-soft" onClick={() => setModal({ type: 'generators' })}><Wand2 size={13} /> Генераторы</button>
            <button className="btn btn-primary" onClick={sync} title={blockers ? 'Заблокировано ошибками' : 'Синхронизировать с Remnawave'}>
              <CloudUpload size={13} /> Синхронизировать
            </button>
            <button className="btn btn-danger" onClick={() => { if (confirm('Очистить конфиг?')) clearConfig(); }}><Trash2 size={13} /></button>
            <input ref={fileRef} type="file" accept=".json,application/json" style={{ display: 'none' }}
              onChange={e => e.target.files?.[0] && readFile(e.target.files[0])} />
          </div>
        </div>

        {/* Section grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-3">
          {/* Inbounds */}
          <ListCard
            title="Inbounds" count={inbounds.length} icon={<ArrowDownToLine size={14} />}
            onJson={() => openSection('inbounds', 'inbounds', 'inbounds')}
            onAdd={() => setModal({ type: 'item', kind: 'inbound', index: null })}
          >
            {inbounds.map((ib: Inbound, i: number) => (
              <ItemRow key={i} tag={ib.tag || 'no-tag'} sub={`${ib.protocol} • ${ib.port ?? '—'}`}
                onEdit={() => setModal({ type: 'item', kind: 'inbound', index: i })}
                onDelete={() => deleteItem('inbounds', i)} />
            ))}
          </ListCard>

          {/* Routing */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column', minHeight: 220 }}>
            <CardHead title="Routing" icon={<Layers size={14} />}
              actions={<button className="iconbtn" title="JSON" onClick={() => openSection('routing', 'routing', 'routing')}><Braces size={14} /></button>} />
            <div style={{ padding: 12, fontSize: 12.5, color: 'var(--t-low)' }}>
              <p>Стратегия: <span style={{ color: 'var(--t-hi)' }}>{config.routing?.domainStrategy || 'AsIs'}</span></p>
              <p style={{ marginTop: 4 }}>Правил: <span className="num" style={{ color: 'var(--t-hi)' }}>{config.routing?.rules?.length || 0}</span> · Балансировщиков: <span className="num" style={{ color: 'var(--t-hi)' }}>{config.routing?.balancers?.length || 0}</span></p>
              <button className="btn btn-soft mt-3" onClick={() => openSection('routing', 'routing', 'routing')}><Pencil size={12} /> Редактировать</button>
            </div>
          </div>

          {/* Outbounds */}
          <ListCard
            title="Outbounds" count={outbounds.length} icon={<ArrowUpFromLine size={14} />}
            onJson={() => openSection('outbounds', 'outbounds', 'outbounds')}
            onAdd={() => setModal({ type: 'item', kind: 'outbound', index: null })}
          >
            {outbounds.map((ob: Outbound, i: number) => {
              const addr = (ob.settings as any)?.vnext?.[0]?.address || (ob.settings as any)?.servers?.[0]?.address || (ob.settings as any)?.address || '';
              return (
                <ItemRow key={i} tag={ob.tag || 'no-tag'} sub={`${ob.protocol}${addr ? ' • ' + addr : ''}`}
                  onEdit={() => setModal({ type: 'item', kind: 'outbound', index: i })}
                  onDelete={() => deleteItem('outbounds', i)} />
              );
            })}
          </ListCard>
        </div>

        {/* DNS + other sections */}
        <div className="card mb-3" style={{ padding: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <p className="micro">DNS</p>
            <button className="iconbtn" title="Редактировать DNS" onClick={() => openSection('dns', 'dns', 'dns')}><Pencil size={13} /></button>
          </div>
          <p style={{ fontSize: 12.5, color: 'var(--t-low)', marginTop: 6 }}>
            {config.dns
              ? <>Серверов: <span className="num" style={{ color: 'var(--t-hi)' }}>{(config.dns.servers as any[])?.length || 0}</span> · Стратегия: <span style={{ color: 'var(--t-hi)' }}>{config.dns.queryStrategy || 'UseIP'}</span></>
              : 'DNS не настроен — откройте редактор для инициализации.'}
          </p>
        </div>

        {/* Other sections */}
        <div className="card mb-3" style={{ padding: 14 }}>
          <p className="micro" style={{ marginBottom: 10 }}>Прочие секции</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2">
            {OTHER_SECTIONS.map(s => {
              const present = (config as any)[s.key] != null;
              return (
                <button key={String(s.key)} className="btn btn-soft" style={{ justifyContent: 'space-between' }}
                  onClick={() => openSection(s.key, s.title, s.schemaMode)}>
                  <span className="trunc">{s.title}</span>
                  <span className="dot" style={{ background: present ? 'var(--ok)' : 'var(--t-faint)' }} />
                </button>
              );
            })}
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            Топология (граф) и web-воркеры (geo/proto) — недоступны в этой версии.
          </p>
        </div>

        {/* Diagnostics */}
        <div className="card" style={{ padding: 14 }}>
          <button onClick={() => setShowDiag(v => !v)}
            style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', background: 'none', border: 0, cursor: 'pointer', color: 'var(--t-hi)' }}>
            {showDiag ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            <Stethoscope size={14} />
            <span className="micro">Диагностика</span>
            {rows.length > 0 && <span className={`chip ${blockers ? 'err' : 'warn'}`} style={{ marginLeft: 'auto', fontSize: 10 }}>{rows.length}</span>}
          </button>
          {showDiag && <div style={{ marginTop: 12 }}><DiagnosticsPanel config={config} /></div>}
        </div>
      </div>

      {/* Modals */}
      {modal?.type === 'section' && (
        <SectionJsonModal
          title={modal.title} data={modal.data} schemaMode={modal.schemaMode}
          onClose={() => setModal(null)}
          onSave={data => { updateSection(modal.key, data); setModal(null); }}
        />
      )}
      {modal?.type === 'item' && (
        <ItemModal
          kind={modal.kind}
          initial={modal.index != null ? (modal.kind === 'inbound' ? inbounds[modal.index] : outbounds[modal.index]) : undefined}
          onClose={() => setModal(null)}
          onSave={item => {
            const section = modal.kind === 'inbound' ? 'inbounds' : 'outbounds';
            if (modal.index != null) updateItem(section, modal.index, item);
            else addItem(section, item);
            setModal(null);
          }}
        />
      )}
      {modal?.type === 'generators' && <GeneratorsModal onClose={() => setModal(null)} />}
    </div>
  );
}

// ── small presentational helpers ───────────────────────────────
function CardHead({ title, icon, actions }: { title: string; icon: React.ReactNode; actions: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', borderBottom: '1px solid var(--line-soft)' }}>
      <p className="micro" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>{icon} {title}</p>
      <div style={{ display: 'flex', gap: 4 }}>{actions}</div>
    </div>
  );
}

function ListCard({ title, count, icon, onJson, onAdd, children }: {
  title: string; count: number; icon: React.ReactNode;
  onJson: () => void; onAdd: () => void; children: React.ReactNode;
}) {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', minHeight: 220, maxHeight: 360 }}>
      <CardHead title={`${title} (${count})`} icon={icon}
        actions={<>
          <button className="iconbtn" title="JSON" onClick={onJson}><Braces size={14} /></button>
          <button className="iconbtn accent" title="Добавить" onClick={onAdd}><Plus size={14} /></button>
        </>} />
      <div style={{ flex: 1, overflowY: 'auto', padding: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {count === 0
          ? <p style={{ fontSize: 12, color: 'var(--t-faint)', textAlign: 'center', padding: '20px 0' }}>Пусто</p>
          : children}
      </div>
    </div>
  );
}

function ItemRow({ tag, sub, onEdit, onDelete }: { tag: string; sub: string; onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="flex items-center gap-2" style={{ padding: '6px 8px', borderRadius: 'var(--r-sm)', background: 'var(--bg3)' }}>
      <div className="flex-1 min-w-0">
        <p className="text-sm trunc" style={{ color: 'var(--t-hi)', fontWeight: 500 }}>{tag}</p>
        <p className="text-xs num trunc" style={{ color: 'var(--t-low)' }}>{sub}</p>
      </div>
      <button className="iconbtn" onClick={onEdit} title="Редактировать"><Pencil size={13} /></button>
      <button className="iconbtn danger" onClick={onDelete} title="Удалить"><Trash2 size={13} /></button>
    </div>
  );
}
