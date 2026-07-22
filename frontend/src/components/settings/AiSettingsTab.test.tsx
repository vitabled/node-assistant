import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AiSettingsTab } from "./AiSettingsTab";

const CONFIG = {
  enabled: true, provider: "openai", base_url: "https://gw/v1", model: "m",
  max_steps: 4, readonly: true, has_key: true, gateway: "cliproxy",
  active_preset_id: "default",
};

const PRESETS = [
  { id: "default", name: "По умолчанию", text: "t", builtin: true },
  { id: "precise", name: "Точный", text: "t2", builtin: true },
];

function installFetch(models: string[] = []) {
  const fn = vi.fn(async (url: string, opts?: any) => {
    if (url === "/api/ai/config" && opts?.method === "POST")
      return { ok: true, json: async () => ({ ...CONFIG, ...JSON.parse(opts.body) }) } as any;
    if (url === "/api/ai/config") return { ok: true, json: async () => CONFIG } as any;
    if (url === "/api/ai/models") return { ok: true, json: async () => ({ models }) } as any;
    if (url === "/api/ai/prompts") return { ok: true, json: async () => PRESETS } as any;
    throw new Error(`unmocked ${url}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

afterEach(() => vi.restoreAllMocks());

describe("AiSettingsTab", () => {
  it("renders the provider config with the has_key badge", async () => {
    installFetch();
    render(<AiSettingsTab />);
    expect(await screen.findByText(/Встроенный ИИ-агент/)).toBeInTheDocument();
    expect(screen.getByText(/сохранён/)).toBeInTheDocument();
    expect(screen.getByText("Base URL")).toBeInTheDocument();
  });

  // Каталог моделей больше не гейтится на gateway === "cliproxy" на клиенте:
  // решает бэкенд, а пустой список сам по себе означает «вводите вручную».
  it("offers a model selector when the catalogue is not empty", async () => {
    installFetch(["gpt-5.6", "claude-opus-4.66"]);
    render(<AiSettingsTab />);
    // findBy* (а не waitFor с дефолтным 1 с): каталог приезжает через две
    // последовательные загрузки — конфиг, затем модели, — и под параллельной
    // нагрузкой полного прогона в секунду это не всегда укладывается.
    await screen.findByRole("option", { name: "gpt-5.6" }, { timeout: 5000 });
  });

  it("falls back to a free-text model input when the catalogue is empty", async () => {
    installFetch([]);
    render(<AiSettingsTab />);
    expect(await screen.findByText(/список пуст/)).toBeInTheDocument();
  });

  // Ручка делает full-replace: частичное тело сбросило бы остальные поля в
  // дефолты pydantic.
  it("POSTs the whole config object", async () => {
    const fn = installFetch();
    render(<AiSettingsTab />);
    fireEvent.click(await screen.findByText("Сохранить"));
    await waitFor(() => {
      const post = fn.mock.calls.find(([u, o]: any[]) => u === "/api/ai/config" && o?.method === "POST");
      expect(post).toBeTruthy();
      const body = JSON.parse(post![1].body);
      for (const k of ["enabled", "provider", "base_url", "model", "max_steps", "gateway"]) {
        expect(body).toHaveProperty(k);
      }
      expect(body).not.toHaveProperty("api_key"); // пустое поле ключа не затирает сохранённый
    });
  });

  it("carries the picked preset into the config POST", async () => {
    const fn = installFetch();
    render(<AiSettingsTab />);
    const select = await screen.findByDisplayValue(/По умолчанию/); // «… · встроенный»
    fireEvent.change(select, { target: { value: "precise" } });
    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => {
      const post = fn.mock.calls.find(([u, o]: any[]) => u === "/api/ai/config" && o?.method === "POST");
      expect(JSON.parse(post![1].body).active_preset_id).toBe("precise");
    });
  });
});
