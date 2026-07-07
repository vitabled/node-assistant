import {
  Activity, Rocket, ShieldCheck, FileCode2, Network, Gauge, Settings2, Server,
  PieChart, CreditCard, FolderKanban, ReceiptText,
  KeyRound, SlidersHorizontal, Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type Tab =
  | "dashboard" | "deploy" | "certs" | "templates" | "hosts" | "traffic" | "settings"
  | "stats-users"
  | "infra-dashboard" | "infra-providers" | "infra-projects" | "infra-services"
  | "infra-payments" | "infra-settings" | "infra-tokens";

interface NavItemDef { tab: Tab; label: string; Icon: LucideIcon }

const NAV_MAIN: NavItemDef[] = [
  { tab: "dashboard", label: "Дешборд",      Icon: Activity  },
  { tab: "deploy",    label: "Деплой ноды",  Icon: Rocket    },
  { tab: "certs",     label: "Управление SSL", Icon: ShieldCheck },
  { tab: "templates", label: "Шаблоны",      Icon: FileCode2 },
  { tab: "hosts",     label: "Хосты",        Icon: Network   },
  { tab: "traffic",   label: "Трафик",       Icon: Gauge     },
];

const STATS_TABS: NavItemDef[] = [
  { tab: "stats-users", label: "Пользователи", Icon: Users },
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
  drawer?: boolean;      // rendered inside the mobile drawer → keep visible (no .ni-sidebar hide)
}

export function Sidebar({ activeTab, onTabChange, drawer }: Props) {
  const NavBtn = ({ item }: { item: NavItemDef }) => {
    const { Icon, label } = item;
    const active = activeTab === item.tab;
    return (
      <button
        className={`navitem ${active ? "active" : ""}`}
        onClick={() => onTabChange(item.tab)}
      >
        <Icon size={16} style={{ flex: "none" }} />
        <span className="trunc">{label}</span>
      </button>
    );
  };

  return (
    <aside
      className={drawer ? undefined : "ni-sidebar"}
      style={{
        width: 224, flex: "none", background: "var(--sidebar-bg)",
        borderRight: "1px solid var(--line-soft)", display: "flex",
        flexDirection: "column", padding: "16px 12px 12px",
        height: drawer ? "100%" : undefined,
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
        <p className="micro" style={{ padding: "0 10px", margin: "2px 0 4px" }}>Статистика</p>
        {STATS_TABS.map(item => <NavBtn key={item.tab} item={item} />)}

        <div style={{ height: 1, background: "var(--line-soft)", margin: "10px 4px" }} />
        <p className="micro" style={{ padding: "0 10px", margin: "2px 0 4px" }}>Инфра-биллинг</p>

        {/* Infra subtabs — flat section (no accordion) */}
        {INFRA_TABS.map(item => <NavBtn key={item.tab} item={item} />)}
      </div>

      {/* footer — Настройки (moved out of the main nav) */}
      <div style={{ paddingTop: 8, borderTop: "1px solid var(--line-soft)", marginTop: 6 }}>
        <button
          className={`navitem ${activeTab === "settings" ? "active" : ""}`}
          onClick={() => onTabChange("settings")}
        >
          <Settings2 size={16} style={{ flex: "none" }} />
          <span className="trunc">Настройки</span>
        </button>
      </div>
    </aside>
  );
}
