import {
  ActionIcon, Alert, Badge, Button, ColorPicker, FileButton, Group, LoadingOverlay, Menu, Modal, NumberInput, Popover, Select, TextInput, Tooltip,
} from '@mantine/core';
import {
  IconAlertCircle, IconArrowBackUp, IconCheck, IconCloudUpload, IconKey,
  IconChevronDown, IconLock, IconRefresh, IconRocket, IconServerCog, IconSettings, IconShieldCheck, IconTrash,
} from '@tabler/icons-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { StateView } from '../components/StateView';
import { PageHeader } from '../components/PageHeader';
import { Surface } from '../components/Surface';
import { demoNodeBundles } from '../fixtures/demo';
import { api, APIError, isUnauthorized } from '../lib/api';
import type {
  AgentRelease, NodeAgentUpdateState, NodeOperational, NodeRecord, PanelSettings, PanelSettingsUpdate,
} from '../lib/contracts';
import {
  accentPattern, applyNodeFlowAccent, applyNodeFlowAppearance, applyNodeFlowTheme, defaultAccent, normaliseAccent,
  themeAccents,
} from '../lib/appearance';
import { formatBytes, timeAgo } from '../lib/format';
import { LoginPanel } from '../components/LoginPanel';
import { heartbeatPlatform, platformLabel, releaseMatchesPlatform, type AgentPlatform } from '../features/releases/platform';
import './settings-hardening.css';

interface SigningKeyInfo { algorithm?: string; sha256?: string; fingerprint?: string }
interface ManagedNode {
  node: NodeRecord;
  operational: NodeOperational | null;
  update: NodeAgentUpdateState | null;
}
interface SettingsData {
  panel: PanelSettings;
  nodes: ManagedNode[];
  releases: AgentRelease[];
  signingKey: SigningKeyInfo | null;
  partialErrors: string[];
}

interface RollbackIntent {
  nodeID: string;
  nodeName: string;
  release: AgentRelease;
  expectedActualSequence: number;
  expectedDesiredSequence: number;
  platform: AgentPlatform;
}

const maxReleaseBytes = 64 * 1024 * 1024;
const panelDefaults: PanelSettings = {
  public_url: 'https://panel.example.com', web_port: 8080, agent_port: 4200,
  theme: 'green', accent: defaultAccent, session_timeout_minutes: 30, max_sessions: 5,
  audit_retention_days: 90,
};
const accentPresets = ['#22C55E', '#C27087', '#45C7D8', '#E7B84B'];

function visiblePanelTheme(settings: Pick<PanelSettings, 'theme' | 'accent'>): PanelSettings['theme'] {
  if (['green', 'rose', 'cyan', 'amber'].includes(settings.theme)) return settings.theme;
  return normaliseAccent(settings.accent) === '#C27087' ? 'rose' : 'green';
}

function normalisePanelAppearance(settings: PanelSettings): PanelSettings {
  return { ...settings, theme: visiblePanelTheme(settings) };
}

const explicitDemo = new URLSearchParams(window.location.search).get('demo') === '1'
  || import.meta.env.VITE_NODEFLOW_DEMO === 'true';

function demoSettings(): SettingsData {
  const now = Date.now();
  const releases: AgentRelease[] = [
    { id: '00000000-0000-4000-8000-000000000005', version: '0.4.5-dev', os: 'linux', arch: 'amd64', sha256: '5d0d4c6b3f129ac6d0c84f1b1b84a7aee410b3ca43b30d138bda9b755593c4b0', size_bytes: 7_340_032, sequence: 5, signature: 'verified', created_at: new Date(now - 3_600_000).toISOString() },
    { id: '00000000-0000-4000-8000-000000000004', version: '0.4.4-dev', os: 'linux', arch: 'amd64', sha256: '8d19bd20112bc8b36a967f0b8f40ab7f17ef138a84c621034195899fbe82e34a', size_bytes: 7_286_784, sequence: 4, signature: 'verified', created_at: new Date(now - 86_400_000).toISOString() },
    { id: '00000000-0000-4000-8000-000000000003', version: '0.4.3-dev', os: 'linux', arch: 'amd64', sha256: 'e73e7d796675f0b024c01b1cd1a3b92459c5caa36c0c14b0a1019a0c67eca323', size_bytes: 7_233_536, sequence: 3, signature: 'verified', created_at: new Date(now - 2 * 86_400_000).toISOString() },
  ];
  const nodes = demoNodeBundles.map((bundle, index) => ({
    node: bundle.node,
    operational: bundle.operational,
    update: {
      node_id: bundle.node.id,
      actual_sequence: index === 0 ? 4 : 3,
      state: 'installed',
      last_report_at: new Date(now - 18_000 - index * 1_000).toISOString(),
      updated_at: new Date(now - 18_000).toISOString(),
    },
  }));
  return {
    panel: { ...panelDefaults, updated_at: new Date(now - 30_000).toISOString() },
    nodes, releases,
    signingKey: { algorithm: 'Ed25519', sha256: '703105fbac33f7388d7cd83e9cf02ad8130fa77d93b62bc111808d4cd5f3d387' },
    partialErrors: [],
  };
}

async function optional<T>(promise: Promise<T>, errors: string[], label: string, fallback: T): Promise<T> {
  try { return await promise; }
  catch (error) { errors.push(`${label}: ${error instanceof Error ? error.message : 'данные недоступны'}`); return fallback; }
}

