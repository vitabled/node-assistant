import { useState } from "react";
import {
  Menu, LayoutDashboard, Rocket, RefreshCw, FileCode2, Settings2, Gauge,
  Wallet, ChevronDown, CreditCard, Boxes, ReceiptText, PieChart,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type Tab =
  | "dashboard" | "deploy" | "certs" | "templates" | "settings" | "traffic"
  | "infra-providers" | "infra-nodes" | "infra-history" | "infra-analytics";

const STORAGE_KEY = "sidebar_collapsed";

interface NavItemDef {
  tab: Tab;
  label: string;
  Icon: LucideIcon;
}

const TOP_ITEMS: NavItemDef[] = [
  { tab: "dashboard", label: "Дешборд", Icon: LayoutDashboard },
];

const NODE_ITEMS: NavItemDef[] = [
  { tab: "deploy",     label: "Деплой ноды",         Icon: Rocket    },
  { tab: "certs",      label: "Обновить SSL",         Icon: RefreshCw },
  { tab: "templates",  label: "Шаблоны",              Icon: FileCode2 },
  { tab: "traffic",    label: "Ограничение трафика",  Icon: Gauge     },
];

const INFRA_ITEMS: NavItemDef[] = [
  { tab: "infra-providers", label: "Провайдеры хостинга", Icon: CreditCard  },
  { tab: "infra-nodes",     label: "Узлы биллинга",       Icon: Boxes       },
  { tab: "infra-history",   label: "История и Инвойсы",   Icon: ReceiptText },
  { tab: "infra-analytics", label: "Аналитика расходов",  Icon: PieChart    },
];

const BOTTOM_ITEMS: NavItemDef[] = [
  { tab: "settings", label: "Настройки", Icon: Settings2 },
];

interface Props {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ activeTab, onTabChange, collapsed, onToggle }: Props) {
  return (
    <aside
      className={`shrink-0 flex flex-col h-full border-r border-gray-800/80 bg-gray-950
                  transition-[width] duration-200 ease-in-out
                  ${collapsed ? "w-14" : "w-52"}`}
    >
      {/* Header / Hamburger */}
      <div className="h-11 flex items-center border-b border-gray-800/80 px-2.5 gap-2.5 shrink-0">
        <button
          onClick={onToggle}
          className="w-8 h-8 flex items-center justify-center rounded-md shrink-0
                     text-gray-500 hover:text-gray-200 hover:bg-gray-800
                     transition-colors focus:outline-none focus:ring-1 focus:ring-gray-700"
          aria-label={collapsed ? "Развернуть панель" : "Свернуть панель"}
        >
          <Menu size={16} />
        </button>
        {!collapsed && (
          <span className="text-sm font-semibold text-white tracking-tight truncate">
            Node Installer
          </span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-2 px-2 flex flex-col gap-0.5">
        {TOP_ITEMS.map(item => (
          <NavBtn
            key={item.tab}
            item={item}
            active={activeTab === item.tab}
            collapsed={collapsed}
            onClick={() => onTabChange(item.tab)}
          />
        ))}

        {/* Nodes group */}
        <div className="mt-3">
          {collapsed
            ? <div className="border-t border-gray-800/50 my-1.5 mx-1" />
            : <p className="px-2 py-1 text-[10px] font-semibold text-gray-600
                            uppercase tracking-widest select-none">
                Ноды
              </p>
          }
          <div className="flex flex-col gap-0.5">
            {NODE_ITEMS.map(item => (
              <NavBtn
                key={item.tab}
                item={item}
                active={activeTab === item.tab}
                collapsed={collapsed}
                onClick={() => onTabChange(item.tab)}
              />
            ))}
          </div>
        </div>

        {/* Инфра-биллинг — collapsible group */}
        <InfraGroup activeTab={activeTab} collapsed={collapsed} onTabChange={onTabChange} />

        {/* Settings at bottom */}
        <div className="mt-auto pt-3">
          {collapsed
            ? <div className="border-t border-gray-800/50 my-1.5 mx-1" />
            : <div className="border-t border-gray-800/50 my-1.5" />
          }
          {BOTTOM_ITEMS.map(item => (
            <NavBtn
              key={item.tab}
              item={item}
              active={activeTab === item.tab}
              collapsed={collapsed}
              onClick={() => onTabChange(item.tab)}
            />
          ))}
        </div>
      </nav>
    </aside>
  );
}


function NavBtn({
  item, active, collapsed, onClick,
}: {
  item: NavItemDef;
  active: boolean;
  collapsed: boolean;
  onClick: () => void;
}) {
  const { Icon, label } = item;
  return (
    <button
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={`w-full flex items-center gap-2.5 rounded-md text-sm
                  transition-colors focus:outline-none focus:ring-1 focus:ring-blue-500/30
                  ${collapsed ? "px-0 justify-center py-2" : "px-2.5 py-2"}
                  ${active
                    ? "bg-blue-600/15 text-blue-400 border border-blue-700/40"
                    : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/60 border border-transparent"
                  }`}
    >
      <Icon size={16} className="shrink-0" />
      {!collapsed && <span className="truncate">{label}</span>}
    </button>
  );
}


// Collapsible "Инфра-биллинг" accordion group. Collapsed by default unless the
// active tab is inside the group. In icon-only mode the sub-items render directly.
function InfraGroup({
  activeTab, collapsed, onTabChange,
}: {
  activeTab: Tab; collapsed: boolean; onTabChange: (t: Tab) => void;
}) {
  const insideGroup = INFRA_ITEMS.some(i => i.tab === activeTab);
  const [open, setOpen] = useState(insideGroup);

  // Icon-only sidebar: show items directly under a divider (accordion has no room).
  if (collapsed) {
    return (
      <div className="mt-3">
        <div className="border-t border-gray-800/50 my-1.5 mx-1" />
        <div className="flex flex-col gap-0.5">
          {INFRA_ITEMS.map(item => (
            <NavBtn key={item.tab} item={item} active={activeTab === item.tab}
              collapsed onClick={() => onTabChange(item.tab)} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen(o => !o)}
        className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-[10px] font-semibold
                    uppercase tracking-widest transition-colors focus:outline-none
                    ${insideGroup ? "text-blue-400" : "text-gray-600 hover:text-gray-400"}`}
      >
        <Wallet size={13} className="shrink-0" />
        <span className="flex-1 text-left">Инфра-биллинг</span>
        <ChevronDown size={13} className={`transition-transform duration-200 ${open ? "" : "-rotate-90"}`} />
      </button>
      {/* Smooth expand/collapse */}
      <div className={`overflow-hidden transition-[max-height] duration-200 ease-in-out
                       ${open ? "max-h-64" : "max-h-0"}`}>
        <div className="flex flex-col gap-0.5 pl-1 pt-0.5">
          {INFRA_ITEMS.map(item => (
            <NavBtn key={item.tab} item={item} active={activeTab === item.tab}
              collapsed={false} onClick={() => onTabChange(item.tab)} />
          ))}
        </div>
      </div>
    </div>
  );
}
