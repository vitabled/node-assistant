import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeftRight, Loader2, Plus, Trash2, Waypoints, X } from "lucide-react";
import { Page, PageHeader } from "../../theme/ui";

/**
 * Мосты (Wave-4 PR-6): маршрутизация трафика выбранных инбаундов через
 * ноду-выход. Backend пишет outbound + routing-правило в config-профили
 * Remnawave через служебного пользователя nai-bridge (бессрочный, безлимит).
 */

interface Inbound { uuid: string; tag: string; type: string; network?: string; security?: string; port?: number }
interface NodeOpt {
  uuid: string; name: string; address: string; countryCode?: string; isDisabled?: boolean;
  profileUuid?: string; inbounds: Inbound[];
}
interface ProfileOpt { uuid: string; name: string }
interface Bridge {
  id: string; name: string;
  exit_node: { uuid: string; name: string; address: string };
  outbound_matched: boolean;
  inbound_tags: string[]; profile_uuids: string[]; applied_profiles: string[];
  matchers: { domain?: string[]; ip?: string[]; protocol?: string[]; port?: string; network?: string };
  profile_errors?: { profile: string; error: string }[];
}

const PROTOCOLS = ["http", "tls", "quic", "bittorrent"];

export function Bridges() {
  const [bridges, setBridges] = useState<Bridge[]>([]);
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    fetch("/api/bridges").then(r => r.json())
      .then(d => setBridges(d.bridges || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const del = async (id: string) => {
    if (!window.confirm("Удалить мост? Outbound и правило будут убраны из профилей панели.")) return;
    await fetch(`/api/bridges/${id}`, { method: "DELETE" });
    load();
  };

  return (
    <Page>
      <PageHeader icon={<Waypoints size={16} />} title="Мосты"
        subtitle="Маршрутизация трафика инбаундов через ноду-выход — правила записываются в config-профили Remnawave"
        actions={
          <button className="btn btn-primary" onClick={() => setFormOpen(true)}>
            <Plus size={13} /> Новый мост
          </button>
        } />

      {loading ? (
        <p style={{ fontSize: 12, color: "var(--t-faint)" }}><Loader2 size={14} className="spin" /></p>
      ) : bridges.length === 0 ? (
        <div className="card card-p" style={{ textAlign: "center", color: "var(--t-faint)", fontSize: 13 }}>
          Мостов пока нет. Создайте первый — сервис сам заведёт служебного
          пользователя в панели и запишет маршруты в профили.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {bridges.map(b => (
            <div key={b.id} className="card card-p" style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--t-hi)" }}>{b.name}</span>
                  {!b.outbound_matched && (
                    <span className="chip warn" title="Outbound ноды-выхода не нашёлся по адресу — взят первый проксёвый">outbound приблизительный</span>
                  )}
                  {(b.profile_errors?.length ?? 0) > 0 && (
                    <span className="chip err" title={b.profile_errors!.map(e => e.error).join("\n")}>
                      ошибки в {b.profile_errors!.length} профилях
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 11.5, color: "var(--t-low)", marginTop: 3, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                  <span>{(b.inbound_tags || []).length} инбаунд(ов)</span>
                  <ArrowLeftRight size={11} style={{ color: "var(--t-faint)" }} />
                  <span className="hi">{b.exit_node?.name || b.exit_node?.address}</span>
                  <span style={{ color: "var(--t-faint)" }}>· профилей: {(b.applied_profiles || []).length}</span>
                  {!!b.matchers?.domain?.length && (
                    <span style={{ color: "var(--t-faint)" }}>· доменов: {b.matchers.domain.length}</span>
                  )}
                </div>
              </div>
              <button className="iconbtn danger" title="Удалить мост" onClick={() => del(b.id)}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {formOpen && <BridgeFormModal onClose={() => setFormOpen(false)} onSaved={() => { setFormOpen(false); load(); }} />}
    </Page>
  );
}

// ── форма создания ─────────────────────────────────────────────
function BridgeFormModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [nodes, setNodes] = useState<NodeOpt[]>([]);
  const [profiles, setProfiles] = useState<ProfileOpt[]>([]);
  const [loadErr, setLoadErr] = useState("");

  const [name, setName] = useState("");
  const [entryNodeId, setEntryNodeId] = useState("");
  const [inboundTags, setInboundTags] = useState<string[]>([]);
  const [exitNodeId, setExitNodeId] = useState("");
  const [profileIds, setProfileIds] = useState<string[]>([]);
  const [domains, setDomains] = useState("");
  const [ips, setIps] = useState("");
  const [port, setPort] = useState("");
  const [network, setNetwork] = useState("");
  const [protocols, setProtocols] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetch("/api/bridges/options").then(async r => {
      const d = await r.json();
      if (!r.ok) { setLoadErr(typeof d.detail === "string" ? d.detail : `HTTP ${r.status}`); return; }
      setNodes(d.nodes || []);
      setProfiles(d.profiles || []);
    }).catch(() => setLoadErr("Панель недоступна"));
  }, []);

  const entryNode = useMemo(() => nodes.find(n => n.uuid === entryNodeId), [nodes, entryNodeId]);

  const toggleArr = (arr: string[], v: string) =>
    arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v];

  const save = async () => {
    setBusy(true); setErr("");
    const res = await fetch("/api/bridges", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name, exit_node_uuid: exitNodeId,
        inbound_tags: inboundTags,
        profile_uuids: profileIds,
        matchers: {
          domain: domains.split("\n").map(s => s.trim()).filter(Boolean),
          ip: ips.split("\n").map(s => s.trim()).filter(Boolean),
          protocol: protocols, port: port.trim(), network,
        },
      }),
    });
    setBusy(false);
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
    onSaved();
  };

  const valid = exitNodeId && profileIds.length > 0;

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal max-w-lg">
        <div className="sticky top-0 flex items-center justify-between px-5 py-3.5 z-10"
          style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          <div className="flex items-center gap-2">
            <Waypoints size={14} style={{ color: "var(--accent-hi)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>Новый мост</h2>
          </div>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>

        <div className="p-5 flex flex-col gap-3 overflow-y-auto">
          {loadErr && <p className="errmsg">{loadErr}</p>}
          <div>
            <label className="label">Название</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)}
              placeholder="EU → DE выход" />
          </div>

          <div>
            <label className="label">Нода-вход (инбаунды которой маршрутизируем)</label>
            <select className="selectbox" value={entryNodeId}
              onChange={e => { setEntryNodeId(e.target.value); setInboundTags([]); }}>
              <option value="">— выбрать —</option>
              {nodes.map(n => <option key={n.uuid} value={n.uuid}>{n.name} ({n.address})</option>)}
            </select>
          </div>

          {entryNode && (
            <div>
              <label className="label">Инбаунды ноды-входа</label>
              {entryNode.inbounds.length === 0 ? (
                <p className="hint">У ноды нет активных инбаундов в конфиг-профиле.</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {entryNode.inbounds.map(i => (
                    <button key={i.uuid} type="button"
                      className={`chip ${inboundTags.includes(i.tag) ? "accent" : "neutral"}`}
                      style={{ cursor: "pointer" }}
                      onClick={() => setInboundTags(t => toggleArr(t, i.tag))}>
                      {i.tag}
                    </button>
                  ))}
                </div>
              )}
              <p className="hint">Пусто = правило матчит весь трафик профиля, а не выбранные инбаунды.</p>
            </div>
          )}

          <div>
            <label className="label">Нода-выход (через неё уйдёт трафик)</label>
            <select className="selectbox" value={exitNodeId} onChange={e => setExitNodeId(e.target.value)}>
              <option value="">— выбрать —</option>
              {nodes.filter(n => !n.isDisabled).map(n => (
                <option key={n.uuid} value={n.uuid}>{n.name} ({n.address})</option>
              ))}
            </select>
          </div>

          <div>
            <label className="label">Конфиг-профили, куда записать маршруты</label>
            <div className="flex flex-wrap gap-1.5">
              {profiles.map(p => (
                <button key={p.uuid} type="button"
                  className={`chip ${profileIds.includes(p.uuid) ? "accent" : "neutral"}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => setProfileIds(t => toggleArr(t, p.uuid))}>
                  {p.name}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">Домены (по строке)</label>
              <textarea className="input" rows={3} value={domains} onChange={e => setDomains(e.target.value)}
                placeholder={"doubleclick.net\ndomain:ads.example\ngeosite:category-ads-all"} />
            </div>
            <div>
              <label className="label">IP / CIDR (по строке)</label>
              <textarea className="input" rows={3} value={ips} onChange={e => setIps(e.target.value)}
                placeholder={"10.0.0.0/8\ngeoip:private"} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">Порт (53,443,1000-2000)</label>
              <input className="input" value={port} onChange={e => setPort(e.target.value)} placeholder="443" />
            </div>
            <div>
              <label className="label">Сеть</label>
              <select className="selectbox" value={network} onChange={e => setNetwork(e.target.value)}>
                <option value="">любая</option>
                <option value="tcp">tcp</option>
                <option value="udp">udp</option>
                <option value="tcp,udp">tcp,udp</option>
              </select>
            </div>
          </div>
          <div>
            <label className="label">Протоколы (sniffing)</label>
            <div className="flex gap-1.5">
              {PROTOCOLS.map(p => (
                <button key={p} type="button"
                  className={`chip ${protocols.includes(p) ? "accent" : "neutral"}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => setProtocols(t => toggleArr(t, p))}>
                  {p}
                </button>
              ))}
            </div>
          </div>

          <div className="px-3 py-2.5 rounded-lg border text-xs leading-relaxed"
            style={{ background: "var(--warn-dim)", borderColor: "var(--warn-line)", color: "var(--warn)" }}>
            Маршрут пишется в выбранные config-профили и затрагивает ВСЕХ их пользователей.
            Сервис заведёт в панели служебного пользователя nai-bridge (бессрочный, безлимитный).
          </div>
          {err && <p className="errmsg">{err}</p>}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3.5" style={{ borderTop: "1px solid var(--line-soft)" }}>
          <button type="button" className="btn" onClick={onClose}>Отмена</button>
          <button type="button" className="btn btn-primary" onClick={save} disabled={busy || !valid}>
            {busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Создать мост
          </button>
        </div>
      </div>
    </div>
  );
}
