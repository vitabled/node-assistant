// ============================================================
// Xray config TypeScript model (node-installer «Профили»)
//
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// Original source used Zod schemas for both typing and validation; this port
// keeps the runtime validation in ajv (see core/schema.ts + core/validators.ts)
// and declares the shapes here as plain TypeScript interfaces. Structural, so
// unknown keys pass through untouched (the editor never strips fields it does
// not model). https://github.com/bropines/xray-config-ui-editor
// ============================================================

export interface XrayConfig {
  log?: LogConfig;
  api?: ApiConfig;
  dns?: DnsConfig;
  routing?: RoutingConfig;
  policy?: PolicyConfig;
  inbounds?: Inbound[];
  outbounds?: Outbound[];
  stats?: Record<string, unknown>;
  fakedns?: FakednsPool | FakednsPool[];
  metrics?: Record<string, unknown>;
  observatory?: Record<string, unknown>;
  burstObservatory?: Record<string, unknown>;
  reverse?: ReverseConfig;
  transport?: Record<string, unknown>;
  // Unknown top-level keys are preserved.
  [key: string]: unknown;
}

export interface LogConfig {
  loglevel?: 'debug' | 'info' | 'warning' | 'error' | 'none';
  access?: string;
  error?: string;
  dnsLog?: boolean;
  [key: string]: unknown;
}

export interface ApiConfig {
  tag?: string;
  services?: string[];
  listen?: string;
  [key: string]: unknown;
}

export interface Inbound {
  tag?: string;
  listen?: string;
  port?: number | string;
  protocol: string;
  settings?: Record<string, unknown>;
  streamSettings?: StreamSettings;
  sniffing?: SniffingConfig;
  allocate?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Outbound {
  tag?: string;
  protocol: string;
  sendThrough?: string;
  settings?: Record<string, unknown>;
  streamSettings?: StreamSettings;
  proxySettings?: Record<string, unknown>;
  mux?: MuxConfig;
  [key: string]: unknown;
}

export interface MuxConfig {
  enabled?: boolean;
  concurrency?: number;
  xudpConcurrency?: number;
  xudpProxyUDP443?: string;
  [key: string]: unknown;
}

export interface SniffingConfig {
  enabled?: boolean;
  destOverride?: string[];
  metadataOnly?: boolean;
  routeOnly?: boolean;
  [key: string]: unknown;
}

export interface StreamSettings {
  network?: string;
  security?: string;
  tlsSettings?: Record<string, unknown>;
  realitySettings?: Record<string, unknown>;
  tcpSettings?: Record<string, unknown>;
  wsSettings?: Record<string, unknown>;
  grpcSettings?: Record<string, unknown>;
  httpupgradeSettings?: Record<string, unknown>;
  xhttpSettings?: Record<string, unknown>;
  kcpSettings?: Record<string, unknown>;
  sockopt?: Record<string, unknown>;
  finalmask?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface RoutingConfig {
  domainStrategy?: 'AsIs' | 'IPIfNonMatch' | 'IPOnDemand';
  domainMatcher?: 'linear' | 'mph';
  rules?: RoutingRule[];
  balancers?: Balancer[];
  [key: string]: unknown;
}

export interface RoutingRule {
  type?: string;
  ruleTag?: string;
  domain?: string[];
  ip?: string[];
  port?: string | number;
  sourcePort?: string | number;
  network?: string;
  source?: string[];
  user?: string[];
  inboundTag?: string[];
  protocol?: string[];
  attrs?: Record<string, unknown> | string;
  outboundTag?: string;
  balancerTag?: string;
  [key: string]: unknown;
}

export interface Balancer {
  tag: string;
  selector?: string[];
  fallbackTag?: string;
  strategy?: { type?: string; settings?: Record<string, unknown> };
  [key: string]: unknown;
}

export interface DnsConfig {
  servers?: Array<string | DnsServerObject>;
  hosts?: Record<string, string | string[]>;
  clientIp?: string;
  queryStrategy?: 'UseIP' | 'UseIPv4' | 'UseIPv6';
  disableCache?: boolean;
  disableFallback?: boolean;
  disableFallbackIfMatch?: boolean;
  tag?: string;
  [key: string]: unknown;
}

export interface DnsServerObject {
  address: string;
  port?: number;
  domains?: string[];
  expectIPs?: string[];
  skipFallback?: boolean;
  clientIp?: string;
  [key: string]: unknown;
}

export interface PolicyConfig {
  levels?: Record<string, Record<string, unknown>>;
  system?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ReverseConfig {
  bridges?: Array<{ tag: string; domain: string }>;
  portals?: Array<{ tag: string; domain: string }>;
  [key: string]: unknown;
}

export interface FakednsPool {
  ipPool: string;
  poolSize: number;
  [key: string]: unknown;
}

// Sections that the section list renders (with presence / count).
export type ConfigSectionKey =
  | 'log' | 'api' | 'dns' | 'routing' | 'policy'
  | 'inbounds' | 'outbounds' | 'stats' | 'fakedns' | 'metrics'
  | 'observatory' | 'burstObservatory' | 'reverse' | 'transport';
