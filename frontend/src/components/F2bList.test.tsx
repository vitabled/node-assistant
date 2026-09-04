import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { F2bList } from "./F2bList";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  localStorage.clear();
  // Initial GET /api/f2b-list resolves with two entries.
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ entries: ["203.0.113.10", "198.51.100.0/24"] }),
  }));
});

describe("F2bList", () => {
  it("renders the always-visible action buttons and the textarea", async () => {
    render(<F2bList />);

    // Главные кнопки видимы сразу (не скрыты и не gated на loading).
    expect(screen.getByRole("button", { name: /Сохранить список/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Собрать с нод/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Загрузить на ноды/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Импорт/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Экспорт/ })).toBeInTheDocument();

    // textarea с прежним data-testid присутствует после загрузки.
    expect(await screen.findByTestId("f2b-textarea")).toBeInTheDocument();
  });

  it("shows the address counter and search field", async () => {
    render(<F2bList />);
    await screen.findByTestId("f2b-textarea");

    expect(screen.getByText("2 адреса")).toBeInTheDocument();
    expect(screen.getByLabelText("Поиск по списку")).toBeInTheDocument();
  });
});