async function loadSettings(): Promise<SettingsData> {
  if (explicitDemo) return demoSettings();
  const partialErrors: string[] = [];
  const [panel, nodes, releases, signingKey] = await Promise.all([
    api<PanelSettings>('/api/v1/settings'),
    api<NodeRecord[]>('/api/v1/nodes'),
    optional(api<AgentRelease[]>('/api/v1/agent-releases'), partialErrors, 'Релизы', []),
    optional(api<SigningKeyInfo>('/api/v1/agent-releases/signing-key'), partialErrors, 'Ключ подписи', null),
  ]);
  const managed = nodes.map((node) => ({ node, operational: null, update: null }));
  return { panel: normalisePanelAppearance(panel), nodes: managed, releases, signingKey, partialErrors };
}

function panelUpdate(settings: PanelSettings): PanelSettingsUpdate {
  return {
    theme: settings.theme,
    accent: settings.accent,
    session_timeout_minutes: settings.session_timeout_minutes,
    max_sessions: settings.max_sessions,
    audit_retention_days: settings.audit_retention_days,
  };
}

function samePanelUpdate(left: PanelSettings, right: PanelSettings) {
  return JSON.stringify(panelUpdate(left)) === JSON.stringify(panelUpdate(right));
}

function panelValidation(settings: PanelSettings): string[] {
  const errors: string[] = [];
  if (!accentPattern.test(settings.accent)) errors.push('Акцент: цвет в формате #RRGGBB.');
  if (!Number.isInteger(settings.session_timeout_minutes) || settings.session_timeout_minutes < 5 || settings.session_timeout_minutes > 1440) errors.push('Таймаут сессии: от 5 до 1440 минут.');
  if (!Number.isInteger(settings.max_sessions) || settings.max_sessions < 1 || settings.max_sessions > 100) errors.push('Количество сессий: от 1 до 100.');
  if (!Number.isInteger(settings.audit_retention_days) || settings.audit_retention_days < 7 || settings.audit_retention_days > 3650) errors.push('Хранение аудита: от 7 до 3650 дней.');
  return errors;
}

function releaseStatus(release: AgentRelease, actualSequence: number | null, platform: AgentPlatform | null) {
  if (!platform) return 'Платформа неизвестна';
  if (actualSequence === null) return 'Состояние неизвестно';
  if (!releaseMatchesPlatform(release, platform)) return 'Другая платформа';
  if (release.sequence === actualSequence) return 'Установлено';
  if (release.sequence > actualSequence) return 'Доступно';
  return 'Для отката';
}

function updaterStateLabel(state?: string) {
  return ({
    idle: 'Ожидание', pending: 'Назначено', downloading: 'Загрузка', verified: 'Проверено',
    activating: 'Установка', installed: 'Установлено', failed: 'Ошибка', rolled_back: 'Выполнен откат',
  } as Record<string, string>)[state ?? ''] ?? state ?? 'Нет отчёта';
}

