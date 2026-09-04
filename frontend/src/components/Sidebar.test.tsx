import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Привилегии подменяем целиком: сайдбар должен проверяться без сети и без
// состояния загрузки. `allow` читается ВНУТРИ can(), то есть в момент рендера, а
// не при создании мока — иначе фабрика `vi.mock` (она поднимается выше объявления)
// упала бы на TDZ.
let allow: (permission: string) => boolean = () => true;
vi.mock("../auth/usePermissions", () => ({
  usePermissions: () => ({
    user: null, permissions: [], loading: false,
    can: (p: string) => allow(p),
  }),
}));

import { Sidebar, NAV_TABS, tabPermission, mergeNavOrder } from "./Sidebar";

function renderSidebar(active = "deploy" as const) {
  const onTabChange = vi.fn();
  const utils = render(
    <Sidebar activeTab={active} onTabChange={onTabChange} collapsed={false} onToggle={() => {}} />);
  return { onTabChange, ...utils };
}

/**
 * Пункты одной группы навигации, в порядке рендера.
 *
 * Группы рендерятся ПЛОСКО: заголовок `<p class="micro">`, затем кнопки `.navitem`
 * — все сиблинги в одной колонке (Sidebar.tsx). Поэтому идём по
 * `nextElementSibling` до следующего заголовка, пропуская разделители.
 *
 * Скоуп обязателен: неограниченный `getByText` по всему сайдбару НЕ проверяет,
 * в какой группе оказался пункт (именно на этом два прежних теста были зелёными
 * при неверном составе групп).
 */
function groupItems(name: string): string[] {
  const header = screen
    .getAllByText(name)
    .find((el) => el.tagName === "P" && el.classList.contains("micro"));
  if (!header) throw new Error(`заголовок группы «${name}» не найден`);
  const out: string[] = [];
  for (let el = header.nextElementSibling; el; el = el.nextElementSibling) {
    if (el.tagName === "P" && el.classList.contains("micro")) break;
    if (el.classList.contains("navitem")) out.push((el.textContent ?? "").trim());
  }
  return out;
}

afterEach(() => { cleanup(); allow = () => true; });

