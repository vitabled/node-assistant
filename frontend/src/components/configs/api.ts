// Typed client for /api/config-templates (Wave-5 Plan D).
export type TemplateKind = "xray-json" | "xray-base64" | "mihomo" | "stash" | "clash" | "singbox";

export interface ConfigTemplate {
  id: string;
  name: string;
  kind: TemplateKind;
  content_json?: Record<string, unknown> | null;
  content_yaml?: string | null;
  note?: string | null;
  view_position: number;
  created_at: number;
}

export const KINDS: { key: TemplateKind; label: string; core: "json" | "yaml" }[] = [
  { key: "xray-json",   label: "Xray JSON",   core: "json" },
  { key: "xray-base64", label: "Xray Base64", core: "json" },
  { key: "singbox",     label: "sing-box",    core: "json" },
  { key: "mihomo",      label: "Mihomo",      core: "yaml" },
  { key: "clash",       label: "Clash",       core: "yaml" },
  { key: "stash",       label: "Stash",       core: "yaml" },
];
export const coreOf = (k: TemplateKind) => KINDS.find(x => x.key === k)?.core ?? "json";
export const labelOf = (k: TemplateKind) => KINDS.find(x => x.key === k)?.label ?? k;

function fmtDetail(d: any): string {
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e: any) => e?.msg ?? "ошибка").join("; ");
  return "Ошибка";
}
async function j(r: Response): Promise<any> {
  if (!r.ok) throw new Error(fmtDetail((await r.json().catch(() => ({}))).detail));
  return r.status === 204 ? null : r.json();
}

export const configApi = {
  list: (): Promise<ConfigTemplate[]> => fetch("/api/config-templates").then(j),
  create: (b: Partial<ConfigTemplate>): Promise<ConfigTemplate> =>
    fetch("/api/config-templates", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(j),
  update: (id: string, b: Partial<ConfigTemplate>): Promise<ConfigTemplate> =>
    fetch(`/api/config-templates/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(j),
  remove: (id: string): Promise<null> =>
    fetch(`/api/config-templates/${id}`, { method: "DELETE" }).then(j),
  exportToPanel: (id: string): Promise<{ uuid: string }> =>
    fetch(`/api/config-templates/${id}/export`, { method: "POST" }).then(j),
};
