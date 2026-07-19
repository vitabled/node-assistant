// ============================================================
// Share-link parser / generator — node-installer «Профили»
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// vless/vmess/ss/trojan/hysteria2 links ↔ Xray outbound; WireGuard .conf import;
// JSON-subscription extraction. Returns null on unparseable input.
// ============================================================

import type { Outbound } from './types';

// vmess:// is base64(JSON) — the base64 body is not a valid URL authority, so it
// is parsed separately (BEFORE `new URL`). Symmetric with generateXrayLink, which
// emits this exact shape.
const parseVmessLink = (link: string): Outbound | null => {
  const body = link.slice('vmess://'.length).split('#')[0].trim();
  let j: any;
  try {
    j = JSON.parse(atob(body.replace(/-/g, '+').replace(/_/g, '/')));
  } catch {
    return null;
  }
  if (!j || !j.add) return null;
  const net = j.net || 'tcp';
  const security = j.tls === 'reality' ? 'reality' : j.tls ? 'tls' : 'none';
  const out: any = {
    tag: j.ps || `vmess-${Math.floor(Math.random() * 1000)}`,
    protocol: 'vmess',
    settings: {
      vnext: [{
        address: j.add,
        port: parseInt(j.port) || 443,
        users: [{ id: j.id, email: 'generated@xray', security: j.scy || 'auto', alterId: parseInt(j.aid) || 0 }],
      }],
    },
    streamSettings: { network: net, security },
  };
  if (security === 'tls') {
    out.streamSettings.tlsSettings = {
      serverName: j.sni || j.host || j.add,
      fingerprint: j.fp || 'chrome',
      alpn: j.alpn ? String(j.alpn).split(',') : undefined,
    };
  }
  if (net === 'ws') out.streamSettings.wsSettings = { path: j.path || '/', headers: { Host: j.host || '' } };
  if (net === 'grpc') out.streamSettings.grpcSettings = { serviceName: j.path || '' };
  return out as Outbound;
};

