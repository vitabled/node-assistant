import {
  Alert, Button, Collapse, LoadingOverlay, Modal, NumberInput, SegmentedControl, Select,
  Switch, TagsInput, Textarea, TextInput, Tooltip,
} from '@mantine/core';
import {
  IconAlertCircle, IconArrowLeft, IconCheck, IconChevronDown, IconChevronUp, IconCode,
  IconInfoCircle, IconRoute, IconShieldCheck,
} from '@tabler/icons-react';
import { useBeforeUnload } from 'react-router-dom';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useBlocker, useLocation, useNavigate, useParams } from 'react-router-dom';
import { LoginPanel } from '../components/LoginPanel';
import { PageHeader } from '../components/PageHeader';
import { StateView } from '../components/StateView';
import { Surface } from '../components/Surface';
import { demoNodeBundles, demoRoutesForNode, upsertDemoRoute } from '../fixtures/demo';
import {
  emptyRouteDraft, quotaPeriodOptions, renderRoutePreview, routePayload, routeToDraft,
  validateRouteDraft, type ProxyProtocol, type QuotaAction, type QuotaPeriod, type RouteDraft,
  type RouteDraftError, type RouteMatchMode, type RouteTargetMode,
} from '../features/routes/model';
import { api, isUnauthorized } from '../lib/api';
import type { NodeRecord, RouteRecord } from '../lib/contracts';

interface EditorData { node: NodeRecord; routes: RouteRecord[]; route?: RouteRecord }
type SaveIntent = 'draft' | 'enable' | 'apply';

const explicitDemo = new URLSearchParams(window.location.search).get('demo') === '1'
  || import.meta.env.VITE_NODEFLOW_DEMO === 'true';

function errorText(errors: RouteDraftError[], fields: RouteDraftError['field'][]): string | undefined {
  return errors.find((error) => fields.includes(error.field))?.message;
}

function statusCopy(intent: SaveIntent, editing: boolean) {
  if (intent === 'draft') return editing ? 'Черновик сохранён' : 'Маршрут создан как выключенный черновик';
  if (intent === 'apply') return 'Изменения отправлены на ноду';
  return 'Маршрут создан и отправлен на включение';
}

async function loadEditor(nodeID: string, routeID?: string): Promise<EditorData> {
  if (explicitDemo) {
    const bundle = structuredClone(demoNodeBundles.find(({ node }) => node.id === nodeID) ?? demoNodeBundles[0]);
    bundle.node.id = nodeID;
    bundle.routes = [
      ...bundle.routes.map((route) => ({ ...route, node_id: nodeID })),
      ...demoRoutesForNode(nodeID),
    ];
    const route = routeID ? bundle.routes.find((item) => item.id === routeID) : undefined;
    return { node: bundle.node, routes: bundle.routes, route };
  }
  const [node, routes] = await Promise.all([
    api<NodeRecord>(`/api/v1/nodes/${nodeID}`),
    api<RouteRecord[]>(`/api/v1/nodes/${nodeID}/routes`),
  ]);
  const route = routeID
    ? routes.find((item) => item.id === routeID) ?? await api<RouteRecord>(`/api/v1/nodes/${nodeID}/routes/${routeID}`)
    : undefined;
  return { node, routes, route };
}

