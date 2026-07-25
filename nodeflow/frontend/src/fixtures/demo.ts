import type {
  HAProxyProxyStats,
  NodeBundle,
  NodeOperational,
  NodeRecord,
  NodeTraffic,
  RouteRecord,
  TrafficHistory,
} from '../lib/contracts';

const GIB = 1024 ** 3;
const TIB = 1024 ** 4;
const now = Date.now();
const month = new Date().toISOString().slice(0, 7);

const nodeSpecs = [
  ['dev-node-01', '10.10.1.11', 128, 3842, 23, 41, 412e6, 256e6, 12.4 * TIB, 20 * TIB],
  ['edge-msk-01', '10.10.1.21', 96, 2911, 31, 48, 298e6, 184e6, 10.7 * TIB, 20 * TIB],
  ['edge-spb-02', '10.10.1.22', 84, 2374, 27, 44, 236e6, 152e6, 9.3 * TIB, 20 * TIB],
  ['relay-de-01', '10.10.2.31', 64, 2189, 19, 39, 152e6, 98e6, 7.6 * TIB, 15 * TIB],
  ['relay-nl-01', '10.10.2.32', 58, 1642, 17, 36, 98e6, 64e6, 4.9 * TIB, 10 * TIB],
  ['backup-ru-01', '10.10.3.41', 42, 1150, 15, 33, 44e6, 26e6, 3.5 * TIB, 10 * TIB],
] as const;

function uuid(index: number): string {
  return `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`;
}

function history(index: number, rx: number, tx: number): TrafficHistory {
  const samples = Array.from({ length: 49 }, (_, point) => {
    const progress = point / 48;
    const rise = progress < 0.5 ? progress * 1.5 : 0.75 - (progress - 0.5) * 0.5;
    const pulse = Math.sin(point * 0.9 + index) * 0.08 + Math.sin(point * 0.23) * 0.12;
    return {
      timestamp: new Date(now - (48 - point) * 30 * 60 * 1000).toISOString(),
      rx_bps: Math.max(0, rx * (0.48 + rise + pulse)),
      tx_bps: Math.max(0, tx * (0.4 + rise * 0.36 + pulse * 0.35)),
    };
  });
  return { range: '24h', bucket_seconds: 1800, samples };
}

function route(index: number, nodeIndex: number, listenerPort = 443): RouteRecord {
  const names = ['api.internal', 'cdn.video', 'storage-backup', 'update.service', 'metrics.internal'];
  const name = names[index % names.length];
  const anyTCP = index % names.length === 2;
  return {
    id: uuid(1000 + nodeIndex * 200 + index),
    node_id: uuid(nodeIndex + 1),
    name,
    version: 1,
    listener_ip: '*',
    listener_port: listenerPort,
    match_mode: anyTCP ? 'any_tcp' : 'sni',
    snis: anyTCP ? [] : [`${name}.example.com`],
    fallback: anyTCP,
    hostname: anyTCP ? '' : `${name}.example.com`,
    target_type: 'tcp',
    target_host: `10.20.${nodeIndex}.${8 + index}`,
    target_port: listenerPort,
    unix_socket_path: '',
    health_check: true,
    proxy_protocol: index % 3 === 0 ? 'v2' : 'none',
    quota_bytes: null,
    quota_action: 'observe',
    quota_period: 'calendar_month',
    enabled: true,
    deployed: true,
    deployment_state: 'active',
  };
}