export function SettingsPage() {
  const location = useLocation();
  const [data, setData] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [selectedNodeID, setSelectedNodeID] = useState('');
  const [detailsRefresh, setDetailsRefresh] = useState(0);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState('');
  const [busy, setBusy] = useState('');
  const [feedback, setFeedback] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [rollbackIntent, setRollbackIntent] = useState<RollbackIntent | null>(null);
  const [rollbackError, setRollbackError] = useState('');
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadVersion, setUploadVersion] = useState('');
  const [uploadOS, setUploadOS] = useState('linux');
  const [uploadArch, setUploadArch] = useState('amd64');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState('');
  const [releaseToDelete, setReleaseToDelete] = useState<AgentRelease | null>(null);
  const [deleteReleaseError, setDeleteReleaseError] = useState('');
  const [deleteReleaseBlocked, setDeleteReleaseBlocked] = useState(false);
  const [panelDraft, setPanelDraft] = useState<PanelSettings>(panelDefaults);
  const [savedPanel, setSavedPanel] = useState<PanelSettings>(panelDefaults);
  const [panelSaving, setPanelSaving] = useState(false);
  const [panelError, setPanelError] = useState('');
  const [accentPickerOpen, setAccentPickerOpen] = useState(false);
  const resetUploadInput = useRef<() => void>(null);
  const demoQuery = explicitDemo ? '?demo=1' : location.search;

  const load = async () => {
    setLoading(true); setError(null); setFeedback(null);
    try {
      const value = await loadSettings();
      setData(value);
      setPanelDraft(value.panel);
      setSavedPanel(value.panel);
      applyNodeFlowAppearance(value.panel, true);
      setPanelError('');
      setSelectedNodeID((current) => current && value.nodes.some(({ node }) => node.id === current) ? current : value.nodes[0]?.node.id ?? '');
      setDetailsRefresh((current) => current + 1);
    } catch (reason) { setError(reason); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (explicitDemo || !selectedNodeID) {
      setDetailsLoading(false);
      setDetailsError('');
      return undefined;
    }
    const controller = new AbortController();
    const errors: string[] = [];
    setDetailsLoading(true);
    setDetailsError('');
    void Promise.all([
      optional(api<NodeOperational>(`/api/v1/nodes/${selectedNodeID}/operational`, { signal: controller.signal }), errors, 'Телеметрия', null),
      optional(api<NodeAgentUpdateState>(`/api/v1/nodes/${selectedNodeID}/agent-update`, { signal: controller.signal }), errors, 'Updater', null),
    ]).then(([operational, update]) => {
      if (controller.signal.aborted) return;
      setData((current) => current ? ({
        ...current,
        nodes: current.nodes.map((item) => item.node.id === selectedNodeID ? { ...item, operational, update } : item),
      }) : current);
      setDetailsError(errors.join(' · '));
    }).finally(() => {
      if (!controller.signal.aborted) setDetailsLoading(false);
    });
    return () => controller.abort();
  }, [selectedNodeID, detailsRefresh]);

  const selected = data?.nodes.find(({ node }) => node.id === selectedNodeID) ?? data?.nodes[0];
  const releases = useMemo(() => [...(data?.releases ?? [])].sort((a, b) => b.sequence - a.sequence), [data?.releases]);
  const actualSequence = selected?.update ? selected.update.actual_sequence : null;
  const nodePlatform = heartbeatPlatform(selected?.operational?.latest_heartbeat?.metrics);
  const compatibleReleases = releases.filter((release) => releaseMatchesPlatform(release, nodePlatform));
  const currentRelease = actualSequence === null ? undefined : compatibleReleases.find((release) => release.sequence === actualSequence);
  const available = actualSequence === null ? undefined : compatibleReleases.find((release) => release.sequence > actualSequence);
  const rollbackReleases = actualSequence === null ? [] : compatibleReleases.filter((release) => release.sequence < actualSequence);
  const installedVersion = selected?.operational?.latest_heartbeat?.agent_version ?? currentRelease?.version ?? '—';
  const connectedNodes = data?.nodes.filter(({ node, operational }) => {
    const signalAt = operational?.latest_heartbeat?.received_at ?? node.last_seen_at;
    const age = signalAt ? Date.now() - new Date(signalAt).getTime() : Number.POSITIVE_INFINITY;
    return node.status === 'online' && age >= 0 && age < 45_000;
  }).length ?? 0;
  const nodeCount = data?.nodes.length ?? 0;
  const validationErrors = panelValidation(panelDraft);
  const panelDirty = !samePanelUpdate(panelDraft, savedPanel);
  const setPanelField = <K extends keyof PanelSettings>(field: K, value: PanelSettings[K]) => {
    const normalised = field === 'accent' ? normaliseAccent(String(value)) : null;
    const nextValue = field === 'accent' && normalised ? normalised : value;
    setPanelDraft((current) => ({ ...current, [field]: nextValue }));
    if (field === 'accent') applyNodeFlowAccent(String(nextValue));
    if (field === 'theme') applyNodeFlowTheme(nextValue as PanelSettings['theme']);
    setFeedback(null);
    setPanelError('');
  };
  const setPanelTheme = (theme: PanelSettings['theme']) => {
    const accent = themeAccents[theme] ?? panelDraft.accent;
    setPanelDraft((current) => ({ ...current, theme, accent }));
    applyNodeFlowAppearance({ theme, accent });
    setFeedback(null);
    setPanelError('');
  };
  const setCustomAccent = (value: string) => {
    const accent = normaliseAccent(value) ?? value;
    const theme = visiblePanelTheme(panelDraft);
    setPanelDraft((current) => ({ ...current, theme, accent }));
    if (normaliseAccent(accent)) applyNodeFlowAppearance({ theme, accent });
    setFeedback(null);
    setPanelError('');
  };
  const resetPanelSettings = () => {
    setPanelDraft(savedPanel);
    applyNodeFlowAppearance(savedPanel, true);
    setPanelError('');
  };
  const savePanelSettings = async () => {
    if (validationErrors.length || panelSaving) return;
    setPanelSaving(true);
    setPanelError('');
    setFeedback(null);
    try {
      const value = explicitDemo
        ? { ...panelDraft, updated_at: new Date().toISOString() }
        : await api<PanelSettings>('/api/v1/settings', { method: 'PUT', body: JSON.stringify(panelUpdate(panelDraft)) });
      setPanelDraft(value);
      setSavedPanel(value);
      setData((current) => current ? { ...current, panel: value } : current);
      applyNodeFlowAppearance(value, true);
      setFeedback({ tone: 'ok', text: explicitDemo ? 'Demo-настройки сохранены в текущем интерфейсе. Live Panel не изменён.' : 'Настройки панели сохранены.' });
    } catch (reason) {
      setPanelError(reason instanceof APIError && reason.status === 409
        ? 'Конфликт: настройки уже изменены в другой сессии. Обновите состояние перед повторным сохранением.'
        : reason instanceof Error ? reason.message : 'Настройки не сохранены.');
    } finally {
      setPanelSaving(false);
    }
  };

  const updateNodeState = (value: NodeAgentUpdateState) => setData((current) => current ? ({
    ...current,
    nodes: current.nodes.map((item) => item.node.id === value.node_id ? { ...item, update: value } : item),
  }) : current);

  const assign = async () => {
    if (!selected || !selected.update || !available || !nodePlatform || !releaseMatchesPlatform(available, nodePlatform)) return;
    setBusy('update'); setFeedback(null);
    try {
      const value = explicitDemo
        ? { ...selected.update!, desired_release: available, state: 'assigned', updated_at: new Date().toISOString() }
        : await api<NodeAgentUpdateState>(`/api/v1/nodes/${selected.node.id}/agent-update`, {
          method: 'PUT',
          body: JSON.stringify({
            release_id: available.id,
            expected_actual_sequence: selected.update.actual_sequence,
            expected_desired_sequence: selected.update.desired_release?.sequence ?? 0,
          }),
        });
      updateNodeState(value); setFeedback({ tone: 'ok', text: `${available.version} назначен ноде ${selected.node.name}. Updater проверит подпись и выполнит авт rollback при ошибке.` });
    } catch (reason) { setFeedback({ tone: 'error', text: reason instanceof Error ? reason.message : 'Обновление не назначено' }); }
    finally { setBusy(''); }
  };

  const rollbackNode = async () => {
    if (!rollbackIntent) return;
    const target = data?.nodes.find(({ node }) => node.id === rollbackIntent.nodeID);
    const currentPlatform = heartbeatPlatform(target?.operational?.latest_heartbeat?.metrics);
    if (!currentPlatform || !releaseMatchesPlatform(rollbackIntent.release, currentPlatform)
      || currentPlatform.os !== rollbackIntent.platform.os || currentPlatform.arch !== rollbackIntent.platform.arch) {
      setRollbackError('Платформа ноды неизвестна или изменилась. Обновите состояние и выберите совместимый релиз заново.');
      return;
    }
    setBusy('rollback'); setFeedback(null);
    setRollbackError('');
    try {
      const selectedState = data?.nodes.find(({ node }) => node.id === rollbackIntent.nodeID)?.update;
      const value = explicitDemo
        ? { ...selectedState!, desired_release: rollbackIntent.release, state: 'assigned', updated_at: new Date().toISOString() }
        : await api<NodeAgentUpdateState>(`/api/v1/nodes/${rollbackIntent.nodeID}/agent-update/rollback`, {
          method: 'POST',
          body: JSON.stringify({
            target_release_id: rollbackIntent.release.id,
            expected_actual_sequence: rollbackIntent.expectedActualSequence,
            expected_desired_sequence: rollbackIntent.expectedDesiredSequence,
          }),
        });
      updateNodeState(value);
      setFeedback({ tone: 'ok', text: `Безопасный откат ${rollbackIntent.nodeName} на ${rollbackIntent.release.version} назначен.` });
      setRollbackIntent(null);
    } catch (reason) { setRollbackError(reason instanceof Error ? reason.message : 'Откат не назначен'); }
    finally { setBusy(''); }
  };

  const requestRollback = (release: AgentRelease) => {
    if (!selected || !selected.update || !nodePlatform || !releaseMatchesPlatform(release, nodePlatform) || release.sequence >= selected.update.actual_sequence) return;
    setRollbackError('');
    setRollbackIntent({
      nodeID: selected.node.id,
      nodeName: selected.node.name,
      release,
      expectedActualSequence: selected.update.actual_sequence,
      expectedDesiredSequence: selected.update.desired_release?.sequence ?? 0,
      platform: nodePlatform,
    });
  };

  const resetUpload = () => {
    resetUploadInput.current?.();
    setUploadOpen(false);
    setUploadVersion('');
    setUploadOS('linux');
    setUploadArch('amd64');
    setUploadFile(null);
    setUploadError('');
  };

  const selectUploadFile = (file: File | null) => {
    if (file && file.size > maxReleaseBytes) {
      resetUploadInput.current?.();
      setUploadFile(null);
      setUploadError(`Файл больше 64 MiB: ${formatBytes(file.size)}.`);
      return;
    }
    setUploadFile(file);
    setUploadError('');
  };

  const upload = async () => {
    if (!uploadFile || !uploadVersion.trim()) return;
    if (uploadFile.size > maxReleaseBytes) {
      setUploadError(`Файл больше 64 MiB: ${formatBytes(uploadFile.size)}.`);
      return;
    }
    setBusy('upload'); setFeedback(null);
    setUploadError('');
    try {
      const value = explicitDemo ? {
        id: crypto.randomUUID(), version: uploadVersion.trim(), os: uploadOS, arch: uploadArch,
        sha256: 'demo-artifact-sha256', size_bytes: uploadFile.size, sequence: (releases[0]?.sequence ?? 0) + 1,
        signature: 'demo-signed', created_at: new Date().toISOString(),
      } satisfies AgentRelease : await api<AgentRelease>(`/api/v1/agent-releases?version=${encodeURIComponent(uploadVersion.trim())}&os=${encodeURIComponent(uploadOS)}&arch=${encodeURIComponent(uploadArch)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: uploadFile,
      });
      setData((current) => current ? { ...current, releases: [value, ...current.releases] } : current);
      setFeedback({ tone: 'ok', text: `Релиз ${value.version} загружен, SHA-256 проверен и подписан Ed25519.` });
      resetUpload();
    } catch (reason) { setUploadError(reason instanceof Error ? reason.message : 'Релиз не загружен'); }
    finally { setBusy(''); }
  };

  const deleteRelease = async () => {
    if (!releaseToDelete || deleteReleaseBlocked) return;
    setBusy('delete-release');
    setDeleteReleaseError('');
    setFeedback(null);
    try {
      if (!explicitDemo) await api<void>(`/api/v1/agent-releases/${encodeURIComponent(releaseToDelete.id)}`, { method: 'DELETE' });
      setData((current) => current ? { ...current, releases: current.releases.filter((release) => release.id !== releaseToDelete.id) } : current);
      setFeedback({ tone: 'ok', text: `Релиз ${releaseToDelete.version} · ${releaseToDelete.os}/${releaseToDelete.arch} удалён.` });
      setReleaseToDelete(null);
    } catch (reason) {
      const releaseInUse = reason instanceof APIError && reason.status === 409 && reason.code === 'release_in_use';
      setDeleteReleaseBlocked(releaseInUse);
      setDeleteReleaseError(reason instanceof APIError && reason.status === 409
        ? 'Релиз установлен или назначен хотя бы одной ноде. Сначала переведите эти ноды на другую версию.'
        : reason instanceof Error ? reason.message : 'Релиз не удалён.');
    } finally { setBusy(''); }
  };

  if (loading) return <main className="nf-page nf-settings-page"><div className="nf-settings-loading"><LoadingOverlay visible /><span>Загружаем параметры панели и подписанные релизы…</span></div></main>;
  if (error && isUnauthorized(error)) return <LoginPanel onSuccess={load} />;
  if (error || !data) return <main className="nf-page"><StateView title="Настройки не загрузились" description={error instanceof Error ? error.message : 'Panel API недоступен'} tone="error" action={<Button onClick={load}>Повторить</Button>} /></main>;

  return (
    <main className="nf-page nf-settings-page">
      <PageHeader
        className="nf-settings-header"
        icon={<IconSettings size={21} />}
        title="Настройки"
        description="Панель, безопасность, сессии и подписанные обновления Node Agent."
        badge={explicitDemo ? <span className="nf-demo-badge">Демо-данные</span> : undefined}
        actions={<Button variant="default" leftSection={<IconRefresh size={17} />} onClick={load}>Обновить состояние</Button>}
      />

      {data.partialErrors.length > 0 && <Alert className="nf-settings-partial" color="yellow" icon={<IconAlertCircle size={18} />} title="Часть данных недоступна">{data.partialErrors.slice(0, 3).join(' · ')}</Alert>}
      {feedback && <Alert color={feedback.tone === 'ok' ? 'nodeflow' : 'red'} icon={feedback.tone === 'ok' ? <IconCheck size={18} /> : <IconAlertCircle size={18} />} withCloseButton onClose={() => setFeedback(null)} role="status">{feedback.text}</Alert>}

      <div className="nf-settings-grid">
        <Surface className="nf-settings-card" title={<span className="nf-settings-card-title"><IconServerCog size={19} />Панель</span>}>
          <form className="nf-settings-form" onSubmit={(event) => { event.preventDefault(); void savePanelSettings(); }}>
            <div className="nf-settings-fields">
              <TextInput label="URL панели" description="Runtime · только чтение" value={panelDraft.public_url} readOnly leftSection={<IconLock size={14} />} />
              <div className="nf-settings-runtime-ports">
                <NumberInput label="Web/API-порт" description="Runtime" value={panelDraft.web_port} readOnly hideControls leftSection={<IconLock size={14} />} />
                <NumberInput label="Порт канала Agent" description="Runtime" value={panelDraft.agent_port} readOnly hideControls leftSection={<IconLock size={14} />} />
              </div>
              <Select
                label="Тема панели"
                value={panelDraft.theme}
                onChange={(value) => setPanelTheme((value ?? 'green') as PanelSettings['theme'])}
                allowDeselect={false}
                disabled={panelSaving}
                data={[
                  { value: 'green', label: 'Green' },
                  { value: 'rose', label: 'Rose' },
                  { value: 'cyan', label: 'Arctic Cyan' },
                  { value: 'amber', label: 'Warm Amber' },
                ]}
              />
              <div className="nf-accent-field">
                <span className="mantine-InputWrapper-label">Цветовой акцент</span>
                <span className="mantine-InputWrapper-description">Меняется во время выбора; сохраняется после подтверждения</span>
                <Popover opened={accentPickerOpen} onChange={setAccentPickerOpen} position="bottom-start" width={280} shadow="md" withinPortal>
                  <Popover.Target>
                    <button className="nf-accent-trigger" type="button" onClick={() => setAccentPickerOpen((current) => !current)} disabled={panelSaving} aria-label={`Выбрать цветовой акцент, сейчас ${panelDraft.accent}`} aria-expanded={accentPickerOpen}>
                      <i style={{ background: normaliseAccent(panelDraft.accent) ?? defaultAccent }} />
                      <span>{panelDraft.accent || '#RRGGBB'}</span>
                      <small>Открыть палитру</small>
                    </button>
                  </Popover.Target>
                  <Popover.Dropdown className="nf-accent-popover">
                    <ColorPicker
                      format="hex"
                      value={normaliseAccent(panelDraft.accent) ?? defaultAccent}
                      onChange={(value) => setCustomAccent(value.toUpperCase())}
                      fullWidth
                    />
                    <div className="nf-accent-presets" aria-label="Готовые акценты">
                      {accentPresets.map((color) => <button key={color} type="button" style={{ background: color }} onClick={() => setCustomAccent(color)} aria-label={`Акцент ${color}`} />)}
                    </div>
                    <TextInput
                      label="HEX"
                      value={panelDraft.accent}
                      onChange={(event) => setCustomAccent(event.currentTarget.value.toUpperCase())}
                      error={panelDraft.accent && !accentPattern.test(panelDraft.accent) ? 'Формат #RRGGBB' : undefined}
                      placeholder="#22C55E"
                      maxLength={7}
                    />
                  </Popover.Dropdown>
                </Popover>
              </div>
            </div>
            <div className="nf-settings-readonly-note nf-panel-facts-note"><IconLock size={16} /><span>URL и порты берутся из runtime-конфигурации сервера и не меняются из браузера.</span></div>
            <div className="nf-settings-subsection">
              <h3>Сессии</h3>
              <div className="nf-settings-control-grid">
                <NumberInput label="Таймаут неактивности" description="минут · 5–1440" value={panelDraft.session_timeout_minutes} onChange={(value) => setPanelField('session_timeout_minutes', Number(value))} min={5} max={1440} allowDecimal={false} disabled={panelSaving} />
                <NumberInput label="Макс. активных сессий" description="глобально для панели · 1–100" value={panelDraft.max_sessions} onChange={(value) => setPanelField('max_sessions', Number(value))} min={1} max={100} allowDecimal={false} disabled={panelSaving} />
              </div>
            </div>
            {panelError && <Alert className="nf-settings-form-error" color="red" icon={<IconAlertCircle size={17} />} role="alert">{panelError}</Alert>}
            {validationErrors.length > 0 && <Alert className="nf-settings-form-error" color="yellow" icon={<IconAlertCircle size={17} />} role="alert">{validationErrors.join(' ')}</Alert>}
            <div className="nf-settings-form-actions">
              <span>{explicitDemo ? 'Демо · без записи в Panel API' : panelDirty ? 'Есть несохранённые изменения' : savedPanel.updated_at ? `Сохранено ${timeAgo(savedPanel.updated_at)}` : 'Настройки синхронизированы'}</span>
              <Group gap={7} wrap="nowrap">
                <Button type="button" size="xs" variant="default" onClick={resetPanelSettings} disabled={!panelDirty || panelSaving}>Сбросить</Button>
                <Button type="submit" size="xs" leftSection={<IconCheck size={15} />} loading={panelSaving} disabled={!panelDirty || validationErrors.length > 0}>Сохранить</Button>
              </Group>
            </div>
          </form>
        </Surface>

        <Surface className="nf-settings-card" title={<span className="nf-settings-card-title"><IconShieldCheck size={19} />Безопасность</span>}>
          <div className={`nf-security-status ${connectedNodes === 0 ? 'is-idle' : connectedNodes < nodeCount ? 'is-partial' : ''}`}>
            <IconShieldCheck size={24} />
            <div><strong>mTLS-канал нод</strong><span>{nodeCount ? `Свежий сигнал: ${connectedNodes} из ${nodeCount}. Agent подключается к TCP ${panelDraft.agent_port}.` : 'Добавьте ноду, чтобы проверить защищённый канал.'}</span></div>
            <Badge color={connectedNodes === 0 ? 'gray' : connectedNodes < nodeCount ? 'yellow' : 'nodeflow'} variant="light">{nodeCount ? `${connectedNodes} / ${nodeCount} на связи` : 'Нод нет'}</Badge>
          </div>
          <div className="nf-settings-subsection">
            <h3>Аудит и журналы</h3>
            <NumberInput label="Хранить события" description="дней · 7–3650" value={panelDraft.audit_retention_days} onChange={(value) => setPanelField('audit_retention_days', Number(value))} min={7} max={3650} allowDecimal={false} disabled={panelSaving} />
            <div className="nf-settings-audit-actions"><span>{panelDirty ? 'Политика изменена' : 'Политика синхронизирована'}</span><Button size="xs" variant="default" leftSection={<IconCheck size={14} />} onClick={() => void savePanelSettings()} loading={panelSaving} disabled={!panelDirty || validationErrors.length > 0}>Сохранить</Button></div>
          </div>
        </Surface>

        <Surface id="node-agent" className="nf-settings-card nf-agent-settings" title={<span className="nf-settings-card-title"><IconRocket size={19} />Node Agent <small>подписанные релизы</small></span>} actions={<Button size="xs" variant="default" leftSection={<IconCloudUpload size={15} />} onClick={() => setUploadOpen(true)}>Загрузить</Button>}>
          {data.nodes.length ? <div className="nf-agent-node-controls">
            <div className="nf-agent-scope">
              <Select label="Нода для обновления" value={selected?.node.id} onChange={(value) => setSelectedNodeID(value ?? '')} allowDeselect={false} data={data.nodes.map(({ node }) => ({ value: node.id, label: `${node.name} · ${node.address}` }))} disabled={Boolean(busy)} />
              <div className="nf-agent-trust" aria-label="Проверки релиза">
                <span><IconShieldCheck size={15} />{data.signingKey?.algorithm || 'Ed25519'} · SHA-256</span>
                <Badge size="xs" variant="light" color={selected?.update?.state === 'installed' ? 'nodeflow' : selected?.update?.state === 'failed' ? 'red' : 'gray'}>{updaterStateLabel(selected?.update?.state)}</Badge>
              </div>
            </div>
            <div className="nf-agent-release-summary">
              <div><span>Установлено</span><strong>{detailsLoading ? 'Загрузка…' : installedVersion}</strong><small>{selected?.update?.last_report_at ? `Отчёт ${timeAgo(selected.update.last_report_at)} · sequence #${selected.update.actual_sequence}` : detailsLoading ? 'Получаем состояние ноды' : 'Отчёт updater ещё не получен'}</small></div>
              <div><span>Доступно</span><strong>{!nodePlatform ? 'Платформа неизвестна' : actualSequence === null ? 'Состояние updater неизвестно' : available?.version ?? 'Актуальная версия'}</strong><small>{available ? `sequence #${available.sequence} · ${formatBytes(available.size_bytes)}` : !nodePlatform ? 'Сигнал ещё не передал os / arch' : actualSequence === null ? 'Нельзя сравнить релизы без отчёта updater' : `Совместимый релиз новее не найден · ${platformLabel(nodePlatform)}`}</small></div>
            </div>
            {detailsError && <Alert color="yellow" icon={<IconAlertCircle size={17} />} role="alert">{detailsError}</Alert>}
            {!detailsLoading && !nodePlatform && <Alert color="yellow" icon={<IconAlertCircle size={17} />} role="status" title="Назначение релиза заблокировано">Дождитесь сигнала с полями os и arch. NodeFlow не будет предполагать платформу ноды.</Alert>}
            {!detailsLoading && nodePlatform && !selected?.update && <Alert color="yellow" icon={<IconAlertCircle size={17} />} role="status" title="Состояние updater неизвестно">Назначение и откат заблокированы, пока Panel не получит фактический sequence ноды.</Alert>}
            {selected?.update?.last_error && <Alert color="red" icon={<IconAlertCircle size={17} />}>{selected.update.last_error}</Alert>}
            <div className="nf-agent-release-actions">
              <Button leftSection={<IconRocket size={16} />} onClick={assign} disabled={!nodePlatform || !available || !selected?.update || detailsLoading || Boolean(busy)} loading={busy === 'update'}>{!nodePlatform ? 'Платформа неизвестна' : !selected?.update ? 'Нет отчёта updater' : available ? `Обновить до ${available.version}` : 'Совместимых обновлений нет'}</Button>
              {rollbackReleases.length > 1 ? <Menu position="bottom-end" withinPortal shadow="md">
                <Menu.Target>
                  <Button variant="default" leftSection={<IconArrowBackUp size={16} />} rightSection={<IconChevronDown size={15} />} disabled={!nodePlatform || !selected?.update || detailsLoading || Boolean(busy)}>Откатить</Button>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>Выберите версию</Menu.Label>
                  {rollbackReleases.map((release) => <Menu.Item key={release.id} leftSection={<IconArrowBackUp size={15} />} onClick={() => requestRollback(release)}>{release.version} · sequence #{release.sequence}</Menu.Item>)}
                </Menu.Dropdown>
              </Menu> : <Button variant="default" leftSection={<IconArrowBackUp size={16} />} onClick={() => { const release = rollbackReleases[0]; if (release) requestRollback(release); }} disabled={!nodePlatform || rollbackReleases.length === 0 || !selected?.update || detailsLoading || Boolean(busy)}>{!nodePlatform ? 'Откат недоступен' : !selected?.update ? 'Нет отчёта updater' : rollbackReleases[0] ? `Откатить до ${rollbackReleases[0].version} · #${rollbackReleases[0].sequence}` : 'Нет совместимой версии'}</Button>}
            </div>
            <p className="nf-agent-rollout-note">HAProxy не перезапускается. Если новый Agent не пройдёт проверку запуска, updater атомарно вернёт предыдущий бинарник.</p>
          </div> : <StateView title="Нод пока нет" description="Релизы можно подготовить заранее; назначение обновлений станет доступно после установки ноды." action={<Button component={Link} to={`/nodes${demoQuery}`}>Добавить ноду</Button>} />}
          <section className="nf-agent-history nf-release-history" aria-labelledby="nf-agent-history-title">
            <header>
              <div><h3 id="nf-agent-history-title">Версии Agent</h3><p>Установка ноды автоматически выбирает новейший релиз для её платформы.</p></div>
              <Badge size="sm" variant="outline" color="nodeflow">{releases.length}</Badge>
            </header>
            {releases.length ? <div className="nf-agent-history__list" role="table" aria-label="Подписанные версии Agent">
              {releases.map((release) => {
                const status = data.nodes.length ? releaseStatus(release, actualSequence, nodePlatform) : 'Готов к установке';
                return <div className="nf-agent-history__row" role="row" key={release.id}>
                  <div className="nf-agent-history__version" role="cell"><strong>{release.version}</strong><span>sequence #{release.sequence}</span></div>
                  <div className="nf-agent-history__meta" role="cell"><span>{release.os} / {release.arch} · {formatBytes(release.size_bytes)}</span><small>{new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(release.created_at))} · <Tooltip label={release.sha256}><code>{release.sha256.slice(0, 10)}…</code></Tooltip></small></div>
                  <div className="nf-agent-history__actions" role="cell">
                    <Badge size="xs" variant={status === 'Установлено' ? 'light' : 'outline'} color={status === 'Установлено' || status === 'Доступно' ? 'nodeflow' : 'gray'}>{status}</Badge>
                    <Tooltip label={`Удалить ${release.version}`}><ActionIcon variant="subtle" color="red" size="sm" aria-label={`Удалить релиз ${release.version} для ${release.os}/${release.arch}`} onClick={() => { setDeleteReleaseError(''); setDeleteReleaseBlocked(false); setReleaseToDelete(release); }} disabled={Boolean(busy)}><IconTrash size={15} /></ActionIcon></Tooltip>
                  </div>
                </div>;
              })}
            </div> : <StateView title="Релизов пока нет" description="Без совместимого релиза установка ноды будет заблокирована. Загрузите бинарник Linux для нужной архитектуры." action={<Button leftSection={<IconCloudUpload size={16} />} onClick={() => setUploadOpen(true)}>Загрузить релиз</Button>} />}
          </section>
        </Surface>
      </div>

      <Modal opened={Boolean(rollbackIntent)} onClose={() => busy !== 'rollback' && setRollbackIntent(null)} title="Подтвердить откат Node Agent" size="sm" closeOnClickOutside={busy !== 'rollback'} closeOnEscape={busy !== 'rollback'} classNames={{ content: 'nf-dialog', header: 'nf-dialog__header', body: 'nf-dialog__body' }}>
        <div className="nf-confirm-dialog">
          <p>Нода <strong>{rollbackIntent?.nodeName}</strong> · <strong>{platformLabel(rollbackIntent?.platform ?? null)}</strong> перейдёт с sequence <strong>#{rollbackIntent?.expectedActualSequence}</strong> на подписанный релиз <strong>{rollbackIntent?.release.version} · #{rollbackIntent?.release.sequence}</strong>. Updater проверит Ed25519 и SHA-256 перед активацией.</p>
          {rollbackError && <div className="nf-inline-error" role="alert">{rollbackError}</div>}
          <div><Button variant="default" onClick={() => setRollbackIntent(null)} disabled={busy === 'rollback'}>Отмена</Button><Button leftSection={<IconArrowBackUp size={16} />} onClick={rollbackNode} loading={busy === 'rollback'}>Назначить откат</Button></div>
        </div>
      </Modal>

      <Modal opened={Boolean(releaseToDelete)} onClose={() => { if (busy !== 'delete-release') { setReleaseToDelete(null); setDeleteReleaseBlocked(false); } }} title="Удалить версию Agent?" size="sm" closeOnClickOutside={busy !== 'delete-release'} closeOnEscape={busy !== 'delete-release'} classNames={{ content: 'nf-dialog', header: 'nf-dialog__header', body: 'nf-dialog__body' }}>
        <div className="nf-confirm-dialog">
          <p>Релиз <strong>{releaseToDelete?.version}</strong> · <strong>{releaseToDelete?.os}/{releaseToDelete?.arch}</strong> · sequence <strong>#{releaseToDelete?.sequence}</strong> будет удалён вместе с бинарником. Номер sequence повторно не используется.</p>
          <Alert color="yellow" icon={<IconAlertCircle size={17} />}>Если релиз установлен или назначен хотя бы одной ноде, Panel отклонит удаление и сохранит артефакт.</Alert>
          {deleteReleaseError && <div className="nf-inline-error" role="alert">{deleteReleaseError}</div>}
          <div><Button variant="default" onClick={() => { setReleaseToDelete(null); setDeleteReleaseBlocked(false); }} disabled={busy === 'delete-release'}>Отмена</Button><Button color="red" leftSection={<IconTrash size={16} />} onClick={deleteRelease} loading={busy === 'delete-release'} disabled={deleteReleaseBlocked}>{deleteReleaseBlocked ? 'Удаление недоступно' : 'Удалить версию'}</Button></div>
        </div>
      </Modal>

      <Modal opened={uploadOpen} onClose={() => busy !== 'upload' && resetUpload()} title="Загрузить подписанный релиз" size="md" closeOnClickOutside={busy !== 'upload'} closeOnEscape={busy !== 'upload'} classNames={{ content: 'nf-dialog', header: 'nf-dialog__header', body: 'nf-dialog__body' }}>
        <div className="nf-release-upload">
          <Alert color="nodeflow" icon={<IconKey size={18} />}>Panel подпишет бинарник серверным Ed25519-ключом. Приватный ключ никогда не передаётся в браузер.</Alert>
          <div className="nf-field-grid nf-field-grid--3">
            <TextInput label="Версия" placeholder="0.4.6-dev" value={uploadVersion} onChange={(event) => setUploadVersion(event.currentTarget.value)} required />
            <Select label="ОС" value={uploadOS} onChange={(value) => setUploadOS(value ?? 'linux')} allowDeselect={false} data={['linux']} />
            <Select label="Архитектура" value={uploadArch} onChange={(value) => setUploadArch(value ?? 'amd64')} allowDeselect={false} data={['amd64', 'arm64']} />
          </div>
          <FileButton onChange={selectUploadFile} accept="application/octet-stream" resetRef={resetUploadInput}>
            {(props) => <Button {...props} variant="default" leftSection={<IconCloudUpload size={17} />}>{uploadFile ? `${uploadFile.name} · ${formatBytes(uploadFile.size)}` : 'Выбрать бинарник до 64 MiB'}</Button>}
          </FileButton>
          {uploadError && <Alert color="red" icon={<IconAlertCircle size={17} />} role="alert">{uploadError}</Alert>}
          <Group justify="flex-end"><Button variant="default" onClick={resetUpload} disabled={busy === 'upload'}>Отмена</Button><Button onClick={upload} loading={busy === 'upload'} disabled={!uploadFile || !uploadVersion.trim() || Boolean(uploadError)}>Загрузить и подписать</Button></Group>
        </div>
      </Modal>
    </main>
  );
}
