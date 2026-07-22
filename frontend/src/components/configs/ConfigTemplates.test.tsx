import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConfigTemplates } from "./ConfigTemplates";

const TPL = {
  id: "t1", name: "Мой xray", kind: "xray-json",
  content_json: {}, content_yaml: null, note: null,
};

const PANELS = {
  panels: [
    { id: "pa", name: "Прод", panel_url: "https://a" },
    { id: "pb", name: "Тест", panel_url: "https://b" },
  ],
  active_panel_id: "pa",
};

function installFetch(panels: any = PANELS) {
  const fn = vi.fn(async (url: string, opts?: any) => {
    if (url === "/api/settings/remnawave/panels") return { ok: true, json: async () => panels } as any;
    if (url === "/api/config-templates") return { ok: true, json: async () => [TPL] } as any;
    if (url.startsWith("/api/config-templates/t1/export"))
      return { ok: true, json: async () => ({ uuid: "u1" }) } as any;
    throw new Error(`unmocked ${url}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

const exportCalls = (fn: any) =>
  fn.mock.calls.map(([u]: any[]) => u as string).filter((u: string) => u.includes("/export"));

afterEach(() => vi.restoreAllMocks());

describe("ConfigTemplates — panel selector", () => {
  it("exports without panel_id by default (server picks the main panel)", async () => {
    const fn = installFetch();
    render(<ConfigTemplates />);
    fireEvent.click(await screen.findByTitle("Отправить в панель"));
    await waitFor(() => expect(exportCalls(fn)).toEqual(["/api/config-templates/t1/export"]));
  });

  it("carries the picked panel into the export request", async () => {
    const fn = installFetch();
    render(<ConfigTemplates />);
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "pb" } });
    fireEvent.click(screen.getByTitle("Отправить в панель"));
    await waitFor(() =>
      expect(exportCalls(fn)).toEqual(["/api/config-templates/t1/export?panel_id=pb"]));
  });

  // Picking a sync source must not re-point the account's main panel — that is a
  // separate, deliberate action in Settings / Установка.
  it("never calls the activate endpoint", async () => {
    const fn = installFetch();
    render(<ConfigTemplates />);
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "pb" } });
    await waitFor(() => expect(select).toHaveValue("pb"));
    expect(fn.mock.calls.some(([u]: any[]) => String(u).includes("/activate"))).toBe(false);
  });

  it("hides the selector when there is only one panel", async () => {
    installFetch({ panels: [PANELS.panels[0]], active_panel_id: "pa" });
    render(<ConfigTemplates />);
    await screen.findByTitle("Отправить в панель");
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});
