// ============================================================
// Config store (Zustand + Immer) — node-installer «Профили»
//
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// Holds the working Xray config, section CRUD, dirty tracking and import/export.
// Persistence is PER-ACCOUNT (localStorage `xray_profile_<accountId>`) — the
// original used a single global zustand-persist key; here we hydrate/persist
// manually so switching accounts loads that account's own draft. Remnawave sync
// is intentionally NOT a direct browser→panel call (see Profiles.tsx TODO).
// ============================================================

import { create } from 'zustand';
import { produce } from 'immer';
import { getActiveId } from '../../../auth/store';
import { validateFullConfig } from '../core/validators';
import type { XrayConfig, Inbound, Outbound, RoutingRule } from '../core/types';

type ListSection = 'inbounds' | 'outbounds';

function storageKey(id: string | null = getActiveId()): string {
  return `xray_profile_${id ?? 'none'}`;
}

function persist(config: XrayConfig | null) {
  try {
    if (config == null) localStorage.removeItem(storageKey());
    else localStorage.setItem(storageKey(), JSON.stringify(config));
  } catch { /* quota / private mode — non-fatal */ }
}

export interface ConfigStoreState {
  config: XrayConfig | null;
  dirty: boolean;

  hydrate: () => void;                 // (re)load the active account's draft
  setConfig: (config: XrayConfig | null) => void;
  loadConfig: (json: unknown) => { ok: boolean; warnings: number };
  clearConfig: () => void;
  markSaved: () => void;

  updateSection: (section: keyof XrayConfig, data: unknown) => void;
  toggleSection: (section: keyof XrayConfig, defaultValue: unknown) => void;
  deleteSection: (section: keyof XrayConfig) => void;

  addItem: (section: ListSection, item: Inbound | Outbound) => void;
  updateItem: (section: ListSection, index: number, item: Inbound | Outbound) => void;
  deleteItem: (section: ListSection, index: number) => void;
  moveItem: (section: ListSection, fromIndex: number, toIndex: number) => void;
  addOutbounds: (items: Outbound[]) => void;

  reorderRules: (rules: RoutingRule[]) => void;
  initDns: () => void;
}

export const useConfigStore = create<ConfigStoreState>((set, get) => {
  const commit = (mutator: (state: ConfigStoreState) => void, markDirty = true) =>
    set(state => {
      // dirty must be set INSIDE produce — immer auto-freezes the result.
      const next = produce(state, draft => { mutator(draft as ConfigStoreState); if (markDirty) draft.dirty = true; });
      persist(next.config);
      return next;
    });

  return {
    config: null,
    dirty: false,

    hydrate: () => {
      try {
        const raw = localStorage.getItem(storageKey());
        set({ config: raw ? JSON.parse(raw) : null, dirty: false });
      } catch {
        set({ config: null, dirty: false });
      }
    },

    setConfig: (config) => commit(s => { s.config = config; }),

    // Loads external JSON. ajv errors are warnings only — the config still loads
    // (advanced/unknown keys must not block the operator), diagnostics surface it.
    loadConfig: (json) => {
      const errors = validateFullConfig(json);
      set(produce((s: ConfigStoreState) => { s.config = json as XrayConfig; s.dirty = false; }));
      persist(json as XrayConfig);
      return { ok: errors.length === 0, warnings: errors.length };
    },

    clearConfig: () => { persist(null); set({ config: null, dirty: false }); },

    markSaved: () => set({ dirty: false }),

    updateSection: (section, data) => commit(s => {
      if (!s.config) s.config = { inbounds: [], outbounds: [] } as XrayConfig;
      if (data !== undefined) (s.config as any)[section] = data;
    }),

    toggleSection: (section, defaultValue) => commit(s => {
      if (!s.config) return;
      if ((s.config as any)[section]) delete (s.config as any)[section];
      else (s.config as any)[section] = defaultValue;
    }),

    deleteSection: (section) => commit(s => {
      if (s.config) delete (s.config as any)[section];
    }),

    addItem: (section, item) => commit(s => {
      if (!s.config) s.config = { inbounds: [], outbounds: [] } as XrayConfig;
      (s.config as any)[section] = (s.config as any)[section] || [];
      (s.config as any)[section].push(item);
    }),

    updateItem: (section, index, item) => commit(s => {
      if (s.config && (s.config as any)[section]) (s.config as any)[section][index] = item;
    }),

    deleteItem: (section, index) => commit(s => {
      if (s.config && (s.config as any)[section]) (s.config as any)[section].splice(index, 1);
    }),

    moveItem: (section, fromIndex, toIndex) => commit(s => {
      const list = s.config && (s.config as any)[section];
      if (!list || toIndex < 0 || toIndex >= list.length) return;
      const [moved] = list.splice(fromIndex, 1);
      list.splice(toIndex, 0, moved);
    }),

    // Import a batch of outbounds, de-duplicating tags (matches the original).
    addOutbounds: (items) => commit(s => {
      if (!s.config) s.config = { inbounds: [], outbounds: [] } as XrayConfig;
      s.config.outbounds = s.config.outbounds || [];
      const existing = new Set((s.config.outbounds || []).map((o: any) => o.tag));
      for (const item of items) {
        let tag = (item as any).tag || `${item.protocol}-${Math.floor(Math.random() * 1000)}`;
        if (existing.has(tag)) tag = `${tag}-${Math.random().toString(36).substring(2, 5)}`;
        existing.add(tag);
        s.config.outbounds.push({ ...item, tag });
      }
    }),

    reorderRules: (rules) => commit(s => {
      if (!s.config) return;
      if (!s.config.routing) s.config.routing = { rules: [], balancers: [] };
      s.config.routing.rules = rules;
    }),

    initDns: () => commit(s => {
      if (s.config && !s.config.dns) {
        s.config.dns = { servers: ['1.1.1.1', '8.8.8.8', 'localhost'], queryStrategy: 'UseIP', tag: 'dns_inbound' };
      }
    }),
  };
});
