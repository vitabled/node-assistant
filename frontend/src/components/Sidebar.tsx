import {
  Activity, Rocket, ShieldCheck, FileCode2, Network, Gauge, Settings2, Server,
  PieChart, CreditCard, FolderKanban, ReceiptText,
  KeyRound, SlidersHorizontal, Users,
  ServerCog, LayoutTemplate, DatabaseBackup, ArrowLeftRight, UserCog, Zap,
  Workflow, Bell, Bot, Map as MapIcon, Waypoints, BookOpen, FileJson,
  LayoutDashboard, Boxes, Route as RouteIcon, ShieldHalf, Package, ScanSearch,
  Lock, Cloud, Globe, CloudDownload, Pencil, Save, GripVertical, Table2,
  MessageCircle, Kanban, Ticket, Smartphone
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Fragment, useRef, useState } from "react";
import { usePermissions } from "../auth/usePermissions";
import { getActiveId } from "../auth/store";

export type Tab =
  | "dashboard" | "deploy" | "certs" | "f2b-list" | "templates" | "hosts" | "traffic" | "settings" | "mihomo" | "configs" | "bridges" | "auto"
  | "stats-users" | "stats-speedtests"
  | "automation" | "assistant" | "notifications"
  | "rw-install" | "rw-subpages" | "rw-variables" | "rw-backup" | "rw-migration" | "rw-profiles"
  | "obhod-regru" | "obhod-beeline" | "obhod-subnets"
  | "haproxy-overview" | "haproxy-nodes" | "haproxy-routes" | "haproxy-traffic"
  | "haproxy-firewall" | "haproxy-releases"
  | "hostings-map" | "hostings-list" | "subscription-analyze" | "library" | "vault" | "sitecopy"
  | "cf-overview" | "cf-subscriptions" | "cf-usage" | "cf-payments" | "cf-domains"
  | "infra-dashboard" | "infra-providers" | "infra-projects" | "infra-services"
  | "infra-payments" | "infra-settings" | "infra-tokens"
  | "support-chats" | "support-kanban" | "support-dashboard" | "support-ai"
  | "reshala-tickets" | "reshala-miniapp";

/**
 * Домен привилегий пункта (`services/permissions.py::DOMAINS`).
 *
 * ⚠️ Соответствие «пункт → привилегия» живёт ОДНОЙ таблицей — прямо в описании
 * пунктов ниже, а не условиями в разметке: при первом же переносе пункта между
 * группами вы забыли бы перенести и скрывающее его условие, открыв вкладку тем,
 * у кого прав на неё нет.
 *
 * Если вкладка — read-only view в данные (как логи Cloudflare), скрывать её по
 * «создать»/«изменить» неверно — на чтение он полезен.
 */
type PermDomain =
  | "deploy" | "panel" | "certs" | "hosts" | "configs" | "automation" | "assistant"
  | "monitoring" | "stats" | "hostings" | "billing" | "cloudflare" | "haproxy"
  | "library" | "vault" | "settings" | "bridges" | "support";

export interface NavItemDef { tab: Tab; label: string; Icon: LucideIcon; domain: PermDomain }

const NAV_MAIN: NavItemDef[] = [
  // Дашборд — статус-страница чекера и доступности серверов, отсюда monitoring.
  { tab: "dashboard",  label: "Дашборд",       Icon: Activity,    domain: "monitoring" },
  { tab: "deploy",     label: "Деплой ноды",   Icon: Rocket,      domain: "deploy"     },
  { tab: "certs",      label: "Управление SSL", Icon: ShieldCheck, domain: "certs"     },
  { tab: "f2b-list",   label: "Fail2Ban",      Icon: ShieldHalf,  domain: "deploy"     },
  { tab: "templates",  label: "Шаблоны",       Icon: FileCode2,   domain: "deploy"     },
  { tab: "hosts",      label: "Хосты",         Icon: Network,     domain: "hosts"      },
  // Лимиты трафика — те же `/api/traffic-rules`, что и правила автоматизации.
  { tab: "traffic",    label: "Трафик",        Icon: Gauge,       domain: "automation" },
  // Мосты между серверами — маршруты в config-профилях панели (Волна 4, PR-6).
  { tab: "bridges",    label: "Мосты",         Icon: Waypoints,   domain: "bridges"    },
  // Конфигуратор XRAY_JSON-шаблонов подписки (Волна 4, PR-7).
  { tab: "auto",       label: "Авто",          Icon: FileJson,    domain: "configs"    },
];

