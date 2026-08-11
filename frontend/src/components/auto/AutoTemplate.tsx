import { useEffect, useMemo, useState, type ReactNode } from "react";
import { FileJson, Loader2, Plus, Save, Trash2 } from "lucide-react";
import { Page, PageHeader, Seg } from "../../theme/ui";

/**
 * «Авто» (Wave-4 PR-7): структурный конфигуратор XRAY_JSON-шаблонов подписки
 * Remnawave. Форма покрывает документированные секции (xtls + docs.rw
 * xray-json-advanced): dns / routing (rules+balancers) / inbounds / outbounds /
 * burstObservatory / remnawave-директиву (injectHosts). Неформенные поля
 * проходят сквозным объектом (ничего не теряется), есть режим raw JSON.
 */

// ── документированные enum'ы (сверено с xtls llms-full + docs.rw) ──
const DOMAIN_STRATEGIES = ["AsIs", "IPIfNonMatch", "IPOnDemand"];
const DOMAIN_MATCHERS = ["mph", "hybrid"];      // mph — xtls, hybrid — docs.rw
const QUERY_STRATEGIES = ["UseIP", "UseIPv4", "UseIPv6", "UseSystem"];
const BALANCER_STRATEGIES = ["random", "roundRobin", "leastPing", "leastLoad"];
const NETWORKS = ["", "tcp", "udp", "tcp,udp"];
const RULE_PROTOCOLS = ["http", "tls", "quic", "bittorrent"];
const SELECTOR_TYPES = ["uuids", "remarkRegex", "tagRegex", "sameTagAsRecipient"];
const SELECT_FROM = ["HIDDEN", "NOT_HIDDEN", "ALL"];
const RAW_PROTOCOLS = ["vless", "trojan", "shadowsocks", "socks", "http", "freedom", "blackhole"];

const EMPTY_DOC = {
  remnawave: {},
  dns: { servers: ["1.1.1.1", "1.0.0.1"], queryStrategy: "UseIP" },
  routing: { domainStrategy: "AsIs", domainMatcher: "mph", rules: [], balancers: [] },
  inbounds: [],
  outbounds: [
    { tag: "direct", protocol: "freedom" },
    { tag: "block", protocol: "blackhole" },
  ],
};

type Doc = Record<string, any>;

const clone = (x: any) => JSON.parse(JSON.stringify(x));

// ── мелкие контролы ────────────────────────────────────────────
function Lines({ value, onChange, placeholder, rows = 3 }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string; rows?: number;
}) {
  return (
    <textarea className="input font-mono text-xs" rows={rows}
      value={(value || []).join("\n")}
      onChange={e => onChange(e.target.value.split("\n").map(s => s.trim()).filter(Boolean))}
      placeholder={placeholder} />
  );
}