export function RouteEditorPage() {
  const { nodeId = '', routeId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const formRef = useRef<HTMLFormElement>(null);
  const bypassNavigationRef = useRef(false);
  const anchoredRouteIDRef = useRef<string | null>(null);
  const [data, setData] = useState<EditorData | null>(null);
  const [draft, setDraft] = useState<RouteDraft>(emptyRouteDraft);
  const [initialSignature, setInitialSignature] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [saving, setSaving] = useState<SaveIntent | null>(null);
  const [validationIntent, setValidationIntent] = useState<SaveIntent>('enable');
  const [saveError, setSaveError] = useState('');
  const [savedMessage, setSavedMessage] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [additionalOpen, setAdditionalOpen] = useState(false);
  const [expertEnabled, setExpertEnabled] = useState(false);

  const load = async () => {
    setLoading(true); setLoadError(null); setSaveError('');
    try {
      const value = await loadEditor(nodeId, routeId);
      const nextDraft = value.route ? routeToDraft(value.route) : explicitDemo ? {
        ...emptyRouteDraft(), name: 'api-internal-tls', matchMode: 'sni' as const,
        snis: ['api.preview.example.com', 'cdn.preview.example.com'], targetHost: '10.20.0.8',
        quotaValue: 2, quotaUnit: 'TiB' as const, quotaPeriod: 'daily' as const,
      } : emptyRouteDraft();
      setData(value); setDraft(nextDraft); setExpertEnabled(Boolean(nextDraft.expertOverride));
      setAdvancedOpen(Boolean(nextDraft.expertOverride)); setAdditionalOpen(false); setInitialSignature(JSON.stringify(nextDraft));
    } catch (error) { setLoadError(error); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    if (routeId && anchoredRouteIDRef.current === routeId) {
      anchoredRouteIDRef.current = null;
      return;
    }
    void load();
  }, [nodeId, routeId]); // eslint-disable-line react-hooks/exhaustive-deps
  const peers = data?.routes ?? [];
  const draftErrors = useMemo(() => validateRouteDraft(draft, [], data?.route?.id), [draft, data?.route?.id]);
  const activationErrors = useMemo(() => validateRouteDraft(draft, peers, data?.route?.id), [draft, peers, data?.route?.id]);
  const errors = validationIntent === 'draft' ? draftErrors : activationErrors;
  const preview = useMemo(() => renderRoutePreview(draft, peers.filter((route) => route.id !== data?.route?.id)), [draft, peers, data?.route?.id]);
  const dirty = Boolean(initialSignature) && JSON.stringify(draft) !== initialSignature;
  const blocker = useBlocker(({ currentLocation, nextLocation }) => (
    !bypassNavigationRef.current && dirty && !saving && currentLocation.pathname !== nextLocation.pathname
  ));
  useEffect(() => { bypassNavigationRef.current = false; }, [location.key]);
  const editing = Boolean(data?.route);
  const editingEnabled = Boolean(data?.route?.enabled);
  const query = explicitDemo ? '?demo=1' : location.search;
  const nodeURL = `/nodes/${encodeURIComponent(nodeId)}${query}`;
  const editURL = (id: string) => `/nodes/${encodeURIComponent(nodeId)}/routes/${encodeURIComponent(id)}/edit${query}`;

  useBeforeUnload((event) => {
    if (!dirty || saving) return;
    event.preventDefault();
    event.returnValue = '';
  });

  const update = <K extends keyof RouteDraft>(key: K, value: RouteDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
    setSavedMessage(''); setSaveError('');
  };
  const leave = () => navigate(nodeURL);
  const focusFirstError = () => {
    window.requestAnimationFrame(() => {
      const invalid = formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"], [data-invalid="true"]');
      invalid?.focus(); invalid?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
  };

  const save = async (intent: SaveIntent) => {
    setValidationIntent(intent);
    setSubmitAttempted(true); setSaveError(''); setSavedMessage('');
    const intentErrors = intent === 'draft' ? draftErrors : activationErrors;
    if (intentErrors.length) { focusFirstError(); return; }
    if (!data) return;
    setSaving(intent);
    let anchoredCreatedDraft = false;
    try {
      let result: RouteRecord;
      if (explicitDemo) {
        await new Promise((resolve) => window.setTimeout(resolve, 420));
        result = {
          id: data.route?.id ?? crypto.randomUUID(), node_id: nodeId, version: (data.route?.version ?? 0) + 1,
          ...routePayload(draft, intent !== 'draft'),
          listener_port: Number(draft.listenerPort), target_port: draft.targetMode === 'unix' ? 0 : Number(draft.targetPort),
          deployed: intent !== 'draft', deployment_state: intent === 'draft' ? 'draft' : 'pending',
          created_at: data.route?.created_at ?? new Date().toISOString(), updated_at: new Date().toISOString(),
        } as RouteRecord;
        upsertDemoRoute(nodeId, result);
      } else if (data.route) {
        result = await api<RouteRecord>(`/api/v1/nodes/${nodeId}/routes/${data.route.id}`, {
          method: 'PUT',
          body: JSON.stringify(routePayload(draft, intent === 'apply' || intent === 'enable', data.route.version)),
        });
      } else {
        const created = await api<RouteRecord>(`/api/v1/nodes/${nodeId}/routes`, {
          method: 'POST', body: JSON.stringify(routePayload(draft, false)),
        });
        const createdDraft = routeToDraft({ ...created, name: draft.name });
        anchoredCreatedDraft = true;
        anchoredRouteIDRef.current = created.id;
        setData((current) => current ? {
          ...current, route: created,
          routes: [...current.routes.filter((route) => route.id !== created.id), created],
        } : current);
        setDraft(createdDraft);
        setInitialSignature(JSON.stringify(createdDraft));
        bypassNavigationRef.current = true;
        navigate(editURL(created.id), { replace: true });
        if (intent === 'draft') result = created;
        else {
          result = await api<RouteRecord>(`/api/v1/nodes/${nodeId}/routes/${created.id}`, {
            method: 'PUT', body: JSON.stringify(routePayload(draft, true, created.version)),
          });
        }
      }
      const cleanDraft = routeToDraft({ ...result, name: draft.name });
      setData((current) => current ? {
        ...current, route: result,
        routes: [...current.routes.filter((route) => route.id !== result.id), result],
      } : current);
      setDraft(cleanDraft); setInitialSignature(JSON.stringify(cleanDraft)); setSavedMessage(statusCopy(intent, editing));
      if (!editing && !anchoredCreatedDraft) { bypassNavigationRef.current = true; navigate(editURL(result.id), { replace: true }); }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Маршрут не сохранён';
      const detail = message.includes('version') ? 'Маршрут изменился в другой вкладке. Обновите данные и повторите.' : message;
      setSaveError(anchoredCreatedDraft ? `Черновик сохранён, но включить маршрут не удалось: ${detail}` : detail);
    } finally { setSaving(null); }
  };

  const submit = (event: FormEvent) => { event.preventDefault(); void save(editingEnabled ? 'apply' : 'enable'); };
  const discardAndLeave = () => {
    if (blocker.state !== 'blocked') return;
    blocker.proceed();
  };

  if (loading) return <main className="nf-page nf-route-editor-page"><div className="nf-route-editor-loading"><LoadingOverlay visible /><span>Загружаем маршруты ноды и проверяем listener-конфликты…</span></div></main>;
  if (loadError && isUnauthorized(loadError)) return <LoginPanel onSuccess={load} />;
  if (loadError || !data) return <main className="nf-page"><StateView title="Редактор не загрузился" description={loadError instanceof Error ? loadError.message : 'Нода или маршрут не найдены'} tone="error" action={<><Button variant="default" onClick={() => navigate(`/nodes${query}`)}>К нодам</Button><Button onClick={load}>Повторить</Button></>} /></main>;

  const showError = (fields: RouteDraftError['field'][]) => submitAttempted ? errorText(errors, fields) : undefined;
  const periodNote = quotaPeriodOptions.find((option) => option.value === draft.quotaPeriod)?.description;
  const valid = errors.length === 0;
  const previewState = valid ? 'valid' : submitAttempted ? 'invalid' : 'incomplete';

  return (
    <main className="nf-page nf-route-editor-page">
      <PageHeader
        className="nf-route-editor-header"
        breadcrumb={<><Link to={`/nodes${query}`}>Ноды</Link><span>/</span><Link to={nodeURL}>{data.node.name}</Link><span>/</span><span aria-current="page">{editing ? 'Редактирование' : 'Новый маршрут'}</span></>}
        backAction={<Button variant="subtle" color="gray" px={6} onClick={leave} aria-label="Назад к ноде"><IconArrowLeft size={20} /></Button>}
        title={editing ? `Редактировать · ${draft.name}` : 'Создать маршрут'}
      />

      <form ref={formRef} className="nf-route-editor-layout" onSubmit={submit} noValidate aria-busy={Boolean(saving)}>
        <Surface className="nf-route-editor-form">
          <section className="nf-route-form-section" aria-labelledby="route-basic-title">
            <div className="nf-route-form-section__head"><IconRoute size={18} /><div><h2 id="route-basic-title">Основное</h2></div></div>
            <div className="nf-field-grid nf-field-grid--2-even">
              <TextInput label="Имя маршрута" placeholder="api-internal-tls" value={draft.name} onChange={(event) => update('name', event.currentTarget.value)} error={showError(['name'])} required maxLength={80} />
              <div className="nf-route-state-field">
                <span>Состояние после сохранения</span>
                <strong className={editingEnabled ? 'is-enabled' : ''}><i />{editingEnabled ? 'Включён · применится сразу' : 'Выключенный черновик'}</strong>
              </div>
            </div>
          </section>

          <section className="nf-route-form-section nf-route-listener-section" aria-labelledby="route-listener-title">
            <div className="nf-route-form-section__head"><span className="nf-section-index">1</span><div><h2 id="route-listener-title">Входящее соединение</h2></div></div>
            <div>
              <label className="nf-field-label">Как выбрать маршрут</label>
              <SegmentedControl fullWidth value={draft.matchMode} onChange={(value) => {
                const mode = value as RouteMatchMode;
                update('matchMode', mode);
                if (mode === 'destination_ip' && draft.listenerIP === '*') update('listenerIP', data.node.address);
              }} data={[
                { value: 'any_tcp', label: 'Любой TCP' }, { value: 'sni', label: 'SNI' }, { value: 'destination_ip', label: 'IP назначения' },
              ]} aria-label="Способ выбора маршрута" />
              <p className="nf-control-help">{draft.matchMode === 'sni' ? 'TLS ClientHello разделяет несколько маршрутов на одном listener.' : draft.matchMode === 'destination_ip' ? 'Трафик выбирается по конкретному локальному IP ноды и порту.' : 'Маршрут по умолчанию для всего TCP-трафика этого listener.'}</p>
            </div>
            <div className="nf-field-grid nf-field-grid--listener">
              <TextInput label="Адрес listener" value={draft.listenerIP} onChange={(event) => update('listenerIP', event.currentTarget.value)} error={showError(['listenerIP', 'listener'])} placeholder="*" required />
              <NumberInput label="Порт" value={draft.listenerPort} onChange={(value) => update('listenerPort', value === '' ? '' : Number(value))} error={showError(['listenerPort'])} min={1} max={65535} allowDecimal={false} inputMode="numeric" required />
            </div>
            {draft.matchMode === 'sni' && <TagsInput className="nf-route-sni-input" classNames={{ pill: 'nf-route-sni-pill', pillsList: 'nf-route-sni-pills', inputField: 'nf-route-sni-field' }} label="SNI" description="Enter добавляет домен. Порядок не влияет на маршрутизацию." placeholder="api.example.com" value={draft.snis} onChange={(value) => update('snis', value)} error={showError(['snis'])} splitChars={[',', ' ']} clearable required />}
          </section>

          <section className="nf-route-form-section" aria-labelledby="route-target-title">
            <div className="nf-route-form-section__head"><span className="nf-section-index">2</span><div><h2 id="route-target-title">Назначение</h2></div></div>
            <div>
              <label className="nf-field-label">Тип назначения</label>
              <SegmentedControl fullWidth value={draft.targetMode} onChange={(value) => update('targetMode', value as RouteTargetMode)} data={[
                { value: 'ip', label: 'IP' }, { value: 'domain', label: 'Домен' }, { value: 'unix', label: 'Unix socket' },
              ]} aria-label="Тип назначения" />
            </div>
            {draft.targetMode === 'unix' ? (
              <TextInput label="Путь Unix socket" placeholder="/run/xray/inbound.sock" value={draft.unixSocketPath} onChange={(event) => update('unixSocketPath', event.currentTarget.value)} error={showError(['unixSocketPath', 'target'])} required />
            ) : (
              <div className="nf-field-grid nf-field-grid--target">
                <TextInput label={draft.targetMode === 'ip' ? 'IP-адрес назначения' : 'Домен назначения'} placeholder={draft.targetMode === 'ip' ? '10.20.0.8' : 'origin.example.com'} value={draft.targetHost} onChange={(event) => update('targetHost', event.currentTarget.value)} error={showError(['targetHost', 'target'])} required />
                <NumberInput label="Порт" value={draft.targetPort} onChange={(value) => update('targetPort', value === '' ? '' : Number(value))} error={showError(['targetPort'])} min={1} max={65535} allowDecimal={false} inputMode="numeric" required />
              </div>
            )}
            <div className="nf-route-inline-options">
              <Switch checked={draft.healthCheck} onChange={(event) => update('healthCheck', event.currentTarget.checked)} label="Проверка здоровья" description="TCP-check перед передачей новых соединений." />
              <div>
                <label className="nf-field-label">PROXY protocol к назначению</label>
                <SegmentedControl fullWidth value={draft.proxyProtocol} onChange={(value) => update('proxyProtocol', value as ProxyProtocol)} data={[
                  { value: 'none', label: 'Не передавать' }, { value: 'v1', label: 'v1' }, { value: 'v2', label: 'v2' },
                ]} aria-label="Версия PROXY protocol к назначению" />
              </div>
            </div>
          </section>

          <section className="nf-route-form-section" aria-labelledby="route-quota-title">
            <div className="nf-route-form-section__head nf-route-form-section__head--switch"><span className="nf-section-index">3</span><div><h2 id="route-quota-title">Лимит трафика</h2></div><Switch checked={draft.quotaEnabled} onChange={(event) => update('quotaEnabled', event.currentTarget.checked)} aria-label="Включить лимит трафика" /></div>
            <Collapse in={draft.quotaEnabled}>
              <div className="nf-route-quota-fields">
                <div className="nf-route-quota-value">
                  <NumberInput label="Лимит" value={draft.quotaValue} onChange={(value) => update('quotaValue', value === '' ? '' : Number(value))} error={showError(['quota'])} min={0.001} decimalScale={3} inputMode="decimal" required={draft.quotaEnabled} />
                  <Select label="Единица" value={draft.quotaUnit} onChange={(value) => update('quotaUnit', (value ?? 'GiB') as 'GiB' | 'TiB')} allowDeselect={false} data={['GiB', 'TiB']} />
                </div>
                <Select label="Период сброса" value={draft.quotaPeriod} onChange={(value) => update('quotaPeriod', (value ?? 'calendar_month') as QuotaPeriod)} allowDeselect={false} data={quotaPeriodOptions.map(({ value, label }) => ({ value, label }))} description={periodNote} />
                <Select label="При достижении" value={draft.quotaAction} onChange={(value) => update('quotaAction', (value ?? 'observe') as QuotaAction)} allowDeselect={false} data={[{ value: 'observe', label: 'Только уведомить' }, { value: 'block_new', label: 'Блокировать новые соединения' }]} />
              </div>
              <Alert mt="md" color={draft.quotaAction === 'block_new' ? 'yellow' : 'gray'} icon={<IconShieldCheck size={18} />}>
                {draft.quotaAction === 'block_new' ? 'После лимита новые соединения перестанут направляться в backend. Активные сессии не разрываются.' : 'Panel подсветит превышение, но HAProxy продолжит передавать трафик.'}
              </Alert>
            </Collapse>
          </section>

          <section className="nf-route-form-section nf-route-advanced" aria-labelledby="route-additional-title">
            <button type="button" className="nf-route-advanced__toggle" onClick={() => setAdditionalOpen((value) => !value)} aria-expanded={additionalOpen}>
              <span><IconCode size={18} /><strong id="route-additional-title">Дополнительно</strong><small>Таймауты, безопасные backend-директивы и параметры Agent</small></span>
              {additionalOpen ? <IconChevronUp size={17} /> : <IconChevronDown size={17} />}
            </button>
            <Collapse in={additionalOpen}>
              <div className="nf-route-additional-grid">
                <div><span>Проверка перед активацией</span><strong><code>haproxy -c</code> на ноде</strong></div>
                <div><span>Listener lifecycle</span><strong>UFW по политике ноды</strong></div>
                <div><span>Ручные директивы</span><strong>В expert-слое справа</strong></div>
              </div>
            </Collapse>
          </section>

          <div className="nf-route-safety-note"><IconShieldCheck size={18} /><p><strong>Безопасное применение</strong><span>Agent проверит <code>haproxy -c</code>; UFW изменится только по политике ноды. Активные соединения не разрываются.</span></p></div>

          {(submitAttempted && errors.length > 0) && <Alert className="nf-route-error-summary" color="red" icon={<IconAlertCircle size={18} />} title={`Нужно исправить: ${errors.length}`} role="alert"><ul>{errors.slice(0, 5).map((error, index) => <li key={`${error.field}-${index}`}>{error.message}</li>)}</ul></Alert>}
          {saveError && <Alert color="red" icon={<IconAlertCircle size={18} />} role="alert">{saveError}</Alert>}
          {savedMessage && <Alert color="nodeflow" icon={<IconCheck size={18} />} role="status">{savedMessage}</Alert>}
          <footer className="nf-route-editor-actions">
            {!editingEnabled && <Button variant="default" onClick={() => void save('draft')} loading={saving === 'draft'} disabled={Boolean(saving && saving !== 'draft')}>Сохранить черновик</Button>}
            <Button type="submit" loading={saving === (editingEnabled ? 'apply' : 'enable')} disabled={Boolean(saving && saving !== (editingEnabled ? 'apply' : 'enable'))}>{editingEnabled ? 'Сохранить и применить' : 'Сохранить и включить'}</Button>
          </footer>
        </Surface>

        <aside className="nf-route-preview-column" aria-label="Предпросмотр HAProxy">
          <Surface className="nf-route-preview" title="Предпросмотр HAProxy" description="Управляемый слой обновляется вместе с формой." actions={<Tooltip label="Managed sections нельзя заменить"><IconInfoCircle size={17} /></Tooltip>}>
            <div className={`nf-preview-validation is-${previewState}`} role="status"><span>{valid ? <IconCheck size={16} /> : submitAttempted ? <IconAlertCircle size={16} /> : <IconInfoCircle size={16} />}{valid ? 'Конфигурация формы валидна' : submitAttempted ? `Найдено ошибок: ${errors.length}` : 'Заполните обязательные поля'}</span><small>{preview.merged ? `Будет общий frontend с ${preview.merged} маршрутами` : 'Новый listener frontend'}</small></div>
            <div className="nf-code-editor" aria-label="HAProxy preview, управляемая часть только для чтения">
              <div className="nf-code-editor__bar"><span><i /><i /><i /></span><b>haproxy.cfg · предпросмотр</b><em>только чтение + расширенный слой</em></div>
              <pre><code>{preview.config}</code></pre>
            </div>
            <section className="nf-preview-expert" aria-labelledby="route-expert-title">
              <button type="button" className="nf-preview-expert__toggle" onClick={() => setAdvancedOpen((value) => !value)} aria-expanded={advancedOpen}>
                <span><IconCode size={17} /><span><strong id="route-expert-title">Редактируемый expert-слой</strong><small>Только безопасные директивы текущего backend</small></span></span>
                {advancedOpen ? <IconChevronUp size={17} /> : <IconChevronDown size={17} />}
              </button>
              <Collapse in={advancedOpen}>
                <div className="nf-preview-expert__body">
                  <Switch checked={expertEnabled} onChange={(event) => { setExpertEnabled(event.currentTarget.checked); if (!event.currentTarget.checked) update('expertOverride', ''); }} label="Разрешить ручные директивы" description="NodeFlow не позволит объявить global, frontend, listen, backend или resolvers." />
                  {expertEnabled && <Textarea label="Backend directives" value={draft.expertOverride} onChange={(event) => update('expertOverride', event.currentTarget.value)} error={showError(['expert'])} placeholder={'timeout connect 5s\nmaxconn 2000'} autosize minRows={5} maxRows={12} classNames={{ input: 'nf-code-input' }} />}
                </div>
              </Collapse>
            </section>
            <div className="nf-preview-boundary"><IconShieldCheck size={17} /><p><strong>Граница безопасности</strong><span>Только безопасные backend-директивы; перед активацией Agent выполнит <code>haproxy -c</code>.</span></p></div>
          </Surface>
        </aside>
      </form>

      <Modal opened={blocker.state === 'blocked'} onClose={() => blocker.state === 'blocked' && blocker.reset()} title="Выйти без сохранения?" size="sm" closeOnClickOutside closeOnEscape classNames={{ content: 'nf-dialog', header: 'nf-dialog__header', body: 'nf-dialog__body' }}>
        <div className="nf-confirm-dialog"><p>Изменения маршрута будут потеряны. HAProxy и UFW не изменятся.</p><div><Button variant="default" onClick={() => blocker.state === 'blocked' && blocker.reset()}>Продолжить редактирование</Button><Button color="red" onClick={discardAndLeave}>Выйти</Button></div></div>
      </Modal>
    </main>
  );
}