const STATS_TABS: NavItemDef[] = [
  { tab: "stats-users",      label: "Пользователи",   Icon: Users, domain: "stats"      },
  { tab: "stats-speedtests", label: "Тесты скорости", Icon: Zap,   domain: "monitoring" },
  // HAProxy (NodeFlow) статистика — перенесена сюда из группы «HAPROXY».
  { tab: "haproxy-overview", label: "HAProxy: обзор",  Icon: LayoutDashboard, domain: "haproxy" },
  { tab: "haproxy-traffic",  label: "HAProxy: трафик", Icon: Gauge,           domain: "haproxy" },
];

const AUTOMATION_TABS: NavItemDef[] = [
  { tab: "automation", label: "Правила",   Icon: Workflow, domain: "automation" },
  // ИИ-чат вынесен из Настроек в отдельный раздел сразу после «Правил» (11a).
  { tab: "assistant",  label: "Ассистент", Icon: Bot,      domain: "assistant"  },
];

const RW_TABS: NavItemDef[] = [
  { tab: "rw-install",   label: "Установка",            Icon: ServerCog,      domain: "panel"   },
  { tab: "rw-subpages",  label: "Страницы подписок",    Icon: LayoutTemplate, domain: "configs" },
  { tab: "rw-variables", label: "Переменные",           Icon: SlidersHorizontal, domain: "panel" },
  { tab: "rw-backup",    label: "Резервное копирование", Icon: DatabaseBackup, domain: "panel"  },
  { tab: "rw-migration", label: "Миграция",             Icon: ArrowLeftRight, domain: "panel"   },
  // Редакторы конфигов Remnawave — после операционных пунктов (Волна 6, План A).
  { tab: "rw-profiles",  label: "Профили",              Icon: UserCog,        domain: "configs" },
  // Mihomo-конфигуратор (встроенный, iframe).
  { tab: "mihomo",       label: "Mihomo",               Icon: Waypoints,      domain: "configs" },
  // Пользовательские конфиги (шаблоны по типам клиента, Wave-5 План D).
  { tab: "configs",      label: "Конфиги",              Icon: FileJson,       domain: "configs" },
];

// Группа «Обходы БС» (Волна 4, PR-9): инструменты обхода через белые хостинги/CDN.
const OBHOD_TABS: NavItemDef[] = [
  { tab: "obhod-regru",   label: "REGRU хостинг", Icon: Globe, domain: "panel"    },
  { tab: "obhod-beeline", label: "Beeline CDN",   Icon: Cloud, domain: "configs"  },
  // Справочник подсетей/IP с авто-ASN (Волна 5, PR-5).
  { tab: "obhod-subnets", label: "Подсети",       Icon: Table2, domain: "hostings" },
];

// Группа «HAPROXY» — прокси к панели NodeFlow (управление HAProxy-нодами).
// «Обзор» и «Трафик» перенесены в «Статистику»; «Настройки» — в Настройки → «HAProxy».
const HAPROXY_TABS: NavItemDef[] = [
  { tab: "haproxy-nodes",     label: "Ноды",      Icon: Boxes,      domain: "haproxy" },
  { tab: "haproxy-routes",    label: "Маршруты",  Icon: RouteIcon,  domain: "haproxy" },
  { tab: "haproxy-firewall",  label: "Файрвол",   Icon: ShieldHalf, domain: "haproxy" },
  { tab: "haproxy-releases",  label: "Релизы",    Icon: Package,    domain: "haproxy" },
];

// Группа «Справка» (бывш. «Хостинги»): карта хостингов + каталог + библиотека знаний.
const HOSTINGS_TABS: NavItemDef[] = [
  { tab: "hostings-map",         label: "Карта хостингов", Icon: MapIcon,    domain: "hostings" },
  { tab: "hostings-list",        label: "Хостинги",        Icon: Server,     domain: "hostings" },
  { tab: "subscription-analyze", label: "Анализ подписки", Icon: ScanSearch, domain: "hostings" },
  // Копия сайта → файлы автоматически в Библиотеку (Волна 4, PR-11).
  { tab: "sitecopy",             label: "Копия сайта",     Icon: CloudDownload, domain: "library" },
  { tab: "library",              label: "Библиотека",      Icon: BookOpen,   domain: "library"  },
  // Хранилище секретов (Волна 9): пароли/API-ключи/SSH-ключи от внешних ресурсов.
  { tab: "vault",                label: "Хранилище",       Icon: Lock,       domain: "vault"    },
];

