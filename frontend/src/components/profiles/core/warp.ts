// ============================================================
// WARP account generator — node-installer «Профили»
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// Registers a WARP device via public generator workers and maps the result to
// a WireGuard outbound. Best-effort: the external endpoints may be offline /
// rate-limited (429) — surfaced as a thrown error the UI turns into a toast.
// ============================================================

import type { Outbound } from './types';

export interface WarpAccount {
  id: string;
  token: string;
  privateKey: string;
  publicKey: string;
  peerPublicKey: string;
  endpoint: string;
  ipv4: string;
  ipv6: string;
  reserved: number[];
}

const DEFAULT_WARP_ENDPOINTS = [
  'https://warp-vercel-murex.vercel.app/api/warp-data',
  'https://xcui.bropines.workers.dev/',
  'https://warp-vercel-chi.vercel.app/api/warp-data',
  'https://warp.sub-aggregator.workers.dev',
  'https://www.warp-generator.workers.dev',
];

export async function generateWarpAccount(customWorkerUrl?: string): Promise<WarpAccount> {
  const endpoints = customWorkerUrl ? [customWorkerUrl] : DEFAULT_WARP_ENDPOINTS;
  let lastError: unknown;

  for (const url of endpoints) {
    try {
      const isVercel = url.includes('vercel.app');
      const isBropines = url.includes('bropines');
      const method = (isVercel || isBropines) ? 'GET' : 'POST';
      const response = await fetch(url, { method, signal: AbortSignal.timeout(15000) });

      if (response.status === 429) throw new Error('Превышен лимит (429). Попробуйте позже или другой профиль.');
      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`Воркер вернул ${response.status}: ${errText.substring(0, 30)}`);
      }

      const data: any = await response.json();
      if (data.privKey && data.peer_pub) {
        return {
          id: data.id || '',
          token: data.token || '',
          privateKey: data.privKey,
          publicKey: '',
          peerPublicKey: data.peer_pub,
          endpoint: data.peer_endpoint || 'engage.cloudflareclient.com:2408',
          ipv4: data.client_ipv4,
          ipv6: data.client_ipv6,
          reserved: data.reserved || [0, 0, 0],
        };
      }
      throw new Error('Некорректный формат ответа воркера');
    } catch (e) {
      console.warn(`WARP endpoint failed ${url}:`, (e as Error).message);
      lastError = e;
    }
  }
  throw lastError instanceof Error ? lastError : new Error('Все точки регистрации WARP сейчас недоступны.');
}

// Maps a registered WARP account to a WireGuard outbound.
export function warpToOutbound(acc: WarpAccount, tag = 'warp'): Outbound {
  return {
    tag,
    protocol: 'wireguard',
    settings: {
      secretKey: acc.privateKey,
      address: [`${acc.ipv4}/32`, `${acc.ipv6}/128`].filter(a => a && !a.startsWith('/32') && !a.startsWith('/128')),
      peers: [{ publicKey: acc.peerPublicKey, endpoint: acc.endpoint, allowedIPs: ['0.0.0.0/0', '::/0'] }],
      reserved: acc.reserved,
      mtu: 1280,
    },
    streamSettings: { network: 'udp', security: 'none' },
  };
}
