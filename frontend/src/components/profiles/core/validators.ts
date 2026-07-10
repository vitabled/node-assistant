// ============================================================
// Config validators (ajv) — node-installer «Профили»
//
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// The original ran Zod `.safeParse` per section; this port compiles the ajv
// schemas from core/schema.ts once and layers the same cross-reference / UI
// sanity checks on top. Messages are surfaced in Russian in the UI.
// ============================================================

import Ajv, { type ValidateFunction } from 'ajv';
import {
  xrayConfigSchema, inboundSchema, outboundSchema, balancerSchema,
} from './schema';
import type { XrayConfig } from './types';

// No schema uses a `format` keyword, so ajv-formats would be dead weight.
const ajv = new Ajv({ allErrors: true, strict: false, allowUnionTypes: true });

const vFull:     ValidateFunction = ajv.compile(xrayConfigSchema);
const vInbound:  ValidateFunction = ajv.compile(inboundSchema);
const vOutbound: ValidateFunction = ajv.compile(outboundSchema);

export interface ValidationError {
  field: string;
  message: string;
  // ajv keyword (e.g. 'enum') — lets the UI down-rank closed-enum violations to
  // warnings instead of hard sync-blockers (our enum lists may lag upstream Xray).
  keyword?: string;
}

// ── small address / port helpers (regex — no `validator` npm dependency) ──
const IPV4 = /^(\d{1,3}\.){3}\d{1,3}$/;
const IPV6 = /^[0-9a-fA-F:]+$/;
const DOMAIN = /^(?:[a-zA-Z0-9_](?:[a-zA-Z0-9_-]{0,61}[a-zA-Z0-9_])?\.)*[a-zA-Z0-9_](?:[a-zA-Z0-9_-]{0,61}[a-zA-Z0-9_])?$/;

export const isValidIP = (ip: string): boolean =>
  (IPV4.test(ip) && ip.split('.').every(o => Number(o) <= 255)) || (ip.includes(':') && IPV6.test(ip));

export const isValidDomain = (d: string): boolean => !!d && DOMAIN.test(d);

export const isValidAddress = (a: string): boolean => isValidIP(a) || isValidDomain(a);

export const isValidPort = (port: number | string): boolean => {
  const p = typeof port === 'string' ? parseInt(port, 10) : port;
  if (!p || isNaN(p)) return false;
  return p > 0 && p <= 65535;
};

