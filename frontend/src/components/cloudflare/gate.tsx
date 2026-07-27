// Connection gate for the Cloudflare section (mirrors haproxy/gate.tsx).
// Every operational page renders <CfNotConnected/> until a token + account exist,
// so no page has to special-case an unconfigured backend.
import { useCallback, useEffect, useState } from "react";
import { Cloud } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { getConfig, type CfConfig } from "./api";

export function useCfReady() {
  const [cfg, setCfg] = useState<CfConfig | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(() => {
    setLoading(true);
    getConfig()
      .then(setCfg)
      .catch(() => setCfg(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(reload, [reload]);

  return {
    cfg,
    loading,
    reload,
    ready: !!cfg && cfg.enabled && cfg.has_token && !!cfg.account_id,
  };
}

export function CfNotConnected({ title }: { title: string }) {
  return (
    <Page>
      <PageHeader icon={<Cloud size={18} />} title={title} />
      <div className="rounded-lg p-4 text-xs"
        style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t-hi)" }}>
        <p style={{ marginBottom: 6 }}>Cloudflare не подключён.</p>
        <p style={{ color: "var(--t-low)" }}>
          Откройте <b>Настройки → Cloudflare</b>, вставьте API-токен и выберите аккаунт.
          Токену нужны права на чтение биллинга, а для покупки домена — на Registrar.
        </p>
      </div>
    </Page>
  );
}
