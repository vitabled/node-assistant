import type { RouteRecord } from '../../lib/contracts';
import { formatBytes } from '../../lib/format';

export type RouteMatchMode = 'any_tcp' | 'sni' | 'destination_ip';
export type RouteTargetMode = 'ip' | 'domain' | 'unix';
export type QuotaPeriod = 'hourly' | 'daily' | 'calendar_month' | 'monthly_from_creation';
export type QuotaAction = 'observe' | 'block_new';
export type ProxyProtocol = 'none' | 'v1' | 'v2';

export interface RouteDraft {
  name: string;
  matchMode: RouteMatchMode;
  listenerIP: string;
  listenerPort: number | '';
  snis: string[];
  targetMode: RouteTargetMode;
  targetHost: string;
  targetPort: number | '';
  unixSocketPath: string;
  healthCheck: boolean;
  proxyProtocol: ProxyProtocol;
  quotaEnabled: boolean;
  quotaValue: number | '';
  quotaUnit: 'GiB' | 'TiB';
  quotaPeriod: QuotaPeriod;
  quotaAction: QuotaAction;
  expertOverride: string;
}

export interface RouteDraftError {
  field: keyof RouteDraft | 'listener' | 'target' | 'quota' | 'expert';
  message: string;
  severity?: 'error' | 'warning';
}