function ajvErrors(errors: ValidateFunction['errors'], skip: (field: string) => boolean = () => false): ValidationError[] {
  if (!errors) return [];
  const out: ValidationError[] = [];
  for (const e of errors) {
    const field = (e.instancePath || '').replace(/^\//, '').replace(/\//g, '.');
    if (skip(field)) continue;
    out.push({ field: field || '(root)', message: e.message || 'invalid', keyword: e.keyword });
  }
  return out;
}

// ── section validators ─────────────────────────────────────────
export const validateInbound = (data: any): ValidationError[] => {
  const errors: ValidationError[] = [];
  if (!data.tag) errors.push({ field: 'tag', message: 'Требуется tag' });
  if (!data.protocol) errors.push({ field: 'protocol', message: 'Требуется protocol' });
  if (data.protocol !== 'tun' && !isValidPort(data.port)) {
    errors.push({ field: 'port', message: 'Некорректный порт' });
  }
  vInbound(data);
  // Skip ajv duplicates of the manual tag/port checks; KEEP the protocol enum
  // error (a present-but-unknown protocol has no manual message to dedupe).
  errors.push(...ajvErrors(vInbound.errors, f => f === 'tag' || f === 'port'));
  return errors;
};

export const validateOutbound = (data: any): ValidationError[] => {
  const errors: ValidationError[] = [];
  if (!data.tag) errors.push({ field: 'tag', message: 'Требуется tag' });
  vOutbound(data);
  errors.push(...ajvErrors(vOutbound.errors, f => f === 'tag'));

  const settings = data.settings || {};
  const proxyProtocols = ['vless', 'vmess', 'trojan', 'shadowsocks', 'shadowsocks-2022', 'socks', 'http', 'hysteria', 'hysteria2'];
  if (proxyProtocols.includes(data.protocol)) {
    let address = '', port = 0;
    if (settings.vnext?.[0]) { address = settings.vnext[0].address; port = settings.vnext[0].port; }
    else if (settings.servers?.[0]) { address = settings.servers[0].address; port = settings.servers[0].port; }
    else if (settings.address) { address = settings.address; port = settings.port; }
    if (!address || !isValidAddress(address)) errors.push({ field: 'address', message: 'Некорректный адрес сервера' });
    if (!port || !isValidPort(port)) errors.push({ field: 'port', message: 'Некорректный порт сервера' });
  }

  // Stream-level custom rules (reality / xhttp) — matches the original logic.
  const stream = data.streamSettings || {};
  if (stream.security === 'reality') {
    const r = stream.realitySettings || {};
    if (!r.publicKey) errors.push({ field: 'reality', message: 'Reality: требуется publicKey' });
    if (r.shortId && String(r.shortId).length % 2 !== 0) {
      errors.push({ field: 'reality', message: 'ShortID должен быть hex-строкой чётной длины' });
    }
  }
  if (stream.network === 'xhttp' && stream.xhttpSettings?.mode === 'stream-up' && stream.security === 'none') {
    errors.push({ field: 'xhttp', message: 'Режим stream-up рассчитан на TLS/REALITY.' });
  }
  return errors;
};

// A balancer with no selectors will crash the node — critical push blocker.
export const validateBalancer = (balancer: any): string[] => {
  if (balancer.tag === 'TORRENT') return [];
  const errors: string[] = [];
  if (!balancer.tag) errors.push('У балансировщика отсутствует tag');
  if (!balancer.selector || balancer.selector.length === 0) {
    errors.push(`У балансировщика [${balancer.tag}] нет селекторов`);
  }
  vBalancer(balancer);
  // Surface ajv structural errors too (e.g. selector of the wrong type) — the
  // manual checks above only cover a missing tag / empty selector.
  for (const e of ajvErrors(vBalancer.errors)) {
    errors.push(`[${balancer.tag || '?'}] ${e.field}: ${e.message}`);
  }
  return errors;
};
const vBalancer: ValidateFunction = ajv.compile(balancerSchema);

export const getCriticalRuleErrors = (rule: any): ValidationError[] => {
  const errs: ValidationError[] = [];
  const hasMatcher =
    rule.domain || rule.ip || rule.port || rule.sourcePort ||
    rule.network || rule.source || rule.user || rule.inboundTag ||
    rule.protocol || rule.attrs;
  if (!hasMatcher) errs.push({ field: 'matchers', message: 'В правиле нет условий (matchers).' });
  if (!rule.outboundTag && !rule.balancerTag) errs.push({ field: 'target', message: 'У правила нет назначения.' });
  return errs;
};
export const validateRule = (rule: any): ValidationError[] => getCriticalRuleErrors(rule);

// ── full-config validation (root schema + per-item + routing) ──
export const validateFullConfig = (config: any): ValidationError[] => {
  const errors: ValidationError[] = [];
  if (!config || typeof config !== 'object') {
    errors.push({ field: 'config', message: 'Конфиг должен быть объектом' });
    return errors;
  }
  vFull(config as XrayConfig);
  errors.push(...ajvErrors(vFull.errors));

  if (Array.isArray(config.inbounds)) {
    config.inbounds.forEach((inb: any, i: number) => {
      validateInbound(inb).forEach(err =>
        errors.push({ field: `inbounds[${i}].${err.field}`, message: err.message, keyword: err.keyword }));
    });
  }
  if (Array.isArray(config.outbounds)) {
    config.outbounds.forEach((ob: any, i: number) => {
      validateOutbound(ob).forEach(err =>
        errors.push({ field: `outbounds[${i}].${err.field}`, message: err.message, keyword: err.keyword }));
    });
  }
  const routing = config.routing || {};
  (routing.rules || []).forEach((rule: any, i: number) => {
    validateRule(rule).forEach(err =>
      errors.push({ field: `routing.rules[${i}].${err.field}`, message: err.message }));
  });
  (routing.balancers || []).forEach((b: any, i: number) => {
    validateBalancer(b).forEach(msg =>
      errors.push({ field: `routing.balancers[${i}]`, message: msg }));
  });
  return errors;
};