export const parseXrayLink = (link: string): Outbound | null => {
  try {
    if (link.startsWith('vmess://')) return parseVmessLink(link);
    const url = new URL(link);
    let protocol = url.protocol.replace(':', '');
    if (protocol === 'ss') protocol = 'shadowsocks';

    const hashPart = link.includes('#') ? link.split('#')[1] : '';
    const tag = decodeURIComponent(hashPart);
    const query = Object.fromEntries(url.searchParams.entries());

    const baseOutbound: any = {
      tag: tag || `${protocol}-${Math.floor(Math.random() * 1000)}`,
      protocol,
      settings: {},
      streamSettings: { network: 'tcp', security: 'none' },
    };

    if (protocol === 'vless') {
      baseOutbound.settings = {
        vnext: [{
          address: url.hostname,
          port: parseInt(url.port) || 443,
          users: [{ id: url.username, email: 'generated@xray', flow: query.flow || '', encryption: query.encryption || 'none' }],
        }],
      };
    } else if (protocol === 'trojan') {
      baseOutbound.settings = {
        servers: [{ address: url.hostname, port: parseInt(url.port) || 443, password: url.username, email: 'generated@xray', level: 0 }],
      };
    } else if (protocol === 'shadowsocks') {
      let method = '', password = '', serverAddr = '', serverPort = 443;
      const linkBody = link.split('://')[1].split('#')[0];
      try {
        if (!linkBody.includes('@')) {
          const decoded = atob(linkBody.replace(/-/g, '+').replace(/_/g, '/'));
          if (decoded.includes('@')) {
            const [userInfo, hostPort] = decoded.split('@');
            const [m, p] = userInfo.split(':');
            method = m; password = p;
            if (hostPort.includes(':')) { const [h, port] = hostPort.split(':'); serverAddr = h; serverPort = parseInt(port); }
            else serverAddr = hostPort;
          }
        }
      } catch { /* not a full base64 link */ }

      if (!serverAddr) {
        const lastAtIndex = linkBody.lastIndexOf('@');
        if (lastAtIndex !== -1) {
          const userInfoRaw = linkBody.substring(0, lastAtIndex);
          const hostPortPart = linkBody.substring(lastAtIndex + 1);
          let decodedUserInfo = '';
          try { decodedUserInfo = atob(userInfoRaw.replace(/-/g, '+').replace(/_/g, '/')); }
          catch { decodedUserInfo = userInfoRaw; }
          if (decodedUserInfo.includes(':')) { const parts = decodedUserInfo.split(':'); method = parts[0]; password = parts.slice(1).join(':'); }
          const [hostPort] = hostPortPart.split('?');
          if (hostPort.includes(':')) { const hp = hostPort.split(':'); serverAddr = hp[0]; serverPort = parseInt(hp[1]); }
          else serverAddr = hostPort;
        }
      }
      baseOutbound.settings = {
        servers: [{ address: serverAddr || url.hostname, port: serverPort || parseInt(url.port) || 443, method: method || 'aes-256-gcm', password, uot: true }],
      };
    } else {
      throw new Error('Unsupported protocol');
    }

    const network = query.type || query.net || 'tcp';
    baseOutbound.streamSettings.network = network;
    if (query.security) baseOutbound.streamSettings.security = query.security;

    if (query.security === 'tls' || query.security === 'reality') {
      const tlsSettings: any = {
        serverName: query.sni || url.hostname,
        fingerprint: query.fp || 'chrome',
        alpn: query.alpn ? query.alpn.split(',') : undefined,
      };
      if (query.security === 'reality') {
        tlsSettings.publicKey = query.pbk;
        tlsSettings.shortId = query.sid;
        tlsSettings.spiderX = query.spx || query.path || query.serviceName || '/';
        baseOutbound.streamSettings.realitySettings = tlsSettings;
      } else {
        baseOutbound.streamSettings.tlsSettings = tlsSettings;
      }
    }

    if (network === 'ws') baseOutbound.streamSettings.wsSettings = { path: query.path || '/', headers: { Host: query.host || '' } };
    if (network === 'grpc') baseOutbound.streamSettings.grpcSettings = { serviceName: query.serviceName || '' };
    if (network === 'xhttp' || network === 'splithttp') {
      const settings = { path: query.path || '/', mode: query.mode || 'auto', host: query.host || '' };
      if (network === 'xhttp') baseOutbound.streamSettings.xhttpSettings = settings;
      else baseOutbound.streamSettings.splithttpSettings = settings;
    }

    return baseOutbound as Outbound;
  } catch (e) {
    console.error('Parse error:', e);
    return null;
  }
};

