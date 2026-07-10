// ============================================================
// Key / id generators — node-installer «Профили»
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// X25519 keypair for REALITY via tweetnacl (url-safe base64, no padding).
// ============================================================

import nacl from 'tweetnacl';

// All ids below feed REALITY (shortId / spiderX) or serve as UUIDs — security
// material, so they MUST come from a CSPRNG, never Math.random (predictable).
const randomBytes = (n: number): Uint8Array => {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return b;
};

export const generateUUID = (): string => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  // RFC-4122 v4 from CSPRNG bytes (fallback for older WebViews).
  const b = randomBytes(16);
  b[6] = (b[6] & 0x0f) | 0x40; // version 4
  b[8] = (b[8] & 0x3f) | 0x80; // variant 10
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0'));
  return `${h.slice(0, 4).join('')}-${h.slice(4, 6).join('')}-${h.slice(6, 8).join('')}-${h.slice(8, 10).join('')}-${h.slice(10, 16).join('')}`;
};

export const generateShortId = (length = 8): string => {
  const chars = '0123456789abcdef';
  const bytes = randomBytes(length);
  let result = '';
  for (let i = 0; i < length; i++) result += chars[bytes[i] & 0x0f]; // 4-bit → hex, no bias
  return result;
};

export const generateRealityKeyPair = (): { privateKey: string; publicKey: string } => {
  const keypair = nacl.box.keyPair();
  const encode = (bytes: Uint8Array) =>
    btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return { privateKey: encode(keypair.secretKey), publicKey: encode(keypair.publicKey) };
};

export const generateRealitySpiderX = (): string => {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  const len = 4 + (randomBytes(1)[0] % 5);
  const bytes = randomBytes(len);
  let result = '/';
  for (let i = 0; i < len; i++) result += chars[bytes[i] % chars.length];
  return result;
};

export const generateRealityShortIds = (count = 1): string[] =>
  Array.from({ length: count }, () => generateShortId(randomBytes(1)[0] & 1 ? 8 : 16));
