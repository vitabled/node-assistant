// ============================================================
// Config diagnostics — node-installer «Профили»
//
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// Pure object-graph checks (no schema library): protocol-level incompatibilities
// and dangling tag references that a JSON-schema can't express. Messages RU.
// ============================================================

import type { XrayConfig } from './types';

export type DiagnosticSeverity = 'critical' | 'warning' | 'info';

export interface Diagnostic {
  section: string;
  itemIndex?: number;
  field?: string;
  message: string;
  severity: DiagnosticSeverity;
  suggestion?: string;
}

export const runFullDiagnostics = (config: XrayConfig | null): Diagnostic[] => {
  const diagnostics: Diagnostic[] = [];
  if (!config) return diagnostics;

  const inbounds = config.inbounds || [];
  const outbounds = config.outbounds || [];
  const routing = config.routing || {};
  const rules = routing.rules || [];
  const balancers = routing.balancers || [];

  const allOutboundTags = new Set(outbounds.map((o: any) => o.tag).filter(Boolean));
  const allBalancerTags = new Set(balancers.map((b: any) => b.tag).filter(Boolean));
  const KNOWN_EXTERNAL_TAGS = new Set(['TORRENT', 'DIRECT', 'REJECT', 'BLOCK', 'DNS']);
  const allTargetTags = new Set([...allOutboundTags, ...allBalancerTags, ...KNOWN_EXTERNAL_TAGS]);

  const checkOutbound = (o: any, i: number) => {
    const stream = o.streamSettings || {};
    const net = stream.network || 'tcp';
    const sec = stream.security || 'none';

    if (net === 'grpc') {
      const grpc = stream.grpcSettings || {};
      if (!grpc.serviceName) {
        diagnostics.push({
          section: 'outbounds', itemIndex: i, field: 'grpcSettings', severity: 'critical',
          message: 'gRPC требует заданный "serviceName".',
          suggestion: 'Добавьте имя сервиса (например, "GunService").',
        });
      }
    }

    if (sec === 'reality') {
      const r = stream.realitySettings || {};
      if (!r.publicKey) {
        diagnostics.push({
          section: 'outbounds', itemIndex: i, field: 'realitySettings', severity: 'critical',
          message: 'REALITY требует "publicKey" для outbound.',
        });
      }
      if (!r.serverName) {
        diagnostics.push({
          section: 'outbounds', itemIndex: i, field: 'realitySettings', severity: 'warning',
          message: 'REALITY обычно требует "serverName" (SNI), совпадающий с назначением.',
        });
      }
    }

    const flow = (o.settings?.vnext?.[0]?.users?.[0]?.flow) || (o.settings?.users?.[0]?.flow);
    const mux = o.mux || {};
    if (flow === 'xtls-rprx-vision' && mux.enabled) {
      diagnostics.push({
        section: 'outbounds', itemIndex: i, field: 'mux', severity: 'critical',
        message: 'XTLS-Vision несовместим с Mux/XUDP.',
        suggestion: 'Отключите Mux для этого outbound, чтобы использовать flow Vision.',
      });
    }
    if (sec === 'reality' && mux.enabled) {
      diagnostics.push({
        section: 'outbounds', itemIndex: i, field: 'mux', severity: 'warning',
        message: 'Mux с REALITY не рекомендуется (влияет на отпечаток).',
        suggestion: 'Рассмотрите отключение Mux для Reality-outbound.',
      });
    }
    if (net === 'xhttp') {
      const x = stream.xhttpSettings || {};
      if (x.mode === 'stream-up' && sec === 'none') {
        diagnostics.push({
          section: 'outbounds', itemIndex: i, severity: 'critical',
          message: 'Режим XHTTP "stream-up" ОБЯЗАТЕЛЬНО требует TLS или REALITY.',
          suggestion: 'Включите Security или смените режим на "packet-up".',
        });
      }
    }
  };

  const checkInbound = (inb: any, i: number) => {
    const stream = inb.streamSettings || {};
    const sec = stream.security || 'none';
    if (sec === 'reality') {
      const r = stream.realitySettings || {};
      if (!r.dest || !r.privateKey) {
        diagnostics.push({
          section: 'inbounds', itemIndex: i, field: 'realitySettings', severity: 'critical',
          message: 'REALITY inbound требует "dest" и "privateKey".',
          suggestion: 'Настройте fallback-назначение и сгенерируйте приватный ключ.',
        });
      }
    }
    if (sec === 'tls') {
      const tls = stream.tlsSettings || {};
      if (!tls.certificates || tls.certificates.length === 0) {
        diagnostics.push({
          section: 'inbounds', itemIndex: i, field: 'tlsSettings', severity: 'critical',
          message: 'TLS inbound требует хотя бы один сертификат.',
        });
      }
    }
  };

  inbounds.forEach(checkInbound);
  outbounds.forEach(checkOutbound);

  rules.forEach((rule: any, i: number) => {
    if (rule.outboundTag && !allTargetTags.has(rule.outboundTag)) {
      diagnostics.push({
        section: 'routing', itemIndex: i, field: 'outboundTag', severity: 'critical',
        message: `Правило ссылается на неизвестный outbound: "${rule.outboundTag}"`,
      });
    }
    if (rule.balancerTag && !allTargetTags.has(rule.balancerTag)) {
      diagnostics.push({
        section: 'routing', itemIndex: i, field: 'balancerTag', severity: 'critical',
        message: `Правило ссылается на неизвестный балансировщик: "${rule.balancerTag}"`,
      });
    }
  });

  return diagnostics;
};
