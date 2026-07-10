// ============================================================
// Xray config JSON-Schema (ajv) — node-installer «Профили»
//
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// The original derived JSON-Schema from Zod via zod-to-json-schema; this port
// authors an equivalent, pragmatic ajv JSON-Schema directly (no Zod dependency).
// Every object is `additionalProperties: true` so unknown/advanced keys pass
// validation untouched — the schema catches STRUCTURAL mistakes (bad protocol
// name, out-of-range port, wrong network/security enum, non-array sections),
// which is what powers the diagnostics panel and the CodeMirror linter.
// ============================================================

export const PROTOCOLS = [
  'vless', 'vmess', 'trojan', 'shadowsocks', 'shadowsocks-2022', 'socks', 'http',
  'freedom', 'blackhole', 'dns', 'wireguard', 'loopback',
  'dokodemo-door', 'tunnel', 'tun', 'hysteria', 'hysteria2',
] as const;

export const NETWORKS = [
  'tcp', 'raw', 'kcp', 'mkcp', 'ws', 'http', 'h2', 'grpc', 'gun',
  'httpupgrade', 'xhttp', 'splithttp', 'quic', 'domainsocket', 'udp',
] as const;

export const SECURITIES = ['none', 'tls', 'reality', 'xtls'] as const;

type JSONSchema = Record<string, unknown>;

const portSchema: JSONSchema = {
  anyOf: [
    { type: 'integer', minimum: 0, maximum: 65535 },
    { type: 'string' }, // "443", "1000-2000", "$env" — kept lenient
  ],
};

const sniffingSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    enabled: { type: 'boolean' },
    destOverride: { type: 'array', items: { type: 'string' } },
    metadataOnly: { type: 'boolean' },
    routeOnly: { type: 'boolean' },
  },
};

const streamSettingsSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    network: { type: 'string', enum: NETWORKS as unknown as string[] },
    security: { type: 'string', enum: SECURITIES as unknown as string[] },
    tlsSettings: { type: 'object', additionalProperties: true },
    realitySettings: { type: 'object', additionalProperties: true },
    sockopt: { type: 'object', additionalProperties: true },
    finalmask: { type: 'object', additionalProperties: true },
  },
};

export const inboundSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  required: ['protocol'],
  properties: {
    tag: { type: 'string' },
    listen: { type: 'string' },
    port: portSchema,
    protocol: { type: 'string', enum: PROTOCOLS as unknown as string[] },
    settings: { type: 'object', additionalProperties: true },
    streamSettings: streamSettingsSchema,
    sniffing: sniffingSchema,
    allocate: { type: 'object', additionalProperties: true },
  },
};

export const outboundSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  required: ['protocol'],
  properties: {
    tag: { type: 'string' },
    protocol: { type: 'string', enum: PROTOCOLS as unknown as string[] },
    sendThrough: { type: 'string' },
    settings: { type: 'object', additionalProperties: true },
    streamSettings: streamSettingsSchema,
    proxySettings: { type: 'object', additionalProperties: true },
    mux: { type: 'object', additionalProperties: true },
  },
};

export const routingRuleSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    type: { type: 'string' },
    ruleTag: { type: 'string' },
    domain: { type: 'array', items: { type: 'string' } },
    ip: { type: 'array', items: { type: 'string' } },
    port: portSchema,
    sourcePort: portSchema,
    network: { type: 'string' },
    source: { type: 'array', items: { type: 'string' } },
    user: { type: 'array', items: { type: 'string' } },
    inboundTag: { type: 'array', items: { type: 'string' } },
    protocol: { type: 'array', items: { type: 'string' } },
    outboundTag: { type: 'string' },
    balancerTag: { type: 'string' },
  },
};

export const balancerSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  required: ['tag'],
  properties: {
    tag: { type: 'string' },
    selector: { type: 'array', items: { type: 'string' } },
    fallbackTag: { type: 'string' },
    strategy: { type: 'object', additionalProperties: true },
  },
};

export const routingSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    domainStrategy: { type: 'string', enum: ['AsIs', 'IPIfNonMatch', 'IPOnDemand'] },
    domainMatcher: { type: 'string', enum: ['linear', 'mph'] },
    rules: { type: 'array', items: routingRuleSchema },
    balancers: { type: 'array', items: balancerSchema },
  },
};

export const dnsSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    servers: {
      type: 'array',
      items: {
        anyOf: [
          { type: 'string' },
          { type: 'object', additionalProperties: true, required: ['address'] },
        ],
      },
    },
    hosts: { type: 'object', additionalProperties: true },
    clientIp: { type: 'string' },
    queryStrategy: { type: 'string', enum: ['UseIP', 'UseIPv4', 'UseIPv6'] },
    disableCache: { type: 'boolean' },
    tag: { type: 'string' },
  },
};

const logSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    loglevel: { type: 'string', enum: ['debug', 'info', 'warning', 'error', 'none'] },
    access: { type: 'string' },
    error: { type: 'string' },
    dnsLog: { type: 'boolean' },
  },
};

export const xrayConfigSchema: JSONSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    log: logSchema,
    api: { type: 'object', additionalProperties: true },
    dns: dnsSchema,
    routing: routingSchema,
    policy: { type: 'object', additionalProperties: true },
    inbounds: { type: 'array', items: inboundSchema },
    outbounds: { type: 'array', items: outboundSchema },
    stats: { type: 'object', additionalProperties: true },
    reverse: { type: 'object', additionalProperties: true },
  },
};

// Maps the SectionJsonModal / JsonEditor `schemaMode` to a schema object.
export type SchemaMode =
  | 'full' | 'inbound' | 'inbounds' | 'outbound' | 'outbounds'
  | 'rule' | 'routing' | 'dns' | 'balancer';

export function schemaForMode(mode: SchemaMode): JSONSchema {
  switch (mode) {
    case 'inbound':   return inboundSchema;
    case 'inbounds':  return { type: 'array', items: inboundSchema };
    case 'outbound':  return outboundSchema;
    case 'outbounds': return { type: 'array', items: outboundSchema };
    case 'rule':      return routingRuleSchema;
    case 'routing':   return routingSchema;
    case 'dns':       return dnsSchema;
    case 'balancer':  return balancerSchema;
    case 'full':
    default:          return xrayConfigSchema;
  }
}
