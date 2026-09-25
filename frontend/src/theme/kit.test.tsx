/**
 * Тесты кита Axottle (src/theme/kit.tsx).
 *
 * Проверяют ровно то, что кит обещает: классы состояний, доступные имена, вызовы
 * колбэков и императивный API тостов. Дизайн (цвета/радиусы) живёт в CSS и меряется
 * отдельно — в contrast.test.ts, поэтому здесь стили не перепроверяются.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  AxoBreadcrumbs, AxoButton, AxoCard, AxoCheckbox, AxoConfirm, AxoDialog, AxoEmpty,
  AxoProgress, AxoSkeletonPage, AxoStatus, AxoSwitch, AxoTable, AxoTabs, AxoThemeToggle,
  AxoToastHost, axoClearToasts, axoDismiss, axoToast,
} from "./kit";

afterEach(() => { cleanup(); axoClearToasts(); vi.useRealTimers(); });

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.skin;
});

describe("AxoButton / AxoStatus", () => {
  it("даёт базовый класс, вариант, размер и disabled", () => {
    render(<AxoButton variant="quiet" size="lg" disabled>Сохранить</AxoButton>);
    const btn = screen.getByRole("button", { name: "Сохранить" });
    expect(btn.className).toContain("axo-btn");
    expect(btn.className).toContain("is-quiet");
    expect(btn.className).toContain("is-lg");
    expect(btn).toBeDisabled();
  });

  it("по умолчанию — сплошная основная без лишних классов", () => {
    render(<AxoButton>Ок</AxoButton>);
    expect(screen.getByRole("button", { name: "Ок" }).className.trim()).toBe("axo-btn");
  });

  it("статус носит класс тона, а точка — отдельным модификатором", () => {
    render(<>
      <AxoStatus tone="success">Активно</AxoStatus>
      <AxoStatus tone="danger" dot={false}>Упало</AxoStatus>
    </>);
    expect(screen.getByText("Активно").className).toContain("tone-success");
    expect(screen.getByText("Упало").className).toContain("tone-danger");
    expect(screen.getByText("Упало").className).toContain("no-dot");
  });
});

describe("поля и переключатели кита", () => {
  it("свитч — role=switch, отдаёт новое значение и не переключается сам", () => {
    const onChange = vi.fn();
    render(<AxoSwitch checked={false} onChange={onChange} label="Автопродление" />);
    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
    expect(sw).toHaveAttribute("aria-checked", "false"); // управляемый: состояние приходит пропом
  });

  it("чекбокс связан с подписью", () => {
    render(<AxoCheckbox label="Показывать логи" defaultChecked />);
    const box = screen.getByRole("checkbox", { name: "Показывать логи" }) as HTMLInputElement;
    expect(box.checked).toBe(true);
  });
});

describe("AxoTable / AxoTabs", () => {
  it("таблица рендерит шапку и строки", () => {
    render(
      <AxoTable head={["Нода", "Статус"]}>
        <tr><td className="axo-mono">de-fra-1</td><td>ok</td></tr>
      </AxoTable>,
    );
    expect(screen.getByRole("columnheader", { name: "Нода" })).toBeTruthy();
    expect(screen.getByText("de-fra-1")).toBeTruthy();
  });

  it("табы помечают активный и сообщают о смене", () => {
    const onChange = vi.fn();
    render(<AxoTabs tabs={[{ value: "a", label: "Все" }, { value: "b", label: "Проблемные" }]} value="a" onChange={onChange} />);
    expect(screen.getByRole("tab", { name: "Все" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Проблемные" }));
    expect(onChange).toHaveBeenCalledWith("b");
  });
});

describe("AxoDialog / AxoConfirm", () => {
  it("закрытый диалог не рендерится", () => {
    render(<AxoDialog open={false} onClose={() => {}} title="Скрыт">тело</AxoDialog>);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Escape и клик по слою закрывают, содержимое — нет", () => {
    const onClose = vi.fn();
    render(<AxoDialog open onClose={onClose} title="Заголовок">тело</AxoDialog>);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(screen.getByRole("dialog").parentElement!);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("подтверждение отдаёт намерение наружу, ничего не выполняя само", () => {
    const onConfirm = vi.fn();
    render(<AxoConfirm open onClose={() => {}} onConfirm={onConfirm} title="Удалить ноду?" danger confirmLabel="Удалить" />);
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe("тосты кита", () => {
  it("пустая стопка не создаёт DOM", () => {
    render(<AxoToastHost />);
    expect(document.querySelector(".axo-toast-host")).toBeNull();
  });

  it("push рисует тост, close убирает, host исчезает вместе с последним", () => {
    render(<AxoToastHost />);
    act(() => { axoToast({ tone: "danger", title: "Ошибка деплоя", text: "шаг 6 упал" }); });
    expect(screen.getByText("Ошибка деплоя")).toBeTruthy();
    expect(screen.getByText("шаг 6 упал")).toBeTruthy(); // перерисовку даёт внешний стор
    act(() => { fireEvent.click(screen.getByRole("button", { name: "Закрыть" })); });
    expect(document.querySelector(".axo-toast-host")).toBeNull();
  });

  it("ttl закрывает тост сам", () => {
    vi.useFakeTimers();
    render(<AxoToastHost />);
    let id = 0;
    act(() => { id = axoToast({ title: "Мигнул", ttl: 1000 }); });
    expect(screen.getByText("Мигнул")).toBeTruthy();
    act(() => { vi.advanceTimersByTime(1001); });
    expect(document.querySelector(".axo-toast-host")).toBeNull();
    act(() => { axoDismiss(id); }); // повторное закрытие не должно падать
    expect(document.querySelector(".axo-toast-host")).toBeNull();
  });
});

describe("скелетон, пустое состояние, крошки, прогресс", () => {
  it("скелетон страницы помечен как busy", () => {
    render(<AxoSkeletonPage />);
    expect(document.querySelector(".axo-skeleton-page")).toHaveAttribute("aria-busy", "true");
  });

  it("пустое состояние переключает опасный тон", () => {
    render(<AxoEmpty error title="Нет данных" text="Ноды ещё не задеплоены" />);
    expect(document.querySelector(".axo-empty")!.className).toContain("is-error");
    expect(screen.getByRole("heading", { name: "Нет данных" })).toBeTruthy();
  });

  it("крошки рендерят путь и текущий экран", () => {
    render(<AxoBreadcrumbs items={[{ label: "Инфраструктура" }, { label: "Ноды", onClick: () => {} }]} current="de-fra-1" />);
    expect(screen.getByText("Инфраструктура")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ноды" })).toBeTruthy();
    expect(screen.getByText("de-fra-1")).toHaveAttribute("aria-current", "page");
  });

  it("прогресс считает проценты и отдаёт их в aria", () => {
    render(<AxoProgress value={3} max={8} label="Ноды" />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "3");
    expect(screen.getByText("38%")).toBeTruthy();
    expect(document.querySelector<HTMLElement>(".axo-progress-fill")!.style.width).toBe("37.5%");
  });

  it("карточка показывает надзаголовок, подвал и класс интерактивности", () => {
    render(<AxoCard title="Трафик" subtitle="за сутки" interactive footer={<span>итого</span>}>тело</AxoCard>);
    expect(screen.getByRole("heading", { name: "Трафик" })).toBeTruthy();
    expect(screen.getByText("за сутки")).toBeTruthy();
    expect(screen.getByText("итого")).toBeTruthy();
    expect(document.querySelector(".axo-card")!.className).toContain("is-interactive");
  });
});

describe("AxoThemeToggle", () => {
  it("переключает dark↔light, применяет data-theme и помнит выбор для аккаунта", () => {
    document.documentElement.dataset.theme = "dark";
    render(<AxoThemeToggle accountId="acc1" />);
    const btn = screen.getByRole("button", { name: "Светлая тема" });
    fireEvent.click(btn);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("ni_thememode_acc1")).toBe("light");
    // Подпись пересчиталась: теперь кнопка предлагает вернуть тёмную.
    fireEvent.click(screen.getByRole("button", { name: "Тёмная тема" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("ni_thememode_acc1")).toBe("dark");
  });
});