describe("Sidebar", () => {
  it("renders the main navigation items", () => {
    renderSidebar();
    // «Дашборд» встречается дважды (главная навигация + BEDOLAGA→Поддержка),
    // поэтому проверяем присутствие, а не единственность.
    for (const label of ["Дашборд", "Деплой ноды", "Управление SSL", "Шаблоны", "Хосты", "Трафик", "Настройки"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("no longer renders the removed infra Sign-in tab", () => {
    renderSidebar();
    expect(screen.queryByText("Sign-in")).not.toBeInTheDocument();
  });

  // S1: три верхних раздела группируют навигацию (состав групп/привилегий не тронут).
  it("groups navigation under the three top-level sections", () => {
    renderSidebar();
    expect(screen.getByTestId("nav-section-monitoring")).toHaveTextContent("Мониторинг");
    expect(screen.getByTestId("nav-section-deploy")).toHaveTextContent("Развёртывание");
    expect(screen.getByTestId("nav-section-settings")).toHaveTextContent("Сервисы");
  });

  it("calls onTabChange with the tab id when a nav item is clicked", () => {
    const { onTabChange } = renderSidebar();
    fireEvent.click(screen.getByText("Настройки"));
    expect(onTabChange).toHaveBeenCalledWith("settings");
  });

  it("renders the Справка group with its exact tabs, in order", () => {
    renderSidebar();
    expect(groupItems("Справка")).toEqual([
      "Карта хостингов", "Хостинги", "Анализ подписки", "Копия сайта", "Библиотека", "Хранилище",
    ]);
  });

  // Инфра-биллинг — плоская секция, аккордеона нет (CLAUDE.md §4c).
  it("renders the infra group flat, with its exact tabs", () => {
    renderSidebar();
    expect(groupItems("Инфра-биллинг")).toEqual([
      "Dashboard", "Провайдеры", "Проекты", "Услуги и тарифы",
      "Платежи", "Настройки биллинга", "API токены",
    ]);
  });

  // Строгие проверки состава И порядка групп. Падают при переносе пункта между
  // группами и при перестановке внутри группы — в отличие от прежних проверок
  // через неограниченный getByText.
  it("renders the Управление group with its exact tabs, in order", () => {
    renderSidebar();
    expect(groupItems("Управление")).toEqual([
      "Дашборд", "Деплой ноды", "Управление SSL", "Fail2Ban", "Шаблоны", "Хосты", "Трафик", "Мосты", "Авто",
    ]);
  });

  // Волна 6, План A: три редактора конфигов переехали сюда из «Управления».
  it("renders the Обходы БС group with its exact tabs, in order", () => {
    renderSidebar();
    expect(groupItems("Обходы БС")).toEqual(["REGRU хостинг", "Beeline CDN", "Подсети"]);
  });

  it("renders the Remnawave group with its exact tabs, in order", () => {
    renderSidebar();
    expect(groupItems("Remnawave")).toEqual([
      "Установка", "Страницы подписок", "Переменные", "Резервное копирование",
      "Миграция", "Профили", "Mihomo", "Конфиги",
    ]);
  });

  // «Настройки»/«Уведомления» живут в футере, вне групп — заодно фиксирует, что
  // обход групп не «утекает» за последнюю группу.
  it("keeps the footer items out of every nav group", () => {
    renderSidebar();
    for (const group of ["Управление", "Статистика", "Автоматизация", "Remnawave", "Справка", "Инфра-биллинг"]) {
      expect(groupItems(group)).not.toContain("Настройки");
      expect(groupItems(group)).not.toContain("Уведомления");
    }
  });

  it("dispatches the rw tab id when a Remnawave item is clicked", () => {
    const { onTabChange } = renderSidebar();
    fireEvent.click(screen.getByText("Установка"));
    expect(onTabChange).toHaveBeenCalledWith("rw-install");
  });
});

// ── гейт привилегий (Волна 13) ────────────────────────────────
//
// ⚠️ Это КОСМЕТИКА: настоящая проверка живёт на сервере (`require_identity`).
// Тест сторожит другое — что таблица «пункт → домен» действительно применяется и
// что скрытие не оставляет мусора (заголовок без пунктов, пустой футер).
describe("Sidebar permission gate", () => {
  it("hides items whose domain lacks <домен>.view", () => {
    allow = p => p !== "vault.view";
    renderSidebar();
    expect(screen.queryByText("Хранилище")).toBeNull();
    // соседи по группе остаются
    expect(groupItems("Справка")).toEqual([
      "Карта хостингов", "Хостинги", "Анализ подписки", "Копия сайта", "Библиотека",
    ]);
  });

  it("drops a whole group when every item in it is hidden", () => {
    allow = p => p !== "cloudflare.view";
    renderSidebar();
    expect(screen.queryByText("Cloudflare")).toBeNull();   // и заголовка нет
    expect(screen.queryByText("Домены")).toBeNull();
    // соседние группы не пострадали
    expect(screen.getByText("Инфра-биллинг")).toBeInTheDocument();
  });

  it("gates the footer items too, and omits the footer when both are hidden", () => {
    allow = p => p !== "settings.view";
    const { unmount } = renderSidebar();
    expect(screen.queryByText("Настройки")).toBeNull();
    expect(screen.getByText("Уведомления")).toBeInTheDocument();
    unmount();

    allow = p => p !== "settings.view" && p !== "automation.view";
    renderSidebar();
    expect(screen.queryByText("Настройки")).toBeNull();
    expect(screen.queryByText("Уведомления")).toBeNull();
  });

  it("renders nothing but the brand for a user without a single view privilege", () => {
    allow = () => false;
    renderSidebar();
    expect(screen.queryByText("Дашборд")).toBeNull();
    expect(screen.queryByText("Управление")).toBeNull();
    // бренд остаётся — иначе колонка выглядит как сбой отрисовки
    expect(screen.getByText("Node Assistant")).toBeInTheDocument();
  });

  // Одна и та же таблица кормит сайдбар и откат недоступной вкладки в App.
  it("maps every nav item to its <домен>.view permission", () => {
    expect(tabPermission("vault")).toBe("vault.view");
    expect(tabPermission("templates")).toBe("deploy.view");    // не свой домен
    expect(tabPermission("traffic")).toBe("automation.view");  // лимиты трафика
    expect(tabPermission("dashboard")).toBe("monitoring.view");
    for (const item of NAV_TABS) {
      expect(tabPermission(item.tab)).toBe(`${item.domain}.view`);
    }
    // Все пункты уникальны: дубль тихо ломал бы «первую доступную вкладку».
    expect(new Set(NAV_TABS.map(i => i.tab)).size).toBe(NAV_TABS.length);
  });
});

describe("nav order (Wave-5 PR-3)", () => {
  it("mergeNavOrder: сохранённый порядок применяется, новые табы — дефолтом", () => {
    const saved = [
      { title: "Управление", tabs: ["hosts", "dashboard"] },   // hosts перед dashboard
      { title: "Неизвестная", tabs: ["ghost"] },               // выпадает целиком
    ];
    const merged = mergeNavOrder(saved);
    const main = merged.find(g => g.title === "Управление")!;
    expect(main.tabs[0]).toBe("hosts");
    expect(main.tabs[1]).toBe("dashboard");
    expect(main.tabs).toContain("deploy");      // новый таб дописан в конец группы
    expect(merged.find(g => g.title === "Неизвестная")).toBeUndefined();
    // остальные группы на месте
    expect(merged.find(g => g.title === "Справка")).toBeTruthy();
  });

  it("mergeNavOrder(null) — дефолтный порядок", () => {
    const merged = mergeNavOrder(null);
    const main = merged.find(g => g.title === "Управление")!;
    expect(main.tabs[0]).toBe("dashboard");
  });

  it("кнопка-карандаш включает режим, дискета сохраняет в localStorage", async () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId("nav-edit-toggle"));
    expect(screen.getByTitle("Сохранить порядок разделов")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("nav-edit-toggle"));
    // сохранённый порядок записался
    const raw = localStorage.getItem("ni_nav_order") ?? localStorage.getItem("ni_nav_order_null");
    const stored = Object.keys(localStorage).filter(k => k.startsWith("ni_nav_order"));
    expect(stored.length).toBeGreaterThan(0);
    expect(raw ?? localStorage.getItem(stored[0])).toContain("dashboard");
  });
});
