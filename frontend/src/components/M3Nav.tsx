import {
  Activity, Rocket, Server, Shield, Wallet, Bot, Headset, BookOpen, Settings2, Menu,
  Zap, LayoutDashboard, Gauge, Users, ScanSearch, ShieldHalf, ShieldCheck, FileCode2, Network,
  Waypoints, Route as RouteIcon, Boxes, Package, ServerCog, SlidersHorizontal, DatabaseBackup,
  ArrowLeftRight, LayoutTemplate, UserCog, FileJson, Braces, Table2, Globe, Cloud, CloudDownload,
  PieChart, CreditCard, FolderKanban, ReceiptText, KeyRound, Workflow, Bell, Cpu, MessageCircle,
  Kanban, Ticket, Smartphone, Lock, Map as MapIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { usePermissions } from "../auth/usePermissions";
import { NAV_TABS, type Tab } from "./Sidebar";

// Навигация скина «Material 3» (Claude Design «Node Assistant M3 — Full»):
// rail из восьми разделов + drawer с группами активного раздела. Состав
// пунктов и привилегии — те же, что у Sidebar: домен каждого пункта берётся из
// NAV_TABS, поэтому перенос пункта между разделами не может открыть вкладку
// без прав. Тест M3Nav.test.tsx следит, что каждый пункт NAV_TABS лежит здесь
// ровно один раз.

type M3Section = "monitoring" | "nodes" | "panel" | "bypass" | "finance" | "automation" | "support" | "reference";
interface M3Item { tab: Tab; label: string; Icon: LucideIcon }
interface M3Group { title: string; sec: M3Section; items: M3Item[] }

export const M3_SECTIONS: { id: M3Section; label: string; short: string; Icon: LucideIcon }[] = [
  { id: "monitoring", label: "Мониторинг",         short: "Мониторинг", Icon: Activity },
  { id: "nodes",      label: "Ноды",               short: "Ноды",       Icon: Server },
  { id: "panel",      label: "Панель Remnawave",   short: "Панель",     Icon: ServerCog },
  { id: "bypass",     label: "Обход блокировок",   short: "Обход",      Icon: Shield },
  { id: "finance",    label: "Финансы",            short: "Финансы",    Icon: Wallet },
  { id: "automation", label: "Автоматизация",      short: "Автоматика", Icon: Bot },
  { id: "support",    label: "Поддержка",          short: "Поддержка",  Icon: Headset },
  { id: "reference",  label: "Справка и доступы",  short: "Справка",    Icon: BookOpen },
];

export const M3_GROUPS: M3Group[] = [
  // ── Мониторинг: что происходит прямо сейчас
  { title: "Состояние сети", sec: "monitoring", items: [
    { tab: "dashboard",        label: "Статус нод",      Icon: Activity },
    { tab: "stats-speedtests", label: "Тесты скорости",  Icon: Zap },
    { tab: "haproxy-overview", label: "HAProxy: обзор",  Icon: LayoutDashboard },
    { tab: "haproxy-traffic",  label: "HAProxy: трафик", Icon: Gauge },
  ] },
  { title: "Пользователи и трафик", sec: "monitoring", items: [
    { tab: "stats-users",          label: "Пользователи",    Icon: Users },
    { tab: "traffic",              label: "Лимиты трафика",  Icon: Gauge },
    { tab: "subscription-analyze", label: "Анализ подписки", Icon: ScanSearch },
  ] },
  { title: "Безопасность", sec: "monitoring", items: [
    { tab: "f2b-list",         label: "Fail2Ban",        Icon: ShieldHalf },
    { tab: "haproxy-firewall", label: "Файрвол HAProxy", Icon: Shield },
    { tab: "certs",            label: "SSL-сертификаты", Icon: ShieldCheck },
    { tab: "rkn-scanners",     label: "RKNscanner",      Icon: ScanSearch },
  ] },
  // ── Ноды: серверы и их обслуживание
  { title: "Развёртывание", sec: "nodes", items: [
    { tab: "deploy",    label: "Деплой ноды",  Icon: Rocket },
    { tab: "templates", label: "Шаблоны Xray", Icon: FileCode2 },
    { tab: "hosts",     label: "Хосты",        Icon: Network },
  ] },
  { title: "Связность", sec: "nodes", items: [
    { tab: "bridges",        label: "Мосты",            Icon: Waypoints },
    { tab: "haproxy-routes", label: "Маршруты HAProxy", Icon: RouteIcon },
  ] },
  { title: "HAProxy-ноды", sec: "nodes", items: [
    { tab: "haproxy-nodes",    label: "Ноды",   Icon: Boxes },
    { tab: "haproxy-releases", label: "Релизы", Icon: Package },
  ] },
  // ── Панель Remnawave: сервер панели и то, что она отдаёт клиентам
  { title: "Сервер панели", sec: "panel", items: [
    { tab: "rw-install",   label: "Установка",             Icon: ServerCog },
    { tab: "rw-variables", label: "Переменные",            Icon: SlidersHorizontal },
    { tab: "rw-backup",    label: "Резервное копирование", Icon: DatabaseBackup },
    { tab: "rw-migration", label: "Миграция",              Icon: ArrowLeftRight },
  ] },
  { title: "Подписки и конфиги", sec: "panel", items: [
    { tab: "rw-subpages", label: "Страницы подписок",  Icon: LayoutTemplate },
    { tab: "rw-profiles", label: "Профили Xray",       Icon: UserCog },
    { tab: "configs",     label: "Клиентские конфиги", Icon: FileJson },
    { tab: "auto",        label: "Авто-шаблон JSON",   Icon: Braces },
    { tab: "mihomo",      label: "Mihomo",             Icon: Waypoints },
  ] },
  // ── Обход блокировок: разведка + маскировка
  { title: "Разведка", sec: "bypass", items: [
    { tab: "obhod-subnets", label: "Подсети операторов", Icon: Table2 },
  ] },
  { title: "Маскировка и фронты", sec: "bypass", items: [
    { tab: "obhod-regru",   label: "REGRU хостинг", Icon: Globe },
    { tab: "obhod-beeline", label: "Beeline CDN",   Icon: Cloud },
    { tab: "sitecopy",      label: "Копия сайта",   Icon: CloudDownload },
  ] },
  // ── Финансы: за что и кому платим
  { title: "Инфра-биллинг", sec: "finance", items: [
    { tab: "infra-dashboard", label: "Обзор",           Icon: PieChart },
    { tab: "infra-providers", label: "Провайдеры",      Icon: CreditCard },
    { tab: "infra-projects",  label: "Проекты",         Icon: FolderKanban },
    { tab: "infra-services",  label: "Услуги и тарифы", Icon: Server },
    { tab: "infra-payments",  label: "Платежи",         Icon: ReceiptText },
  ] },
  { title: "Cloudflare", sec: "finance", items: [
    { tab: "cf-overview",      label: "Обзор",         Icon: Cloud },
    { tab: "cf-domains",       label: "Домены",        Icon: Globe },
    { tab: "cf-subscriptions", label: "Подписки",      Icon: ReceiptText },
    { tab: "cf-usage",         label: "Использование", Icon: Gauge },
    { tab: "cf-payments",      label: "Платежи",       Icon: CreditCard },
  ] },
  { title: "Интеграции", sec: "finance", items: [
    { tab: "infra-tokens",   label: "API токены",           Icon: KeyRound },
    { tab: "infra-settings", label: "Валюта и уведомления", Icon: SlidersHorizontal },
  ] },
  // ── Автоматизация: правила, события, ИИ
  { title: "Правила и события", sec: "automation", items: [
    { tab: "automation",    label: "Правила",     Icon: Workflow },
    { tab: "notifications", label: "Уведомления", Icon: Bell },
  ] },
  { title: "Искусственный интеллект", sec: "automation", items: [
    { tab: "assistant",  label: "Ассистент",     Icon: Bot },
    { tab: "support-ai", label: "AI-провайдеры", Icon: Cpu },
  ] },
  // ── Поддержка: клиенты
  { title: "Bedolaga", sec: "support", items: [
    { tab: "support-chats",     label: "Чаты клиентов",     Icon: MessageCircle },
    { tab: "support-kanban",    label: "Канбан-доска",      Icon: Kanban },
    { tab: "support-dashboard", label: "Метрики поддержки", Icon: PieChart },
  ] },
  { title: "Решала", sec: "support", items: [
    { tab: "reshala-tickets", label: "Тикеты",   Icon: Ticket },
    { tab: "reshala-miniapp", label: "Mini App", Icon: Smartphone },
  ] },
  // ── Справка и доступы
  { title: "База знаний", sec: "reference", items: [
    { tab: "library", label: "Библиотека",         Icon: BookOpen },
    { tab: "vault",   label: "Хранилище секретов", Icon: Lock },
  ] },
  { title: "Каталог хостингов", sec: "reference", items: [
    { tab: "hostings-map",  label: "Карта хостингов", Icon: MapIcon },
    { tab: "hostings-list", label: "Хостинги",        Icon: Server },
  ] },
];

export const M3_FOOTER: M3Item[] = [{ tab: "settings", label: "Настройки", Icon: Settings2 }];

const TAB_DOMAIN = new Map<Tab, string>(NAV_TABS.map(i => [i.tab, i.domain]));

/** Текущий `data-skin` на :root, реактивно: ThemeTab меняет скин императивно
 *  (applySkin), поэтому слушаем сам атрибут, а не стор. */
export function useSkin(): string | undefined {
  const [skin, setSkin] = useState(() => document.documentElement.dataset.skin);
  useEffect(() => {
    const el = document.documentElement;
    const obs = new MutationObserver(() => setSkin(el.dataset.skin));
    obs.observe(el, { attributes: true, attributeFilter: ["data-skin"] });
    setSkin(el.dataset.skin);
    return () => obs.disconnect();
  }, []);
  return skin;
}

interface Props {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
}

export function M3Nav({ activeTab, onTabChange }: Props) {
  const { can } = usePermissions();
  // Как в макете: на узком экране (<1100px) drawer по умолчанию свёрнут.
  const [drawer, setDrawer] = useState(() => typeof window === "undefined" || window.innerWidth >= 1100);

  const allowed = (t: Tab) => {
    const d = TAB_DOMAIN.get(t);
    return !d || can(`${d}.view`);
  };
  const groups = M3_GROUPS
    .map(g => ({ ...g, items: g.items.filter(i => allowed(i.tab)) }))
    .filter(g => g.items.length > 0);
  const sections = M3_SECTIONS.filter(s => groups.some(g => g.sec === s.id));
  const footer = M3_FOOTER.filter(i => allowed(i.tab));
  const section = groups.find(g => g.items.some(i => i.tab === activeTab))?.sec ?? null;
  const secDef = M3_SECTIONS.find(s => s.id === section);

  const pickSection = (id: M3Section) => {
    const first = groups.find(g => g.sec === id)?.items[0];
    if (!first) return;
    setDrawer(d => (section === id ? !d : true));
    if (section !== id) onTabChange(first.tab);
  };

  return (
    <div className="ni-sidebar ni-m3nav" data-testid="m3-nav">
      <nav className="ni-m3rail" aria-label="Разделы">
        <button type="button" className="ni-m3rail-menu" title="Меню" aria-label="Меню"
          aria-expanded={drawer && !!section} onClick={() => setDrawer(d => !d)}>
          <Menu size={24} />
        </button>
        {allowed("deploy") && (
          <button type="button" className="ni-m3fab" title="Новый деплой" aria-label="Новый деплой"
            onClick={() => onTabChange("deploy")}>
            <Rocket size={24} />
          </button>
        )}
        {sections.map(({ id, short, label, Icon }) => {
          const on = section === id;
          return (
            <button key={id} type="button" title={label} data-testid={`m3-section-${id}`}
              className={`ni-m3rail-item ${on ? "active" : ""}`} aria-current={on ? "true" : undefined}
              onClick={() => pickSection(id)}>
              <span className="ni-m3rail-pill"><Icon size={22} /></span>
              <span className="ni-m3rail-label">{short}</span>
            </button>
          );
        })}
        <div style={{ flex: 1 }} />
        {footer.map(({ tab, label, Icon }) => {
          const on = activeTab === tab;
          return (
            <button key={tab} type="button" title={label}
              className={`ni-m3rail-item ${on ? "active" : ""}`} aria-current={on ? "page" : undefined}
              onClick={() => onTabChange(tab)}>
              <span className="ni-m3rail-pill"><Icon size={22} /></span>
              <span className="ni-m3rail-label">{label}</span>
            </button>
          );
        })}
      </nav>

      {drawer && section && (
        <nav className="ni-m3drawer" aria-label={secDef?.label}>
          <div className="ni-m3drawer-head">
            <span className="ni-m3drawer-logo"><Server size={20} /></span>
            <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
              <span style={{ fontSize: 15, fontWeight: 600, color: "var(--t-hi)" }}>Node Assistant</span>
              <span style={{ fontSize: 12, color: "var(--t-mid)" }}>{secDef?.label}</span>
            </div>
          </div>
          {groups.filter(g => g.sec === section).map(g => (
            <div key={g.title} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span className="ni-m3drawer-group">{g.title}</span>
              {g.items.map(({ tab, label, Icon }) => {
                const on = activeTab === tab;
                return (
                  <button key={tab} type="button" className={`navitem ${on ? "active" : ""}`}
                    aria-current={on ? "page" : undefined} onClick={() => onTabChange(tab)}>
                    <Icon size={20} style={{ flex: "none" }} />
                    <span className="trunc">{label}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
      )}
    </div>
  );
}
