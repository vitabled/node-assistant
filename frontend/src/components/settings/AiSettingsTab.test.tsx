import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AiSettingsTab } from "./AiSettingsTab";

const CONFIG = {
  enabled: true, provider: "openai", base_url: "https://gw/v1", model: "m",
  max_steps: 4, readonly: true, has_key: true, gateway: "cliproxy",
  active_preset_id: "default",
  // Веб-доступ. `has_web_key: false` — иначе бейдж «(сохранён)» был бы на двух
  // полях сразу и getByText(/сохранён/) перестал бы быть однозначным.
  web_enabled: true, web_provider: "duckduckgo", web_base_url: "",
  web_max_results: 5, web_needs_key: false, has_web_key: false,
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

/** Тело последнего POST на /api/ai/config. */
function lastPost(fn: ReturnType<typeof installFetch>) {
  const calls = fn.mock.calls.filter(([u, o]: any[]) => u === "/api/ai/config" && o?.method === "POST");
  expect(calls.length).toBeGreaterThan(0);
  return JSON.parse(calls[calls.length - 1][1].body);
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
  // дефолты pydantic — включая веб-настройки, которые живут в том же документе.
  it("POSTs the whole config object", async () => {
    const fn = installFetch();
    render(<AiSettingsTab />);
    fireEvent.click(await screen.findByText("Сохранить"));
    await waitFor(() => {
      const body = lastPost(fn);
      for (const k of ["enabled", "provider", "base_url", "model", "max_steps", "gateway",
                       "readonly", "web_enabled", "web_provider", "web_base_url", "web_max_results"]) {
        expect(body).toHaveProperty(k);
      }
      expect(body).not.toHaveProperty("api_key");     // пустое поле не затирает сохранённый
      expect(body).not.toHaveProperty("web_api_key"); // то же правило для ключа поиска
    });
  });

  it("carries the picked preset into the config POST", async () => {
    const fn = installFetch();
    render(<AiSettingsTab />);
    const select = await screen.findByDisplayValue(/По умолчанию/); // «… · встроенный»
    fireEvent.change(select, { target: { value: "precise" } });
    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => expect(lastPost(fn).active_preset_id).toBe("precise"));
  });

  // ── Интернет ───────────────────────────────────────────────
  it("hides the key and instance fields for the keyless provider", async () => {
    installFetch();
    render(<AiSettingsTab />);
    expect(await screen.findByText("Интернет")).toBeInTheDocument();
    expect(screen.queryByText(/Ключ поисковика/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Адрес инстанса/)).not.toBeInTheDocument();
  });

  // Ключевой сценарий: серверный `web_needs_key` описывает СОХРАНЁННЫЙ провайдер,
  // поэтому поле обязано появиться сразу после выбора — иначе ключ негде ввести.
  it("reveals the key field as soon as a key-based provider is picked, before saving", async () => {
    installFetch();
    render(<AiSettingsTab />);
    const sel = await screen.findByDisplayValue(/DuckDuckGo/);
    fireEvent.change(sel, { target: { value: "tavily" } });
    expect(await screen.findByText(/Ключ поисковика/)).toBeInTheDocument();
    expect(screen.getByText(/Пустое поле не затирает сохранённый ключ/)).toBeInTheDocument();
  });

  it("asks for the instance URL only for searxng", async () => {
    installFetch();
    render(<AiSettingsTab />);
    const sel = await screen.findByDisplayValue(/DuckDuckGo/);
    fireEvent.change(sel, { target: { value: "searxng" } });
    expect(await screen.findByText(/Адрес инстанса/)).toBeInTheDocument();
    expect(screen.queryByText(/Ключ поисковика/)).not.toBeInTheDocument();
  });

  it("sends the search key only when one was typed", async () => {
    const fn = installFetch();
    render(<AiSettingsTab />);
    fireEvent.change(await screen.findByDisplayValue(/DuckDuckGo/), { target: { value: "brave" } });
    fireEvent.change(await screen.findByPlaceholderText(/ключ провайдера поиска/), { target: { value: " tvly-x " } });
    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => {
      const body = lastPost(fn);
      expect(body.web_api_key).toBe("tvly-x"); // без обрамляющих пробелов
      expect(body.web_provider).toBe("brave");
    });
  });

  it("keeps the web settings in the payload even when web access is off", async () => {
    const fn = installFetch();
    render(<AiSettingsTab />);
    fireEvent.click(await screen.findByRole("switch", { name: "Разрешить поиск и чтение страниц" }));
    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => {
      const body = lastPost(fn);
      expect(body.web_enabled).toBe(false);
      expect(body.web_provider).toBe("duckduckgo"); // не потерялся вместе со скрытыми полями
    });
  });

  // ── Режим записи ───────────────────────────────────────────
  it("explains what write mode allows, and only while it is on", async () => {
    installFetch();
    render(<AiSettingsTab />);
    const ro = await screen.findByRole("switch", { name: "Только чтение" });
    expect(screen.queryByText(/Запись включена/)).not.toBeInTheDocument();

    fireEvent.click(ro);
    expect(await screen.findByText(/Запись включена/)).toBeInTheDocument();
    expect(screen.getByText(/Всегда запрещены/)).toBeInTheDocument();
    // Асимметрия из ai_tools: после веб-вызова запись в панель блокируется, а
    // заметки остаются — предупреждение обязано говорить именно это.
    expect(screen.getByText(/После обращения в интернет/)).toBeInTheDocument();

    fireEvent.click(ro);
    await waitFor(() => expect(screen.queryByText(/Запись включена/)).not.toBeInTheDocument());
  });

  it("POSTs readonly=false once write mode is enabled", async () => {
    const fn = installFetch();
    render(<AiSettingsTab />);
    fireEvent.click(await screen.findByRole("switch", { name: "Только чтение" }));
    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => expect(lastPost(fn).readonly).toBe(false));
  });
});