// Группа «CLOUDFLARE» — биллинг подключённого аккаунта + покупка доменов.
// Подключение живёт в Настройки → «Cloudflare» (как у HAProxy), группа операционная.
const CF_TABS: NavItemDef[] = [
  { tab: "cf-overview",      label: "Обзор",          Icon: Cloud,       domain: "cloudflare" },
  { tab: "cf-subscriptions", label: "Подписки",       Icon: ReceiptText, domain: "cloudflare" },
  { tab: "cf-usage",         label: "Использование",  Icon: Gauge,       domain: "cloudflare" },
  { tab: "cf-payments",      label: "Платежи",        Icon: CreditCard,  domain: "cloudflare" },
  { tab: "cf-domains",       label: "Домены",         Icon: Globe,       domain: "cloudflare" },
];

const INFRA_TABS: NavItemDef[] = [
  { tab: "infra-dashboard", label: "Dashboard",          Icon: PieChart,          domain: "billing" },
  { tab: "infra-providers", label: "Провайдеры",         Icon: CreditCard,        domain: "billing" },
  { tab: "infra-projects",  label: "Проекты",            Icon: FolderKanban,      domain: "billing" },
  { tab: "infra-services",  label: "Услуги и тарифы",    Icon: Server,            domain: "billing" },
  { tab: "infra-payments",  label: "Платежи",            Icon: ReceiptText,       domain: "billing" },
  { tab: "infra-settings",  label: "Настройки биллинга", Icon: SlidersHorizontal, domain: "billing" },
  { tab: "infra-tokens",    label: "API токены",         Icon: KeyRound,          domain: "billing" },
];

// Футер — вне групп, но в модели привилегий такой же, как остальные пункты.
const FOOTER_TABS: NavItemDef[] = [
  { tab: "notifications", label: "Уведомления", Icon: Bell,      domain: "automation" },
  { tab: "settings",      label: "Настройки",   Icon: Settings2, domain: "settings"   },
];

interface NavSubgroup {
  title: string;
  items: NavItemDef[];
}

interface NavGroupDef {
  title: string;
  items: NavItemDef[];
  subgroups?: NavSubgroup[];
}

const GROUPS: NavGroupDef[] = [
  { title: "Управление",     items: NAV_MAIN        },
  { title: "Статистика",     items: STATS_TABS      },
  { title: "Автоматизация",  items: AUTOMATION_TABS },
  { title: "Remnawave",      items: RW_TABS         },
  { title: "Обходы БС",      items: OBHOD_TABS      },
  { title: "HAPROXY",        items: HAPROXY_TABS    },
  { title: "Справка",        items: HOSTINGS_TABS   },
  { title: "Cloudflare",     items: CF_TABS         },
  { title: "Инфра-биллинг",  items: INFRA_TABS      },
  {
    title: "BEDOLAGA",
    items: [],
    subgroups: [
      {
        title: "Поддержка",
        items: [
          { tab: "support-chats", label: "Чаты клиентов", Icon: MessageCircle, domain: "support" },
          { tab: "support-kanban", label: "Канбан-доска", Icon: Kanban, domain: "support" },
          { tab: "support-dashboard", label: "Дашборд", Icon: PieChart, domain: "support" },
          { tab: "support-ai", label: "AI Провайдеры", Icon: Bot, domain: "support" },
        ]
      },
      {
        title: "Решала",
        items: [
          { tab: "reshala-tickets", label: "Тикеты (Решала)", Icon: Ticket, domain: "support" },
          { tab: "reshala-miniapp", label: "Mini App", Icon: Smartphone, domain: "support" },
        ]
      }
    ]
  },
];

/** Все пункты в порядке отрисовки — App берёт отсюда «первую доступную вкладку». */
export const NAV_TABS: readonly NavItemDef[] = [
  ...GROUPS.flatMap(g => [
    ...g.items,
    ...(g.subgroups ? g.subgroups.flatMap(sg => sg.items) : [])
  ]),
  ...FOOTER_TABS,
];

const TAB_DOMAIN = new Map<Tab, PermDomain>(NAV_TABS.map(i => [i.tab, i.domain]));

/** Привилегия просмотра вкладки. `null` — вкладки нет в таблице выше; тогда её НЕ
 *  прячем: тихо потерянный раздел хуже лишнего пункта, а сервер всё равно ответит
 *  403 на его запросы. */
export function tabPermission(tab: Tab): string | null {
  const domain = TAB_DOMAIN.get(tab);
  return domain ? `${domain}.view` : null;
}

// ── порядок разделов (Wave-5 PR-3) ─────────────────────────────
// per-account в localStorage: сайдбар — личная раскладка оператора, на сервер
// не пишем (чужой порядок за другим входом менять нельзя).
interface GroupOrder { title: string; tabs: string[] }

