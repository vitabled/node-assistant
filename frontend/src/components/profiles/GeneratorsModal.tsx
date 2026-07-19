// ============================================================
// Generators & import modal — node-installer «Профили»
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// X25519 (REALITY) keypair, UUID, shortIds, WARP account, and import from a
// share-link (vless/vmess/ss/trojan) or a WireGuard .conf → outbound.
// ============================================================

import { useState } from 'react';
import { X, Copy, KeyRound, Zap, Link2, Loader2 } from 'lucide-react';
import { toast } from '../infra/Toast';
import { generateRealityKeyPair, generateUUID, generateRealityShortIds } from './core/crypto';
import { generateWarpAccount, warpToOutbound } from './core/warp';
import { parseXrayLink, parseWireguardConfig } from './core/links';
import { useConfigStore } from './store/configStore';

function copy(text: string) {
  navigator.clipboard?.writeText(text).then(
    () => toast('Скопировано', 'success'),
    () => toast('Не удалось скопировать', 'error'),
  );
}

function KeyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <div className="flex items-center gap-2">
        <input className="input font-mono text-xs" readOnly value={value} onFocus={e => e.target.select()} />
        <button className="iconbtn" onClick={() => copy(value)} title="Копировать"><Copy size={14} /></button>
      </div>
    </div>
  );
}

export function GeneratorsModal({ onClose }: { onClose: () => void }) {
  const addOutbounds = useConfigStore(s => s.addOutbounds);
  const [keys, setKeys] = useState<{ privateKey: string; publicKey: string } | null>(null);
  const [uuid, setUuid] = useState('');
  const [shortIds, setShortIds] = useState<string[]>([]);
  const [link, setLink] = useState('');
  const [wg, setWg] = useState('');
  const [warpBusy, setWarpBusy] = useState(false);

  const importLink = () => {
    const ob = parseXrayLink(link.trim());
    if (!ob) { toast('Не удалось разобрать ссылку', 'error'); return; }
    addOutbounds([ob]);
    toast(`Добавлен outbound "${ob.tag}"`, 'success');
    setLink('');
  };

  const importWg = () => {
    const res = parseWireguardConfig(wg);
    if (!res) { toast('Некорректный WireGuard-конфиг', 'error'); return; }
    const list = res.multiple ? res.outbounds : [res];
    addOutbounds(list);
    toast(`Импортировано outbound: ${list.length}`, 'success');
    setWg('');
  };

  const genWarp = async () => {
    setWarpBusy(true);
    try {
      const acc = await generateWarpAccount();
      addOutbounds([warpToOutbound(acc)]);
      toast('WARP-outbound добавлен', 'success');
    } catch (e) {
      toast((e as Error).message || 'Ошибка генерации WARP', 'error');
    } finally {
      setWarpBusy(false);
    }
  };

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal max-w-lg" style={{ maxHeight: '88vh', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 18px', borderBottom: '1px solid var(--line-soft)', flex: 'none' }}>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--t-hi)' }}>Генераторы и импорт</h2>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>

        <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 18, overflowY: 'auto' }}>
          {/* Reality keys */}
          <section style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <p className="micro" style={{ display: 'flex', alignItems: 'center', gap: 6 }}><KeyRound size={13} /> REALITY / ключи</p>
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-soft" onClick={() => setKeys(generateRealityKeyPair())}>X25519 keypair</button>
              <button className="btn btn-soft" onClick={() => setUuid(generateUUID())}>UUID</button>
              <button className="btn btn-soft" onClick={() => setShortIds(generateRealityShortIds(3))}>ShortIds ×3</button>
            </div>
            {keys && <><KeyRow label="Private key" value={keys.privateKey} /><KeyRow label="Public key" value={keys.publicKey} /></>}
            {uuid && <KeyRow label="UUID" value={uuid} />}
            {shortIds.length > 0 && <KeyRow label="ShortIds" value={shortIds.join(', ')} />}
          </section>

          {/* WARP */}
          <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <p className="micro" style={{ display: 'flex', alignItems: 'center', gap: 6 }}><Zap size={13} /> WARP</p>
            <p className="hint">Регистрирует устройство WARP через публичные воркеры и добавляет WireGuard-outbound. Может быть недоступно (внешние сервисы).</p>
            <button className="btn btn-soft" onClick={genWarp} disabled={warpBusy} style={{ alignSelf: 'flex-start' }}>
              {warpBusy ? <><Loader2 size={13} className="spin" /> Генерация…</> : 'Сгенерировать WARP-outbound'}
            </button>
          </section>

          {/* Import from link */}
          <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <p className="micro" style={{ display: 'flex', alignItems: 'center', gap: 6 }}><Link2 size={13} /> Импорт из ссылки</p>
            <input className="input font-mono text-xs" value={link} onChange={e => setLink(e.target.value)}
              placeholder="vless:// | vmess:// | ss:// | trojan://" spellCheck={false} />
            <button className="btn btn-soft" onClick={importLink} disabled={!link.trim()} style={{ alignSelf: 'flex-start' }}>Добавить outbound</button>
          </section>

          {/* Import WireGuard */}
          <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <p className="micro">Импорт WireGuard / AmneziaWG (.conf)</p>
            <textarea className="input font-mono text-xs" rows={5} value={wg} onChange={e => setWg(e.target.value)}
              placeholder="[Interface]&#10;PrivateKey = …&#10;[Peer]&#10;…" spellCheck={false} style={{ resize: 'vertical' }} />
            <button className="btn btn-soft" onClick={importWg} disabled={!wg.trim()} style={{ alignSelf: 'flex-start' }}>Импортировать</button>
          </section>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 18px', borderTop: '1px solid var(--line-soft)', flex: 'none' }}>
          <button onClick={onClose} className="btn btn-primary">Готово</button>
        </div>
      </div>
    </div>
  );
}