const GIB = 1024 ** 3;
const TIB = 1024 ** 4;
const MAX_SAFE_BYTES = Number.MAX_SAFE_INTEGER;
const DNS_LABEL = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/i;
const IPV4 = /^(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
const IPV6 = /^(?:[a-f0-9]{0,4}:){2,7}[a-f0-9]{0,4}$/i;
const FORBIDDEN_SECTIONS = new Set(['global', 'defaults', 'frontend', 'backend', 'listen', 'peers', 'resolvers', 'userlist', 'mailers', 'cache', 'program', 'ring', 'http-errors']);

export const quotaPeriodOptions = [
  { value: 'hourly', label: 'Каждый час', description: 'Сбрасывается в начале следующего часа.' },
  { value: 'daily', label: 'Ежедневно', description: 'Сбрасывается в 00:00 UTC.' },
  { value: 'calendar_month', label: 'Календарный месяц', description: 'Сбрасывается первого числа в 00:00 UTC.' },
  { value: 'monthly_from_creation', label: 'Месяц от создания', description: 'Окно начинается в дату создания маршрута.' },
] as const;

export function routeDisplayName(route: RouteRecord): string {
  if (route.name?.trim()) return route.name.trim();
  if (route.fallback) return `tcp-${route.listener_port}`;
  return route.snis[0] ?? route.hostname ?? `route-${route.listener_port}`;
}

export function emptyRouteDraft(): RouteDraft {
  return {
    name: '', matchMode: 'any_tcp', listenerIP: '*', listenerPort: 443, snis: [],
    targetMode: 'ip', targetHost: '', targetPort: 443, unixSocketPath: '', healthCheck: true,
    proxyProtocol: 'none', quotaEnabled: false, quotaValue: '', quotaUnit: 'GiB',
    quotaPeriod: 'calendar_month', quotaAction: 'observe', expertOverride: '',
  };
}

export function routeToDraft(route: RouteRecord): RouteDraft {
  const bytes = route.quota_bytes ?? 0;
  const useTiB = bytes >= TIB && bytes % TIB === 0;
  const listenerIP = route.listener_ip || '*';
  const matchMode: RouteMatchMode = ['any_tcp', 'sni', 'destination_ip'].includes(route.match_mode)
    ? route.match_mode
    : route.fallback
      ? listenerIP === '*' ? 'any_tcp' : 'destination_ip'
      : 'sni';
  const host = route.target_host ?? '';
  const targetMode: RouteTargetMode = route.target_type === 'unix'
    ? 'unix'
    : isIPAddress(host) ? 'ip' : 'domain';
  return {
    name: routeDisplayName(route), matchMode, listenerIP, listenerPort: route.listener_port,
    snis: route.snis ?? [], targetMode, targetHost: host, targetPort: route.target_port || 443,
    unixSocketPath: route.unix_socket_path ?? '', healthCheck: route.health_check ?? true,
    proxyProtocol: (['none', 'v1', 'v2'].includes(route.proxy_protocol) ? route.proxy_protocol : 'none') as ProxyProtocol,
    quotaEnabled: bytes > 0, quotaValue: bytes > 0 ? bytes / (useTiB ? TIB : GIB) : '',
    quotaUnit: useTiB ? 'TiB' : 'GiB',
    quotaPeriod: (['hourly', 'daily', 'calendar_month', 'monthly_from_creation'].includes(route.quota_period)
      ? route.quota_period : 'calendar_month') as QuotaPeriod,
    quotaAction: route.quota_action === 'block_new' ? 'block_new' : 'observe',
    expertOverride: route.custom_fragment ?? '',
  };
}

export function isIPAddress(value: string): boolean {
  const trimmed = value.trim();
  return IPV4.test(trimmed) || (trimmed.includes(':') && IPV6.test(trimmed));
}

function isDNSName(value: string): boolean {
  const normalized = value.trim().replace(/\.$/, '').toLowerCase();
  return normalized.length > 0 && normalized.length <= 253
    && normalized.split('.').every((label) => DNS_LABEL.test(label));
}

function isWellFormedUTF16(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function isCanonicalUnixSocketPath(value: string): boolean {
  const bytes = new TextEncoder().encode(value).length;
  if (!isWellFormedUTF16(value) || bytes < 2 || bytes > 107 || !value.startsWith('/')) return false;
  if (!/^[A-Za-z0-9/._-]+$/.test(value) || value.endsWith('/') || value.includes('//')) return false;
  return value.slice(1).split('/').every((segment) => segment !== '' && segment !== '.' && segment !== '..');
}

function listenerOverlaps(leftIP: string, leftPort: number, rightIP: string, rightPort: number): boolean {
  if (leftPort !== rightPort) return false;
  const leftWildcard = leftIP === '*' || leftIP === '0.0.0.0' || leftIP === '::';
  const rightWildcard = rightIP === '*' || rightIP === '0.0.0.0' || rightIP === '::';
  return leftIP === rightIP || leftWildcard || rightWildcard;
}

function sameListener(leftIP: string, leftPort: number, rightIP: string, rightPort: number): boolean {
  return leftIP.toLowerCase() === rightIP.toLowerCase() && leftPort === rightPort;
}

export function quotaBytes(draft: RouteDraft): number | null {
  if (!draft.quotaEnabled || draft.quotaValue === '') return null;
  const multiplier = draft.quotaUnit === 'TiB' ? TIB : GIB;
  return Math.round(Number(draft.quotaValue) * multiplier);
}

export function validateExpertOverride(value: string): RouteDraftError[] {
  const encoder = new TextEncoder();
  if (encoder.encode(value).length > 8192) {
    return [{ field: 'expert', message: 'Экспертный слой превышает 8192 байт.' }];
  }
  if (!isWellFormedUTF16(value)) {
    return [{ field: 'expert', message: 'Экспертный слой должен быть корректным UTF-8.' }];
  }
  const normalizedInput = value.replace(/\r\n/g, '\n');
  if (/[\u0000-\u0008\u000b-\u001f\u007f]/.test(normalizedInput)) {
    return [{ field: 'expert', message: 'Экспертный слой содержит управляющий символ.' }];
  }
  if (normalizedInput.includes('\\')) {
    return [{ field: 'expert', message: 'Перенос и экранирование директив через \\ запрещены.' }];
  }

  const normalizedLines: string[] = [];
  let blankPending = false;
  for (const rawLine of normalizedInput.split('\n')) {
    if (encoder.encode(rawLine).length > 512) {
      return [{ field: 'expert', message: 'Одна из директив длиннее 512 байт.' }];
    }
    const line = rawLine.trim();
    if (!line) {
      if (normalizedLines.length > 0) blankPending = true;
      continue;
    }
    const directive = line.split(/\s+/)[0].toLowerCase();
    if (FORBIDDEN_SECTIONS.has(directive)) {
      return [{ field: 'expert', message: `Секция ${directive} управляется NodeFlow и не может быть переопределена.` }];
    }
    if (blankPending) {
      normalizedLines.push('');
      blankPending = false;
    }
    normalizedLines.push(`    ${line}`);
  }
  if (encoder.encode(normalizedLines.join('\n')).length > 8192) {
    return [{ field: 'expert', message: 'Экспертный слой превышает 8192 байт после нормализации.' }];
  }
  return [];
}

export function validateRouteDraft(draft: RouteDraft, peers: RouteRecord[], editingID?: string): RouteDraftError[] {
  const errors: RouteDraftError[] = [];
  const add = (field: RouteDraftError['field'], message: string) => errors.push({ field, message });
  const listenerPort = Number(draft.listenerPort);
  const listenerIP = draft.listenerIP.trim() || '*';
  const snis = draft.snis.map((value) => value.trim().replace(/\.$/, '').toLowerCase()).filter(Boolean);

  if (!draft.name.trim()) add('name', 'Укажите имя маршрута. Оно видно только оператору.');
  else if (draft.name.trim().length > 80) add('name', 'Имя маршрута не должно превышать 80 символов.');
  if (listenerIP !== '*' && !isIPAddress(listenerIP)) add('listenerIP', 'Адрес listener должен быть * или корректным IP.');
  if (!Number.isInteger(listenerPort) || listenerPort < 1 || listenerPort > 65535) add('listenerPort', 'Порт listener должен быть от 1 до 65535.');
  if (draft.matchMode === 'any_tcp' && listenerIP !== '*') add('listenerIP', 'Для «Любой TCP» используйте wildcard listener *.');
  if (draft.matchMode === 'destination_ip' && listenerIP === '*') add('listenerIP', 'Для IP назначения выберите конкретный локальный IP ноды.');
  if (draft.matchMode === 'sni') {
    if (!snis.length) add('snis', 'Добавьте хотя бы один SNI.');
    if (snis.length > 64) add('snis', 'Можно указать не больше 64 SNI.');
    if (snis.some((sni) => !isDNSName(sni))) add('snis', 'Каждый SNI должен быть корректным DNS-именем.');
    if (new Set(snis).size !== snis.length) add('snis', 'SNI внутри маршрута не должны повторяться.');
  }

  if (draft.targetMode === 'unix') {
    const socket = draft.unixSocketPath.trim();
    if (!isCanonicalUnixSocketPath(socket)) {
      add('unixSocketPath', 'Укажите canonical absolute path Unix socket длиной до 107 байт.');
    }
  } else {
    const target = draft.targetHost.trim();
    if (draft.targetMode === 'ip' ? !isIPAddress(target) : !isDNSName(target)) {
      add('targetHost', draft.targetMode === 'ip' ? 'Укажите корректный IP-адрес назначения.' : 'Укажите корректный домен назначения.');
    }
    const targetPort = Number(draft.targetPort);
    if (!Number.isInteger(targetPort) || targetPort < 1 || targetPort > 65535) add('targetPort', 'Порт target должен быть от 1 до 65535.');
  }

  const bytes = quotaBytes(draft);
  if (draft.quotaEnabled && (draft.quotaValue === '' || Number(draft.quotaValue) <= 0 || !Number.isFinite(bytes) || Number(bytes) > MAX_SAFE_BYTES)) {
    add('quota', 'Лимит должен быть положительным числом в безопасном диапазоне.');
  }
  if (draft.quotaAction === 'block_new' && !draft.quotaEnabled) add('quota', 'Блокировка новых соединений требует включённого лимита.');
  errors.push(...validateExpertOverride(draft.expertOverride));

  for (const peer of peers.filter((route) => route.id !== editingID && !route.delete_pending && route.deployment_state !== 'deleting')) {
    if (!Number.isInteger(listenerPort) || !listenerOverlaps(listenerIP, listenerPort, peer.listener_ip || '*', peer.listener_port)) continue;
    const exact = sameListener(listenerIP, listenerPort, peer.listener_ip || '*', peer.listener_port);
    if (!exact) {
      add('listener', `Порт ${listenerPort} уже слушается ${peer.listener_ip || '*'}. Нельзя смешивать wildcard и конкретный IP.`);
      continue;
    }
    const fallback = draft.matchMode !== 'sni';
    if (fallback && peer.fallback) add('listener', `Для ${listenerIP}:${listenerPort} уже есть маршрут «Любой TCP»/IP назначения.`);
    if (!fallback && !peer.fallback) {
      const duplicate = snis.find((sni) => (peer.snis ?? []).map((value) => value.toLowerCase()).includes(sni));
      if (duplicate) add('snis', `SNI ${duplicate} уже назначен другому маршруту на ${listenerIP}:${listenerPort}.`);
    }
  }
  return errors;
}

export function routePayload(draft: RouteDraft, enabled: boolean, expectedVersion?: number) {
  const fallback = draft.matchMode !== 'sni';
  const targetUnix = draft.targetMode === 'unix';
  const snis = fallback ? [] : draft.snis.map((value) => value.trim().replace(/\.$/, '').toLowerCase()).filter(Boolean);
  return {
    ...(expectedVersion ? { expected_version: expectedVersion } : {}),
    name: draft.name.trim(),
    hostname: snis[0] ?? '',
    listener_ip: draft.listenerIP.trim() || '*', listener_port: Number(draft.listenerPort), match_mode: draft.matchMode, snis, fallback,
    target_type: targetUnix ? 'unix' : 'tcp', target_host: targetUnix ? '' : draft.targetHost.trim(),
    target_port: targetUnix ? 0 : Number(draft.targetPort), unix_socket_path: targetUnix ? draft.unixSocketPath.trim() : '',
    health_check: draft.healthCheck, proxy_protocol: draft.proxyProtocol, quota_bytes: quotaBytes(draft),
    quota_action: draft.quotaEnabled ? draft.quotaAction : 'observe', quota_period: draft.quotaPeriod,
    enabled, custom_fragment: draft.expertOverride,
  };
}

function safeName(value: string): string {
  const normalized = value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return normalized || 'route_preview';
}

export function renderRoutePreview(draft: RouteDraft, peers: RouteRecord[]): { config: string; merged: number } {
  const listenerIP = draft.listenerIP.trim() || '*';
  const listenerPort = Number(draft.listenerPort) || 443;
  const activePeers = peers.filter((route) => route.enabled && sameListener(route.listener_ip || '*', route.listener_port, listenerIP, listenerPort));
  const current = routePayload(draft, true) as ReturnType<typeof routePayload> & { id?: string };
  const all = [...activePeers, current];
  const frontend = `nf_listener_${listenerPort}_${safeName(listenerIP === '*' ? 'any' : listenerIP)}`;
  const lines = [
    '# Managed by NodeFlow — preview',
    `frontend ${frontend}`,
    `    bind ${listenerIP}:${listenerPort}`,
    '    mode tcp',
  ];
  const sniRoutes = all.filter((route) => !route.fallback);
  if (sniRoutes.length) lines.push('    tcp-request inspect-delay 5s', '    tcp-request content accept if { req_ssl_hello_type 1 }');
  sniRoutes.forEach((route, index) => {
    const values = (route.snis ?? []).length ? route.snis : ['sni.example.com'];
    lines.push(`    acl nf_sni_${index + 1} req.ssl_sni -i ${values.join(' ')}`);
    lines.push(`    use_backend nf_be_${safeName(index === sniRoutes.length - 1 ? draft.name : values[0])} if nf_sni_${index + 1}`);
  });
  const fallbackRoute = all.find((route) => route.fallback);
  if (fallbackRoute) lines.push(`    default_backend nf_be_${safeName(fallbackRoute === current ? draft.name : fallbackRoute.snis?.[0] || `tcp_${fallbackRoute.listener_port}`)}`);
  lines.push('', `backend nf_be_${safeName(draft.name)}`, '    mode tcp');
  if (draft.healthCheck) lines.push('    option tcp-check');
  if (draft.expertOverride.trim()) {
    lines.push('    # ── expert override: backend directives only ──');
    draft.expertOverride.split('\n').forEach((line) => lines.push(`    ${line.trimStart()}`));
    lines.push('    # ── end expert override ──');
  }
  const target = draft.targetMode === 'unix' ? `unix@${draft.unixSocketPath || '/run/service.sock'}` : `${draft.targetHost || '10.20.0.8'}:${Number(draft.targetPort) || 443}`;
  const proxy = draft.proxyProtocol === 'v1' ? ' send-proxy' : draft.proxyProtocol === 'v2' ? ' send-proxy-v2' : '';
  const health = draft.healthCheck ? ' check inter 5s fall 3 rise 2' : '';
  lines.push(`    server target ${target}${health}${proxy}`);
  if (draft.quotaEnabled) lines.push('', `    # quota ${formatBytes(quotaBytes(draft) ?? 0)} · ${draft.quotaPeriod} · ${draft.quotaAction}`);
  return { config: lines.join('\n'), merged: activePeers.length };
}
