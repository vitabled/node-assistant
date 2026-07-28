// Человеческие названия для сторов экспорта и группировка их по разделам панели.
//
// Бэкенд оперирует именами файлов (`hostings.json`) и секциями настроек
// (`settings:haproxy`) — пользователю это ничего не говорит, поэтому раскладка
// живёт здесь. Незнакомый стор (появился на бэкенде раньше, чем тут) не
// теряется: он попадает в группу «Прочее» под своим техническим именем.
export interface StoreDef { id: string; label: string }
export interface StoreGroup { title: string; items: StoreDef[] }

const LABELS: Record<string, string> = {
  "settings.json": "Настройки (целиком)",
  "templates.json": "Шаблоны конфигов",
  "traffic_rules.json": "Правила трафика",
  "subscriptions.json": "Подписки",
  "domains.json": "Домены (SSL)",
  "hosts.json": "Хост-шаблоны Remnawave",
  "checkers.json": "Инстансы xray-checker",
  "rules.json": "Правила автоматизации",
  "testservers.json": "Серверы для тестов",
  "certwarden.json": "Certwarden",
  "hostings.json": "Хостинги (каталог)",
  "panel_groups.json": "Группы синхронизации панелей",
  "config_templates.json": "Пользовательские конфиги",
  "prompt_presets.json": "Промпт-пресеты ассистента",
  "stat_widgets.json": "Виджеты статистики",
  "vault.json": "Хранилище (без секретов)",
  "netbird.json": "Netbird",
};

const SECTION_LABELS: Record<string, string> = {
  remnawave: "Remnawave: панель",
  remnawave_registry: "Remnawave: реестр панелей",
  deploy_defaults: "Деплой: умолчания",
  optimization: "Оптимизация ОС",
  xray_checker: "Мониторинг (xray-checker)",
  mcp: "MCP-сервер",
  ai: "Ассистент и шлюз",
  haproxy: "HAProxy (NodeFlow)",
  cloudflare: "Cloudflare",
  appearance: "Оформление",
  auto_backup: "Автобэкап",
};

/** Порядок групп = порядок разделов в панели, а не алфавит. */
const GROUPS: { title: string; ids: string[] }[] = [
  { title: "Remnawave", ids: ["settings:remnawave", "settings:remnawave_registry", "hosts.json", "templates.json", "config_templates.json", "panel_groups.json"] },
  { title: "Деплой и SSL", ids: ["settings:deploy_defaults", "settings:optimization", "domains.json", "certwarden.json"] },
  { title: "Мониторинг и трафик", ids: ["settings:xray_checker", "checkers.json", "subscriptions.json", "traffic_rules.json", "testservers.json", "stat_widgets.json"] },
  { title: "Автоматизация и ассистент", ids: ["rules.json", "settings:ai", "settings:mcp", "prompt_presets.json"] },
  { title: "HAProxy", ids: ["settings:haproxy"] },
  { title: "Инфраструктура и справка", ids: ["hostings.json", "vault.json", "settings:cloudflare", "netbird.json"] },
  { title: "Прочее", ids: ["settings:appearance", "settings:auto_backup", "settings.json"] },
];

export function buildGroups(stores: string[], sections: string[]): StoreGroup[] {
  const known = new Set<string>();
  const label = (id: string) => id.startsWith("settings:")
    ? (SECTION_LABELS[id.slice(9)] || id)
    : (LABELS[id] || id);

  const available = new Set([...stores, ...sections.map(s => `settings:${s}`)]);
  const out: StoreGroup[] = [];
  for (const g of GROUPS) {
    const items = g.ids.filter(id => available.has(id)).map(id => ({ id, label: label(id) }));
    items.forEach(i => known.add(i.id));
    if (items.length) out.push({ title: g.title, items });
  }
  // Всё, что бэкенд знает, а раскладка — ещё нет: показать, а не потерять.
  const rest = [...available].filter(id => !known.has(id));
  if (rest.length) out.push({ title: "Не разложено", items: rest.map(id => ({ id, label: label(id) })) });
  return out;
}