function Csv({ value, onChange, placeholder }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string;
}) {
  return (
    <input className="input" value={(value || []).join(", ")} placeholder={placeholder}
      onChange={e => onChange(e.target.value.split(",").map(s => s.trim()).filter(Boolean))} />
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <span className="micro">{title}</span>
      {children}
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}

// ── главный компонент ──────────────────────────────────────────
export function AutoTemplate() {
  const [templates, setTemplates] = useState<{ uuid: string; name: string }[]>([]);
  const [sel, setSel] = useState("");           // uuid выбранного шаблона
  const [newName, setNewName] = useState("");
  const [doc, setDoc] = useState<Doc>(() => clone(EMPTY_DOC));
  const [mode, setMode] = useState<"form" | "json">("form");
  const [jsonText, setJsonText] = useState("");
  const [jsonErr, setJsonErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const loadList = () =>
    fetch("/api/xray-templates").then(r => r.json())
      .then(d => setTemplates(d.templates || [])).catch(() => {});
  useEffect(() => { loadList(); }, []);

  const set = (path: string, value: any) =>
    setDoc(d => {
      const next = clone(d);
      const keys = path.split(".");
      let cur = next;
      for (const k of keys.slice(0, -1)) cur = cur[k] ??= {};
      cur[keys[keys.length - 1]] = value;
      return next;
    });

  const loadTemplate = async (uuid: string) => {
    setSel(uuid); setMsg("");
    if (!uuid) { setDoc(clone(EMPTY_DOC)); return; }
    const r = await fetch(`/api/xray-templates/${uuid}`);
    const t = await r.json().catch(() => ({}));
    const content = t.templateJson ?? t.content ?? t.template ?? null;
    setDoc(content && typeof content === "object" ? clone(content) : clone(EMPTY_DOC));
  };

  const toJson = () => { setJsonText(JSON.stringify(doc, null, 2)); setJsonErr(""); setMode("json"); };
  const toForm = () => {
    try {
      const parsed = JSON.parse(jsonText);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("ожидается JSON-объект");
      setDoc(parsed); setJsonErr(""); setMode("form");
    } catch (e) { setJsonErr(`Ошибка JSON: ${(e as Error).message}`); }
  };

  const save = async () => {
    setBusy(true); setMsg("");
    try {
      let uuid = sel;
      if (!uuid) {
        const r = await fetch("/api/xray-templates", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newName.trim() || "Авто-шаблон" }),
        });
        const t = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(typeof t.detail === "string" ? t.detail : "Ошибка создания");
        uuid = t.uuid || t.template?.uuid;
        if (!uuid) throw new Error("Панель не вернула uuid шаблона");
        setSel(uuid);
      }
      const r2 = await fetch(`/api/xray-templates/${uuid}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_json: doc }),
      });
      const t2 = await r2.json().catch(() => ({}));
      if (!r2.ok) throw new Error(typeof t2.detail === "string" ? t2.detail : "Ошибка записи");
      setMsg("Сохранено в панели");
      loadList();
    } catch (e) { setMsg((e as Error).message); }
    finally { setBusy(false); }
  };

  const rw = doc.remnawave ?? {};
  const injectHosts: any[] = rw.injectHosts ?? [];
  const routing = doc.routing ?? {};
  const rules: any[] = routing.rules ?? [];
  const balancers: any[] = routing.balancers ?? [];
  const dns = doc.dns ?? {};
  const inbounds: any[] = doc.inbounds ?? [];
  const outbounds: any[] = doc.outbounds ?? [];
  const bo = doc.burstObservatory;

  const injectWarnings = useMemo(() => {
    const w: string[] = [];
    injectHosts.forEach((g, i) => {
      const modes = ["tagPrefix", "useHostRemarkAsTag", "useHostTagAsTag"]
        .filter(k => g?.[k] !== undefined && g?.[k] !== "" && g?.[k] !== false);
      if (modes.length !== 1) w.push(`injectHosts[${i}]: нужно ровно одно tag-поле (tagPrefix / useHostRemarkAsTag / useHostTagAsTag)`);
      const t = g?.selector?.type;
      if ((t === "remarkRegex" || t === "tagRegex") && !g?.selector?.pattern) w.push(`injectHosts[${i}]: selector.${t} требует pattern`);
      if (t === "uuids" && !(g?.selector?.values || []).length) w.push(`injectHosts[${i}]: selector.uuids требует values`);
    });
    return w;
  }, [injectHosts]);

  return (
    <Page max={860}>
      <PageHeader icon={<FileJson size={16} />} title="Авто"
        subtitle="Конфигуратор XRAY_JSON-шаблонов подписки (xray-json-advanced)"
        actions={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Seg mini options={[{ v: "form", l: "Форма" }, { v: "json", l: "JSON" }]}
              value={mode} onChange={(v: string) => (v === "json" ? toJson() : toForm())} />
            <button className="btn btn-primary" onClick={save}
              disabled={busy || (mode === "json" && !!jsonErr) || injectWarnings.length > 0}>
              {busy ? <Loader2 size={13} className="spin" /> : <Save size={13} />} Сохранить в панель
            </button>
          </div>
        } />

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        <select className="selectbox" style={{ width: 260 }} value={sel} onChange={e => loadTemplate(e.target.value)}>
          <option value="">— новый шаблон —</option>
          {templates.map(t => <option key={t.uuid} value={t.uuid}>{t.name}</option>)}
        </select>
        {!sel && (
          <input className="input" style={{ width: 240 }} placeholder="Имя нового шаблона"
            value={newName} onChange={e => setNewName(e.target.value)} />
        )}
        {msg && <span className="text-xs" style={{ color: msg === "Сохранено в панели" ? "var(--ok)" : "var(--err)" }}>{msg}</span>}
      </div>

      {mode === "json" ? (
        <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <textarea className="input font-mono text-xs" rows={26} value={jsonText}
            onChange={e => { setJsonText(e.target.value); setJsonErr(""); }} spellCheck={false} />
          {jsonErr && <p className="errmsg">{jsonErr}</p>}
          <p className="hint">«Форма» применит этот JSON обратно в структуру; ошибка подсвечивается при переключении.</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* ── remnawave-директива ── */}
          <Section title="Remnawave-директива"
            hint="Объект remnawave обрабатывается панелью и удаляется из итогового конфига (Remnawave ≥ 2.6.3).">
            <label className="flex items-center gap-2 text-xs" style={{ color: "var(--t-mid)" }}>
              <input type="checkbox" checked={!!rw.addVirtualHostAsOutbound}
                onChange={e => set("remnawave.addVirtualHostAsOutbound", e.target.checked)} />
              addVirtualHostAsOutbound — виртуальный хост тоже станет outbound «proxy»
            </label>

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {injectHosts.map((g, gi) => (
                <div key={gi} style={{ border: "1px solid var(--line-soft)", borderRadius: "var(--r-md)", padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="text-xs font-semibold" style={{ color: "var(--t-hi)" }}>injectHosts[{gi}]</span>
                    <button className="iconbtn danger" style={{ marginLeft: "auto" }}
                      onClick={() => set("remnawave.injectHosts", injectHosts.filter((_, j) => j !== gi))}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="label">selector.type</label>
                      <select className="selectbox" value={g.selector?.type || "uuids"}
                        onChange={e => set(`remnawave.injectHosts.${gi}.selector`, { type: e.target.value })}>
                        {SELECTOR_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="label">selectFrom</label>
                      <select className="selectbox" value={g.selectFrom || "HIDDEN"}
                        onChange={e => set(`remnawave.injectHosts.${gi}.selectFrom`, e.target.value)}>
                        {SELECT_FROM.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                  </div>
                  {(g.selector?.type || "uuids") === "uuids" && (
                    <div>
                      <label className="label">values — uuid хостов (по строке)</label>
                      <Lines value={g.selector?.values || []} rows={2}
                        placeholder="8478b271-95d3-4312-85ae-ecf63fb53d1d"
                        onChange={v => set(`remnawave.injectHosts.${gi}.selector.values`, v)} />
                    </div>
                  )}
                  {["remarkRegex", "tagRegex"].includes(g.selector?.type) && (
                    <div>
                      <label className="label">pattern (JavaScript RegExp)</label>
                      <input className="input font-mono text-xs" value={g.selector?.pattern || ""}
                        onChange={e => set(`remnawave.injectHosts.${gi}.selector.pattern`, e.target.value)}
                        placeholder="^RU-" />
                    </div>
                  )}
                  <div>
                    <label className="label">Теги outbound'ов (ровно одно)</label>
                    <div className="flex items-center gap-2 flex-wrap">
                      <label className="flex items-center gap-1 text-xs" style={{ color: "var(--t-mid)" }}>
                        <input type="radio" checked={!!g.tagPrefix && !g.useHostRemarkAsTag && !g.useHostTagAsTag}
                          onChange={() => set(`remnawave.injectHosts.${gi}`, { selector: g.selector, selectFrom: g.selectFrom ?? "HIDDEN", tagPrefix: "proxy" })} />
                        tagPrefix
                      </label>
                      <input className="input" style={{ width: 120 }} value={g.tagPrefix || ""}
                        onChange={e => set(`remnawave.injectHosts.${gi}`, { selector: g.selector, selectFrom: g.selectFrom ?? "HIDDEN", tagPrefix: e.target.value })} />
                      <label className="flex items-center gap-1 text-xs" style={{ color: "var(--t-mid)" }}>
                        <input type="radio" checked={!!g.useHostRemarkAsTag}
                          onChange={() => set(`remnawave.injectHosts.${gi}`, { selector: g.selector, selectFrom: g.selectFrom ?? "HIDDEN", useHostRemarkAsTag: true })} />
                        useHostRemarkAsTag
                      </label>
                      <label className="flex items-center gap-1 text-xs" style={{ color: "var(--t-mid)" }}>
                        <input type="radio" checked={!!g.useHostTagAsTag}
                          onChange={() => set(`remnawave.injectHosts.${gi}`, { selector: g.selector, selectFrom: g.selectFrom ?? "HIDDEN", useHostTagAsTag: true })} />
                        useHostTagAsTag
                      </label>
                    </div>
                  </div>
                </div>
              ))}
              <button type="button" className="btn btn-soft" style={{ alignSelf: "flex-start" }}
                onClick={() => set("remnawave.injectHosts", [...injectHosts, { selector: { type: "uuids", values: [] }, tagPrefix: "proxy" }])}>
                <Plus size={13} /> Группа injectHosts
              </button>
            </div>
            {injectWarnings.map(w => <p key={w} className="errmsg" style={{ marginTop: 0 }}>{w}</p>)}
          </Section>

          {/* ── routing ── */}
          <Section title="Routing">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">domainStrategy</label>
                <select className="selectbox" value={routing.domainStrategy || "AsIs"}
                  onChange={e => set("routing.domainStrategy", e.target.value)}>
                  {DOMAIN_STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label className="label">domainMatcher</label>
                <select className="selectbox" value={routing.domainMatcher || "mph"}
                  onChange={e => set("routing.domainMatcher", e.target.value)}>
                  {DOMAIN_MATCHERS.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
            </div>

            <span className="micro" style={{ marginTop: 4 }}>rules</span>
            {rules.map((r, ri) => (
              <div key={ri} style={{ border: "1px solid var(--line-soft)", borderRadius: "var(--r-md)", padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="text-xs font-semibold" style={{ color: "var(--t-hi)" }}>rules[{ri}]</span>
                  <div className="seg mini" style={{ marginLeft: "auto" }}>
                    <button type="button" className={r.balancerTag ? "" : "on"}
                      onClick={() => set(`routing.rules.${ri}`, { ...r, outboundTag: r.outboundTag || "direct", balancerTag: undefined })}>
                      outboundTag
                    </button>
                    <button type="button" className={r.balancerTag ? "on" : ""}
                      onClick={() => set(`routing.rules.${ri}`, { ...r, balancerTag: r.balancerTag || "balancer", outboundTag: undefined })}>
                      balancerTag
                    </button>
                  </div>
                  <button className="iconbtn danger" onClick={() => set("routing.rules", rules.filter((_, j) => j !== ri))}>
                    <Trash2 size={13} />
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {r.balancerTag !== undefined ? (
                    <div>
                      <label className="label">balancerTag</label>
                      <input className="input" value={r.balancerTag || ""}
                        onChange={e => set(`routing.rules.${ri}.balancerTag`, e.target.value)} />
                    </div>
                  ) : (
                    <div>
                      <label className="label">outboundTag</label>
                      <input className="input" value={r.outboundTag || ""}
                        onChange={e => set(`routing.rules.${ri}.outboundTag`, e.target.value)} />
                    </div>
                  )}
                  <div>
                    <label className="label">inboundTag (через запятую)</label>
                    <Csv value={r.inboundTag || []} onChange={v => set(`routing.rules.${ri}.inboundTag`, v)} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="label">domain (по строке)</label>
                    <Lines value={r.domain || []} rows={2} onChange={v => set(`routing.rules.${ri}.domain`, v)} />
                  </div>
                  <div>
                    <label className="label">ip (по строке)</label>
                    <Lines value={r.ip || []} rows={2} onChange={v => set(`routing.rules.${ri}.ip`, v)} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="label">port</label>
                    <input className="input" value={r.port || ""} placeholder="53,443,1000-2000"
                      onChange={e => set(`routing.rules.${ri}.port`, e.target.value)} />
                  </div>
                  <div>
                    <label className="label">network</label>
                    <select className="selectbox" value={r.network || ""}
                      onChange={e => set(`routing.rules.${ri}.network`, e.target.value || undefined)}>
                      {NETWORKS.map(n => <option key={n} value={n}>{n || "—"}</option>)}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="label">protocol</label>
                  <div className="flex gap-1.5">
                    {RULE_PROTOCOLS.map(p => (
                      <button key={p} type="button"
                        className={`chip ${(r.protocol || []).includes(p) ? "accent" : "neutral"}`}
                        style={{ cursor: "pointer" }}
                        onClick={() => set(`routing.rules.${ri}.protocol`,
                          (r.protocol || []).includes(p)
                            ? (r.protocol || []).filter((x: string) => x !== p)
                            : [...(r.protocol || []), p])}>
                        {p}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ))}
            <button type="button" className="btn btn-soft" style={{ alignSelf: "flex-start" }}
              onClick={() => set("routing.rules", [...rules, { outboundTag: "direct" }])}>
              <Plus size={13} /> Правило
            </button>

            <span className="micro" style={{ marginTop: 4 }}>balancers</span>
            {balancers.map((b, bi) => (
              <div key={bi} style={{ border: "1px solid var(--line-soft)", borderRadius: "var(--r-md)", padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="text-xs font-semibold" style={{ color: "var(--t-hi)" }}>balancers[{bi}]</span>
                  <button className="iconbtn danger" style={{ marginLeft: "auto" }}
                    onClick={() => set("routing.balancers", balancers.filter((_, j) => j !== bi))}>
                    <Trash2 size={13} />
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="label">tag</label>
                    <input className="input" value={b.tag || ""}
                      onChange={e => set(`routing.balancers.${bi}.tag`, e.target.value)} />
                  </div>
                  <div>
                    <label className="label">strategy</label>
                    <select className="selectbox" value={b.strategy?.type || "roundRobin"}
                      onChange={e => set(`routing.balancers.${bi}.strategy`, { ...(b.strategy || {}), type: e.target.value })}>
                      {BALANCER_STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="label">selector (префиксы тегов, через запятую)</label>
                    <Csv value={b.selector || []} onChange={v => set(`routing.balancers.${bi}.selector`, v)} />
                  </div>
                  <div>
                    <label className="label">fallbackTag</label>
                    <input className="input" value={b.fallbackTag || ""}
                      onChange={e => set(`routing.balancers.${bi}.fallbackTag`, e.target.value || undefined)} />
                  </div>
                </div>
              </div>
            ))}
            <button type="button" className="btn btn-soft" style={{ alignSelf: "flex-start" }}
              onClick={() => set("routing.balancers", [...balancers, { tag: "balancer", selector: ["proxy"], strategy: { type: "roundRobin" } }])}>
              <Plus size={13} /> Балансировщик
            </button>
          </Section>

          {/* ── dns ── */}
          <Section title="DNS">
            <div className="grid grid-cols-[1fr_220px] gap-2">
              <div>
                <label className="label">servers (по строке)</label>
                <Lines value={dns.servers || []} rows={3} onChange={v => set("dns.servers", v)} />
              </div>
              <div>
                <label className="label">queryStrategy</label>
                <select className="selectbox" value={dns.queryStrategy || "UseIP"}
                  onChange={e => set("dns.queryStrategy", e.target.value)}>
                  {QUERY_STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
            </div>
          </Section>

          {/* ── inbounds / outbounds (raw per-item) ── */}
          {([["inbounds", inbounds], ["outbounds", outbounds]] as const).map(([key, arr]) => (
            <Section key={key} title={key}
              hint={key === "outbounds" ? "Outbound'ы injectHosts добавляются панелью в НАЧАЛО массива — статические (direct/block) оставляйте в конце." : undefined}>
              {arr.map((item: any, i: number) => (
                <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span className="tag">{item?.protocol || "?"}</span>
                    <span className="text-xs trunc" style={{ color: "var(--t-low)" }}>{item?.tag || `${key}[${i}]`}</span>
                    <button className="iconbtn danger" style={{ marginLeft: "auto" }}
                      onClick={() => set(key, arr.filter((_, j) => j !== i))}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <textarea className="input font-mono text-xs" rows={5}
                    value={JSON.stringify(item, null, 2)}
                    onChange={e => {
                      try { set(key, arr.map((x, j) => (j === i ? JSON.parse(e.target.value) : x))); }
                      catch { /* допечатывает */ }
                    }}
                    onBlur={e => {
                      try { e.target.value = JSON.stringify(JSON.parse(e.target.value), null, 2); }
                      catch { e.target.value = JSON.stringify(item, null, 2); }
                    }}
                    spellCheck={false} />
                </div>
              ))}
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <select className="selectbox" style={{ width: 180 }} id={`${key}-proto`}>
                  {RAW_PROTOCOLS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
                <button type="button" className="btn btn-soft"
                  onClick={() => {
                    const sel = document.getElementById(`${key}-proto`) as HTMLSelectElement;
                    const proto = sel?.value || "freedom";
                    const skeleton: any = { tag: `${proto}-${arr.length + 1}`, protocol: proto };
                    if (key === "inbounds") { skeleton.port = 1080 + arr.length; skeleton.listen = "0.0.0.0"; skeleton.settings = {}; }
                    else if (!["freedom", "blackhole"].includes(proto)) { skeleton.settings = { vnext: [{ address: "example.com", port: 443, users: [] }] }; skeleton.streamSettings = { network: "tcp", security: "none" }; }
                    set(key, [...arr, skeleton]);
                  }}>
                  <Plus size={13} /> {key === "inbounds" ? "Inbound" : "Outbound"}
                </button>
              </div>
            </Section>
          ))}

          {/* ── burstObservatory ── */}
          <Section title="burstObservatory">
            <label className="flex items-center gap-2 text-xs" style={{ color: "var(--t-mid)" }}>
              <input type="checkbox" checked={!!bo}
                onChange={e => set("burstObservatory",
                  e.target.checked
                    ? { pingConfig: { destination: "http://www.gstatic.com/generate_204", interval: "1m", timeout: "3s", sampling: 1 }, subjectSelector: ["proxy"] }
                    : undefined)} />
              Включить обсерваторию (pingConfig + subjectSelector)
            </label>
            {bo && (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="label">destination</label>
                  <input className="input" value={bo.pingConfig?.destination || ""}
                    onChange={e => set("burstObservatory.pingConfig.destination", e.target.value)} />
                </div>
                <div>
                  <label className="label">subjectSelector (через запятую)</label>
                  <Csv value={bo.subjectSelector || []} onChange={v => set("burstObservatory.subjectSelector", v)} />
                </div>
                <div>
                  <label className="label">interval</label>
                  <input className="input" value={bo.pingConfig?.interval || ""}
                    onChange={e => set("burstObservatory.pingConfig.interval", e.target.value)} />
                </div>
                <div>
                  <label className="label">timeout</label>
                  <input className="input" value={bo.pingConfig?.timeout || ""}
                    onChange={e => set("burstObservatory.pingConfig.timeout", e.target.value)} />
                </div>
              </div>
            )}
          </Section>
        </div>
      )}
    </Page>
  );
}
