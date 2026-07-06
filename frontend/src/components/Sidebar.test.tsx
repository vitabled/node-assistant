import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

function renderSidebar(active = "deploy" as const) {
  const onTabChange = vi.fn();
  render(<Sidebar activeTab={active} onTabChange={onTabChange} collapsed={false} onToggle={() => {}} />);
  return { onTabChange };
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

  it("expands the infra group and exposes its tabs", () => {
    renderSidebar();
    fireEvent.click(screen.getByText("Инфра-биллинг"));
    expect(screen.getByText("Провайдеры")).toBeInTheDocument();
    expect(screen.getByText("API токены")).toBeInTheDocument();
  });
});