const DEFAULT_ORDER: GroupOrder[] = GROUPS.map(g => ({
  title: g.title,
  tabs: [
    ...g.items.map(i => i.tab),
    ...(g.subgroups ? g.subgroups.flatMap(sg => sg.items.map(i => i.tab)) : [])
  ],
}));

function navOrderKey(accountId: string | null | undefined): string {
  return accountId ? `ni_nav_order_${accountId}` : "ni_nav_order";
}

/** Слить сохранённый порядок с текущим составом: неизвестные табы/группы —
 *  в дефолтные позиции, удалённые из кода — выпадают. */
export function mergeNavOrder(saved: GroupOrder[] | null): GroupOrder[] {
  if (!Array.isArray(saved)) return DEFAULT_ORDER;
  const knownTabs = new Map(NAV_TABS.map(i => [i.tab as string, true]));
  const seen = new Set<string>();
  const out: GroupOrder[] = [];
  for (const g of saved) {
    if (!g || typeof g.title !== "string" || !Array.isArray(g.tabs)) continue;
    const def = DEFAULT_ORDER.find(d => d.title === g.title);
    if (!def) continue;
    const tabs = g.tabs.filter(t => knownTabs.has(t) && def.tabs.includes(t) && !seen.has(t));
    tabs.forEach(t => seen.add(t));
    if (tabs.length) out.push({ title: g.title, tabs });
  }
  // недостающие группы/табы — дефолтным порядком
  for (const def of DEFAULT_ORDER) {
    const missing = def.tabs.filter(t => !seen.has(t));
    if (!missing.length) continue;
    missing.forEach(t => seen.add(t));
    const existing = out.find(g => g.title === def.title);
    if (existing) existing.tabs.push(...missing);
    else out.push({ title: def.title, tabs: missing });
  }
  return out;
}

function loadNavOrder(accountId: string | null | undefined): GroupOrder[] {
  try {
    return mergeNavOrder(JSON.parse(localStorage.getItem(navOrderKey(accountId)) || "null"));
  } catch { return DEFAULT_ORDER; }
}

function saveNavOrder(accountId: string | null | undefined, order: GroupOrder[]): void {
  try { localStorage.setItem(navOrderKey(accountId), JSON.stringify(order)); } catch {}
}

interface Props {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
  collapsed: boolean;    // "rail" mode in the design
  onToggle: () => void;
  drawer?: boolean;      // rendered inside the mobile drawer → keep visible (no .ni-sidebar hide)
}

