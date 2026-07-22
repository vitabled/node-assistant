import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

function renderSidebar(active = "deploy" as const) {
  const onTabChange = vi.fn();
  render(<Sidebar activeTab={active} onTabChange={onTabChange} collapsed={false} onToggle={() => {}} />);
  return { onTabChange };
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

afterEach(cleanup);

describe("Sidebar", () => {
  it("renders the main navigation items", () => {
    renderSidebar();
    for (const label of ["Дешборд", "Деплой ноды", "Управление SSL", "Шаблоны", "Хосты", "Трафик", "Настройки"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("no longer renders the removed infra Sign-in tab", () => {
    renderSidebar();
    expect(screen.queryByText("Sign-in")).not.toBeInTheDocument();
  });

  it("calls onTabChange with the tab id when a nav item is clicked", () => {
    const { onTabChange } = renderSidebar();
    fireEvent.click(screen.getByText("Настройки"));
    expect(onTabChange).toHaveBeenCalledWith("settings");
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
      "Дешборд", "Деплой ноды", "Управление SSL", "Шаблоны", "Хосты", "Трафик",
    ]);
  });

  // Волна 6, План A: три редактора конфигов переехали сюда из «Управления».
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