export const parseWireguardConfig = (text: string, mode: 'direct' | 'chained' = 'direct'): any => {
  const lines = text.split('\n');
  const config: any = { Interface: {}, Peers: [] as any[] };
  let currentSection = '';
  for (let line of lines) {
    line = line.trim();
    if (!line || line.startsWith('#')) continue;
    if (line.startsWith('[Interface]')) { currentSection = 'Interface'; continue; }
    if (line.startsWith('[Peer]')) { config.Peers.push({}); currentSection = 'Peer'; continue; }
    const parts = line.split('=');
    if (parts.length < 2) continue;
    const key = parts[0].trim();
    const value = parts.slice(1).join('=').trim();
    if (currentSection === 'Interface') config.Interface[key] = value;
    else if (currentSection === 'Peer') config.Peers[config.Peers.length - 1][key] = value;
  }
  if (!config.Interface.PrivateKey) return null;

  const outbound: any = {
    tag: 'wg-imported-' + Math.floor(Math.random() * 1000),
    protocol: 'wireguard',
    settings: {
      secretKey: config.Interface.PrivateKey,
      address: config.Interface.Address ? config.Interface.Address.split(',').map((s: string) => s.trim()) : [],
      mtu: config.Interface.MTU ? parseInt(config.Interface.MTU) : 1280,
      peers: config.Peers.map((p: any) => ({
        publicKey: p.PublicKey,
        endpoint: p.Endpoint,
        allowedIPs: p.AllowedIPs ? p.AllowedIPs.split(',').map((s: string) => s.trim()) : ['0.0.0.0/0', '::/0'],
        keepAlive: p.PersistentKeepalive ? parseInt(p.PersistentKeepalive) : 0,
      })),
    },
    streamSettings: { network: 'udp', security: 'none' },
  };

  const isAWG = config.Interface.Jc || config.Interface.Jmin || config.Interface.H1 || config.Interface.I1;
  if (isAWG) {
    const noise: any[] = [];
    const extractHex = (val: string) => { if (!val) return null; const m = val.match(/0x([0-9a-fA-F]+)/); return m ? m[1] : null; };
    const i1Hex = extractHex(config.Interface.I1); if (i1Hex) noise.push({ type: 'hex', packet: i1Hex, delay: '5-10' });
    const i2Hex = extractHex(config.Interface.I2); if (i2Hex) noise.push({ type: 'hex', packet: i2Hex, delay: '5-10' });
    const jc = parseInt(config.Interface.Jc) || 0;
    const jmin = parseInt(config.Interface.Jmin) || 40;
    const jmax = parseInt(config.Interface.Jmax) || 70;
    for (let i = 0; i < jc; i++) noise.push({ rand: `${jmin}-${jmax}`, delay: '5-15' });

    const isWARP = outbound.settings.peers.some((p: any) => p.endpoint?.includes('cloudflare') || p.endpoint?.includes('162.159.'));
    if (isWARP) outbound.settings.reserved = [0, 0, 0];
    else if (config.Interface.S1 || config.Interface.S2) outbound.settings.reserved = [parseInt(config.Interface.S1) || 0, parseInt(config.Interface.S2) || 0, 0];

    if (mode === 'direct') {
      outbound.streamSettings.network = 'raw';
      outbound.streamSettings.finalmask = { udp: [{ type: 'noise', settings: { noise } }] };
    } else {
      const noiseTag = outbound.tag + '-obfuscator';
      outbound.streamSettings.sockopt = { dialerProxy: noiseTag };
      const obfuscator = {
        tag: noiseTag, protocol: 'freedom', settings: {},
        streamSettings: { network: 'raw', finalmask: { udp: [{ type: 'noise', settings: { noise } }] } },
      };
      return { multiple: true, outbounds: [outbound, obfuscator] };
    }
  }
  return outbound;
};

export const parseJsonSubscription = (jsonText: string): any[] => {
  try {
    const data = JSON.parse(jsonText);
    const outbounds: any[] = [];
    const processConfig = (conf: any) => {
      if (!conf || typeof conf !== 'object') return;
      if (Array.isArray(conf.outbounds)) {
        const proxies = conf.outbounds.filter((o: any) => !['freedom', 'dns', 'blackhole', 'direct', 'block'].includes(o.protocol));
        proxies.forEach((proxy: any, idx: number) => {
          if (conf.remarks) { const baseTag = conf.remarks.trim(); proxy.tag = proxies.length > 1 ? `${baseTag}-${idx + 1}` : baseTag; }
          outbounds.push(proxy);
        });
        if (proxies.length === 0 && conf.outbounds.length > 0) outbounds.push(conf.outbounds[0]);
      } else if (conf.protocol && conf.settings) {
        outbounds.push(conf);
      }
    };
    if (Array.isArray(data)) data.forEach(processConfig);
    else processConfig(data);
    return outbounds;
  } catch {
    return [];
  }
};

