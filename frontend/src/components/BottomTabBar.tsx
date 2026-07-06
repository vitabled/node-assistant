import { Activity, Rocket, ShieldCheck, Gauge, Menu } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Tab } from "./Sidebar";

// Native-style bottom tab bar — mobile only (shown via .ni-tabbar CSS ≤820px).
// 4 primary destinations + «Ещё» which opens the full-nav drawer.
const TABS: { tab: Tab; label: string; Icon: LucideIcon }[] = [
  { tab: "dashboard", label: "Статус", Icon: Activity },
  { tab: "deploy",    label: "Деплой", Icon: Rocket },
  { tab: "certs",     label: "SSL",    Icon: ShieldCheck },
  { tab: "traffic",   label: "Трафик", Icon: Gauge },
];

// The tab-bar's primary destinations. Exported so App.tsx derives «Ещё»'s
// active-state from the SAME source (no drift between the two lists).
export const PRIMARY_TABS: Tab[] = TABS.map(t => t.tab);

interface Props {
  activeTab: Tab;
  onTabChange: (t: Tab) => void;
  onMore: () => void;
  moreActive: boolean;
}

function TabButton({ Icon, label, active, onClick }:
  { Icon: LucideIcon; label: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} aria-current={active ? "page" : undefined} style={{
      flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      gap: 3, padding: "7px 2px 4px", minHeight: 50,
      color: active ? "var(--accent)" : "var(--t-low)", transition: "color .12s",
    }}>
      <Icon size={21} />
      <span className="trunc" style={{ fontSize: 10, fontWeight: 600, lineHeight: 1 }}>{label}</span>
    </button>
  );
}

export function BottomTabBar({ activeTab, onTabChange, onMore, moreActive }: Props) {
  return (
    <nav className="ni-tabbar" style={{
      position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 50,
      background: "var(--sidebar-bg)", borderTop: "1px solid var(--line-soft)",
      paddingBottom: "var(--safe-b)", paddingLeft: "var(--safe-l)", paddingRight: "var(--safe-r)",
      alignItems: "stretch",
    }}>
      {TABS.map(it => (
        <TabButton key={it.tab} Icon={it.Icon} label={it.label}
          active={!moreActive && activeTab === it.tab} onClick={() => onTabChange(it.tab)} />
      ))}
      <TabButton Icon={Menu} label="Ещё" active={moreActive} onClick={onMore} />
    </nav>
  );
}
