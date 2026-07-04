import { useState } from "react";
import {
  Activity, Rocket, RefreshCw, FileCode2, Gauge, Settings2, Server,
  PieChart, ChevronDown, CreditCard, FolderKanban, ReceiptText,
  KeyRound, SlidersHorizontal,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type Tab =
  | "dashboard" | "deploy" | "certs" | "templates" | "traffic" | "settings"
  | "infra-dashboard" | "infra-providers" | "infra-projects" | "infra-services"
  | "infra-payments" | "infra-settings" | "infra-tokens";

interface NavItemDef { tab: Tab; label: string; Icon: LucideIcon }

const NAV_MAIN: NavItemDef[] = [
  { tab: "dashboard", label: "Дешборд",      Icon: Activity  },
  { tab: "deploy",    label: "Деплой ноды",  Icon: Rocket    },
  { tab: "certs",     label: "Обновить SSL", Icon: RefreshCw },
  { tab: "templates", label: "Шаблоны",      Icon: FileCode2 },
  { tab: "traffic",   label: "Трафик",       Icon: Gauge     },
  { tab: "settings",  label: "Настройки",    Icon: Settings2 },
];

const INFRA_TABS: NavItemDef[] = [
  { tab: "infra-dashboard", label: "Dashboard",          Icon: PieChart          },
  { tab: "infra-providers", label: "Провайдеры",         Icon: CreditCard        },
  { tab: "infra-projects",  label: "Проекты",            Icon: FolderKanban      },
  { tab: "infra-services",  label: "Услуги и тарифы",    Icon: Server            },
  { tab: "infra-payments",  label: "Платежи",            Icon: ReceiptText       },
  { tab: "infra-settings",  label: "Настройки биллинга", Icon: SlidersHorizontal },
  { tab: "infra-tokens",    label: "API токены",         Icon: KeyRound          },
];

interface Props {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
  collapsed: boolean;    // "rail" mode in the design
  onToggle: () => void;
}

export function Sidebar({ activeTab, onTabChange }: Props) {
  const isInfra = activeTab.startsWith("infra-");
  const [infraOpen, setInfraOpen] = useState(isInfra);

  const NavBtn = ({ item, nested }: { item: NavItemDef; nested?: boolean }) => {
    const { Icon, label } = item;
    const active = activeTab === item.tab;
    return (
      <button
        className={`navitem ${active ? "active" : ""}`}
        onClick={() => onTabChange(item.tab)}
        style={{ paddingLeft: nested ? 30 : undefined }}
      >
        <Icon size={16} style={{ flex: "none" }} />
        <span className="trunc">{label}</span>
      </button>
    );
  };

  return (
    <aside
      style={{
        width: 224, flex: "none", background: "var(--sidebar-bg)",
        borderRight: "1px solid var(--line-soft)", display: "flex",
        flexDirection: "column", padding: "16px 12px 12px",
      }}
    >
      {/* brand */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 4px 4px", minHeight: 34 }}>
        <span style={{
          width: 30, height: 30, borderRadius: 8, background: "var(--accent)",
          color: "var(--accent-ink)", display: "grid", placeItems: "center", flex: "none",
          boxShadow: "0 2px 10px var(--accent-dim)",
        }}>
          <Server size={17} />
        </span>
        <div style={{ minWidth: 0 }}>
          <p style={{ fontSize: 13, fontWeight: 700, color: "var(--t-hi)", lineHeight: 1.2 }}>Node Installer</p>
          <p style={{ fontSize: 10, color: "var(--t-low)", letterSpacing: ".04em" }}>remnawave ops</p>
        </div>
      </div>

      {/* nav */}
      <div style={{ flex: 1, overflowY: "auto", overflowX: "hidden", display: "flex", flexDirection: "column", gap: 2, paddingTop: 8 }}>
        <p className="micro" style={{ padding: "0 10px", margin: "2px 0 4px" }}>Управление</p>
        {NAV_MAIN.map(item => <NavBtn key={item.tab} item={item} />)}

        <div style={{ height: 1, background: "var(--line-soft)", margin: "10px 4px" }} />
        <p className="micro" style={{ padding: "0 10px", margin: "2px 0 4px" }}>Инфраструктура</p>

        {/* Infra accordion group */}
        <button className={`navitem ${isInfra && !infraOpen ? "active" : ""}`} onClick={() => setInfraOpen(v => !v)}>
          <PieChart size={16} style={{ flex: "none" }} />
          <span className="trunc" style={{ flex: 1 }}>Инфра-биллинг</span>
          <ChevronDown size={13} style={{ color: "var(--t-low)", transform: infraOpen ? "none" : "rotate(-90deg)", transition: "transform .15s" }} />
        </button>
        {infraOpen && (
          <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 2 }}>
            {INFRA_TABS.map(item => <NavBtn key={item.tab} item={item} nested />)}
          </div>
        )}
      </div>

      {/* footer status */}
      <div style={{ padding: "10px 10px 2px", borderTop: "1px solid var(--line-soft)", marginTop: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
          <span className="dot" style={{ background: "var(--ok)" }} />
          <span className="dim">Remnawave</span>
          <span className="chip ok" style={{ marginLeft: "auto", padding: "1px 7px", fontSize: 10 }}>онлайн</span>
        </div>
      </div>
    </aside>
  );
}