export function Sidebar({ activeTab, onTabChange, drawer }: Props) {
  // Гейт разделов. ⚠️ Косметика: настоящая проверка — на сервере (см.
  // usePermissions). Пока права неизвестны, `can` разрешает всё, поэтому сайдбар
  // не мигает пустым на каждой загрузке.
  const { can } = usePermissions();

  // ── перестановка разделов (Wave-5 PR-3) ──
  const accountId = getActiveId();
  const [order, setOrder] = useState<GroupOrder[]>(() => loadNavOrder(accountId));
  const [editing, setEditing] = useState(false);
  const dragRef = useRef<{ type: "item"; tab: string } | { type: "group"; title: string } | null>(null);
  const defs = new Map(NAV_TABS.map(i => [i.tab as string, i]));

  const groups = order
    .map(go => ({
      title: go.title,
      items: go.tabs
        .map(t => defs.get(t))
        .filter((i): i is NavItemDef => !!i && can(`${i.domain}.view`)),
    }))
    .filter(g => g.items.length > 0);
  const footer = FOOTER_TABS.filter(i => can(`${i.domain}.view`));

  // Переместить перетаскиваемое перед целевым (таб — в позицию таба, группу —
  // перед группой). Работает и между группами.
  const moveBefore = (dragTab: string, targetGroup: string, targetTab: string | null) => {
    setOrder(prev => {
      const next: GroupOrder[] = prev.map(g => ({ ...g, tabs: g.tabs.filter(t => t !== dragTab) }));
      const g = next.find(x => x.title === targetGroup);
      if (!g) return prev;
      const idx = targetTab ? g.tabs.indexOf(targetTab) : g.tabs.length;
      g.tabs.splice(idx < 0 ? g.tabs.length : idx, 0, dragTab);
      return next.filter(x => x.tabs.length > 0);
    });
  };
  const moveGroupBefore = (dragTitle: string, targetTitle: string) => {
    setOrder(prev => {
      const from = prev.findIndex(g => g.title === dragTitle);
      const to = prev.findIndex(g => g.title === targetTitle);
      if (from < 0 || to < 0 || from === to) return prev;
      const next = [...prev];
      const [g] = next.splice(from, 1);
      next.splice(next.findIndex(x => x.title === targetTitle), 0, g);
      return next;
    });
  };

  const NavBtn = ({ item }: { item: NavItemDef }) => {
    const { Icon, label } = item;
    const active = activeTab === item.tab;
    return (
      <button
        className={`navitem ${active ? "active" : ""}`}
        onClick={() => !editing && onTabChange(item.tab)}
        draggable={editing}
        onDragStart={editing ? () => { dragRef.current = { type: "item", tab: item.tab }; } : undefined}
        onDragOver={editing ? e => e.preventDefault() : undefined}
        onDrop={editing ? e => {
          e.preventDefault();
          const d = dragRef.current;
          if (d?.type === "item" && d.tab !== item.tab) {
            const g = order.find(x => x.tabs.includes(item.tab));
            if (g) moveBefore(d.tab, g.title, item.tab);
          }
        } : undefined}
        style={editing ? { cursor: "grab" } : undefined}
      >
        {editing && <GripVertical size={13} style={{ flex: "none", color: "var(--t-faint)" }} />}
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
        <span className="ni-brandicon">
          <Server size={17} />
        </span>
        <div style={{ minWidth: 0 }}>
          <p style={{ fontSize: 13, fontWeight: 700, color: "var(--t-hi)", lineHeight: 1.2 }}>Node Assistant</p>
        </div>
        {/* Режим перестановки разделов (Wave-5 PR-3): карандаш → dnd,
            сохранение — дискета на его месте. */}
        <button
          type="button"
          className="iconbtn"
          style={{ marginLeft: "auto", flex: "none" }}
          title={editing ? "Сохранить порядок разделов" : "Изменить порядок разделов"}
          data-testid="nav-edit-toggle"
          onClick={() => {
            if (editing) saveNavOrder(accountId, order);
            setEditing(e => !e);
          }}
        >
          {editing ? <Save size={14} /> : <Pencil size={14} />}
        </button>
      </div>

      {/* Бренд-разделитель: градиент transparent → cyan → transparent (только remnawave). */}
      <div className="ni-brand-divider" />

      {/* nav — заголовок группы и её кнопки остаются СИБЛИНГАМИ в одной колонке
          (Fragment не добавляет узлов): по этой плоской структуре ходит тест. */}
      <div style={{ flex: 1, overflowY: "auto", overflowX: "hidden", display: "flex", flexDirection: "column", gap: 2, paddingTop: 8 }}>
        {groups.map((g, i) => (
          <Fragment key={g.title}>
            {i > 0 && <div style={{ height: 1, background: "var(--line-soft)", margin: "10px 4px" }} />}
            <p className="micro"
              style={{
                padding: "0 10px", margin: "2px 0 4px",
                cursor: editing ? "grab" : undefined,
                color: editing ? "var(--accent-hi)" : undefined,
              }}
              draggable={editing}
              onDragStart={editing ? () => { dragRef.current = { type: "group", title: g.title }; } : undefined}
              onDragOver={editing ? e => e.preventDefault() : undefined}
              onDrop={editing ? e => {
                e.preventDefault();
                const d = dragRef.current;
                if (d?.type === "group" && d.title !== g.title) moveGroupBefore(d.title, g.title);
                if (d?.type === "item") moveBefore(d.tab, g.title, null);  // в конец группы
              } : undefined}
            >{editing && <GripVertical size={11} style={{ verticalAlign: "-2px", marginRight: 4 }} />}{g.title}</p>
            {g.items.map(item => {
              const defGroup = GROUPS.find(gd => gd.title === g.title);
              let subgroupTitle: string | null = null;
              if (defGroup?.subgroups) {
                for (const sg of defGroup.subgroups) {
                  if (sg.items.length > 0 && sg.items[0].tab === item.tab) {
                    subgroupTitle = sg.title;
                    break;
                  }
                }
              }
              return (
                <Fragment key={item.tab}>
                  {subgroupTitle && (
                    <div style={{
                      fontSize: 9, fontWeight: 700, textTransform: "uppercase", 
                      color: "var(--t-faint)", margin: "6px 0 2px 24px", letterSpacing: ".04em"
                    }}>
                      {subgroupTitle}
                    </div>
                  )}
                  <NavBtn item={item} />
                </Fragment>
              );
            })}
          </Fragment>
        ))}
      </div>

      {/* footer — Уведомления + Настройки (moved out of the main nav) */}
      {footer.length > 0 && (
        <div style={{ paddingTop: 8, borderTop: "1px solid var(--line-soft)", marginTop: 6 }}>
          {footer.map(item => <NavBtn key={item.tab} item={item} />)}
        </div>
      )}
    </aside>
  );
}