function makeBundle(spec: (typeof nodeSpecs)[number], index: number): NodeBundle {
  const [name, address, routeCount, connections, cpu, ram, rx, tx, used, limit] = spec;
  const node: NodeRecord = {
    id: uuid(index + 1),
    name,
    address,
    status: 'online',
    metadata: { traffic_limit_bytes: limit },
    last_seen_at: new Date(now - 8000 - index * 1100).toISOString(),
    created_at: new Date(now - 14 * 864e5).toISOString(),
    updated_at: new Date(now - 8000).toISOString(),
  };
  const backends = Object.fromEntries(
    Array.from({ length: Math.min(routeCount, 5) }, (_, routeIndex) => [
      `nf_be_${String(index)}_${routeIndex}`,
      {
        status: 'UP',
        sessions_current: Math.round(connections / Math.min(routeCount, 5)),
        sessions_total: connections * 120,
        session_rate: Math.max(1, Math.round(connections / 380)),
      } satisfies HAProxyProxyStats,
    ]),
  );
  const operational: NodeOperational = {
    node,
    haproxy_control: {
      node_id: node.id,
      supported: true,
      desired_enabled: true,
      generation: 1,
      actual_enabled: true,
      active_state: 'active',
      report_generation: 1,
      reported_at: node.last_seen_at,
      updated_at: node.updated_at,
    },
    routes_total: routeCount,
    routes_enabled: routeCount,
    traffic_month: month,
    traffic_bytes_in: Math.round(used * 0.64),
    traffic_bytes_out: Math.round(used * 0.36),
    traffic_used_bytes: Math.round(used),
    traffic_observed: true,
    latest_heartbeat: {
      agent_version: '0.4.4-dev',
      status: 'online',
      received_at: node.last_seen_at!,
      routes_ok: true,
      metrics: {
        os: 'linux',
        arch: 'amd64',
        cpu_count: index < 3 ? 8 : 4,
        cpu_percent: cpu,
        load: [cpu / 28, cpu / 31, cpu / 34],
        memory_total_bytes: 8 * GIB,
        memory_available_bytes: 8 * GIB * (1 - ram / 100),
        network_bytes_per_second: { eth0_rx: rx / 8, eth0_tx: tx / 8 },
        haproxy_version: '2.8.16-0ubuntu0.24.04.3',
        haproxy_stats_available: true,
        haproxy_runtime: {
          connections_current: connections,
          connections_total: connections * 220,
          connection_rate: Math.max(1, Math.round(connections / 340)),
          backends,
        },
      },
    },
  };
  const routes = Array.from({ length: Math.min(routeCount, 5) }, (_, routeIndex) =>
    route(routeIndex, index, routeIndex === 2 ? 10065 : 443),
  );
  const trafficRoutes = routes.map((item, routeIndex) => ({
    route_id: item.id,
    backend_key: `nf_be_${String(index)}_${routeIndex}`,
    bytes_in: Math.round((used * (0.3 - routeIndex * 0.035)) / routes.length),
    bytes_out: Math.round((used * (0.18 - routeIndex * 0.018)) / routes.length),
    used_bytes: Math.round((used * (0.48 - routeIndex * 0.053)) / routes.length),
    quota_used_bytes: 0,
    limit_bytes: null,
    reached: false,
    observed: true,
    applied: true,
  }));
  const traffic: NodeTraffic = {
    node_id: node.id,
    month,
    bytes_in: operational.traffic_bytes_in,
    bytes_out: operational.traffic_bytes_out,
    used_bytes: operational.traffic_used_bytes,
    enforcement: false,
    routes: trafficRoutes,
  };
  return { node, operational, routes, traffic, history: history(index, rx, tx) };
}

export const demoNodeBundles: NodeBundle[] = nodeSpecs.map(makeBundle);

// Demo mutations live for the current browser session. Keeping them outside a
// page component makes route URLs behave like the real API after React Router
// remounts the editor.
const demoRouteOverrides = new Map<string, Map<string, RouteRecord>>();
const demoRoutesStorageKey = 'nodeflow.demo.routes.v1';

function restoreDemoRoutes(): void {
  if (demoRouteOverrides.size) return;
  try {
    const stored = globalThis.sessionStorage?.getItem(demoRoutesStorageKey);
    if (!stored) return;
    const nodes = JSON.parse(stored) as Record<string, RouteRecord[]>;
    Object.entries(nodes).forEach(([nodeID, routes]) => {
      demoRouteOverrides.set(nodeID, new Map(routes.map((route) => [route.id, route])));
    });
  } catch {
    globalThis.sessionStorage?.removeItem(demoRoutesStorageKey);
  }
}

function persistDemoRoutes(): void {
  try {
    globalThis.sessionStorage?.setItem(demoRoutesStorageKey, JSON.stringify(Object.fromEntries(
      [...demoRouteOverrides].map(([nodeID, routes]) => [nodeID, [...routes.values()]]),
    )));
  } catch {
    // Demo persistence is optional; the real panel remains API-backed.
  }
}

export function demoRoutesForNode(nodeID: string): RouteRecord[] {
  restoreDemoRoutes();
  return [...(demoRouteOverrides.get(nodeID)?.values() ?? [])].map((route) => structuredClone(route));
}

export function upsertDemoRoute(nodeID: string, route: RouteRecord): void {
  restoreDemoRoutes();
  const routes = demoRouteOverrides.get(nodeID) ?? new Map<string, RouteRecord>();
  routes.set(route.id, structuredClone({ ...route, node_id: nodeID }));
  demoRouteOverrides.set(nodeID, routes);
  persistDemoRoutes();
}