export const generateXrayLink = (item: any): string => {
  if (!item) return '';
  const { protocol, settings, streamSettings, tag } = item;
  const stream = streamSettings || {};
  const security = stream.security || 'none';
  const network = stream.network || 'tcp';

  let address = 'YOUR_SERVER_IP';
  let port = item.port || 0;
  if (settings?.vnext?.[0]) { address = settings.vnext[0].address; port = settings.vnext[0].port; }
  else if (settings?.servers?.[0]) { address = settings.servers[0].address; port = settings.servers[0].port; }
  else if (settings?.address) { address = settings.address; port = settings.port || port; }

  const params = new URLSearchParams();
  if (security !== 'none') params.set('security', security);
  if (network !== 'tcp') params.set('type', network);

  const tls = security === 'tls' ? stream.tlsSettings : (security === 'reality' ? stream.realitySettings : null);
  if (tls) {
    if (tls.serverName) params.set('sni', tls.serverName);
    if (security === 'reality') {
      if (tls.publicKey) params.set('pbk', tls.publicKey);
      if (tls.shortId) params.set('sid', tls.shortId);
      if (tls.spiderX) params.set('spx', tls.spiderX);
    }
    if (tls.fingerprint) params.set('fp', tls.fingerprint);
  }

  if (network === 'ws') { const ws = stream.wsSettings || {}; if (ws.path) params.set('path', ws.path); if (ws.headers?.Host) params.set('host', ws.headers.Host); }
  else if (network === 'grpc') { const grpc = stream.grpcSettings || {}; if (grpc.serviceName) params.set('serviceName', grpc.serviceName); }
  else if (network === 'xhttp') { const x = stream.xhttpSettings || {}; if (x.path) params.set('path', x.path); if (x.host) params.set('host', x.host); if (x.mode) params.set('mode', x.mode); }

  params.set('sni', params.get('sni') || params.get('host') || '');

  const getCredentials = () => {
    if (settings?.clients?.[0]) return settings.clients[0].id || settings.clients[0].password;
    if (settings?.users?.[0]) return settings.users[0].password || settings.users[0].id || settings.users[0].auth;
    if (settings?.vnext?.[0]?.users?.[0]) return settings.vnext[0].users[0].id;
    if (settings?.servers?.[0]?.users?.[0]) return settings.servers[0].users[0].password;
    if (settings?.servers?.[0]?.password) return settings.servers[0].password;
    return settings?.password || settings?.secret || 'password';
  };
  const creds = getCredentials();

  if (protocol === 'vless') {
    const flow = settings?.clients?.[0]?.flow || settings?.vnext?.[0]?.users?.[0]?.flow;
    if (flow) params.set('flow', flow);
    return `vless://${creds}@${address}:${port}?${params.toString()}#${encodeURIComponent(tag || 'VLESS')}`;
  }
  if (protocol === 'vmess') {
    const vmessConfig = {
      v: '2', ps: tag || 'VMess', add: address, port, id: creds, aid: '0', scy: 'auto', net: network, type: 'none',
      host: params.get('host') || '', path: params.get('path') || '', tls: security === 'none' ? '' : security,
      sni: params.get('sni') || '', fp: params.get('fp') || '',
    };
    return `vmess://${btoa(JSON.stringify(vmessConfig))}`;
  }
  if (protocol === 'trojan') return `trojan://${creds}@${address}:${port}?${params.toString()}#${encodeURIComponent(tag || 'Trojan')}`;
  if (protocol === 'shadowsocks' || protocol === 'shadowsocks-2022') {
    const method = settings?.method || settings?.servers?.[0]?.method || 'aes-256-gcm';
    const userInfo = btoa(`${method}:${creds}`).replace(/=/g, '');
    return `ss://${userInfo}@${address}:${port}#${encodeURIComponent(tag || 'SS')}`;
  }
  if (protocol === 'hysteria' || protocol === 'hysteria2') return `hysteria2://${creds}@${address}:${port}?${params.toString()}#${encodeURIComponent(tag || 'Hysteria2')}`;
  return '';
};
