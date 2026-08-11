import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AutoTemplate } from "./AutoTemplate";

// Wave-4 PR-7: конфигуратор XRAY_JSON — валидация injectHosts, raw-режим, save flow.

function installFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url === "/api/xray-templates" && !init?.method) {
      return new Response(JSON.stringify({ templates: [{ uuid: "t1", name: "Main" }] }), { status: 200 });
    }
    if (url === "/api/xray-templates" && init?.method === "POST") {
      return new Response(JSON.stringify({ uuid: "t-new", name: "Авто-шаблон" }), { status: 201 });
    }
    if (url.startsWith("/api/xray-templates/") && init?.method === "PUT") {
      return new Response("{}", { status: 200 });
    }
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

describe("AutoTemplate", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("injectHosts: пустой values блокирует сохранение, заполненный — нет", async () => {
    installFetch();
    render(<AutoTemplate />);
    fireEvent.click(await screen.findByText("Группа injectHosts"));
    // свежая группа uuids без values → предупреждение и блокировка
    expect(await screen.findByText(/selector.uuids требует values/)).toBeInTheDocument();
    expect(screen.getByText("Сохранить в панель")).toBeDisabled();
    // заполнили uuid → валидация прошла
    fireEvent.change(screen.getByPlaceholderText("8478b271-95d3-4312-85ae-ecf63fb53d1d"),
      { target: { value: "d31d6161-1315-4c1e-9a4b-141ab1c022f6" } });
    await waitFor(() => {
      expect(screen.queryByText(/требует values/)).toBeNull();
      expect(screen.queryByText(/ровно одно tag-поле/)).toBeNull();
      expect(screen.getByText("Сохранить в панель")).not.toBeDisabled();
    });
  });

  it("новый шаблон: POST создаёт, PUT пишет template_json", async () => {
    const calls = installFetch();
    render(<AutoTemplate />);
    await screen.findByText("Remnawave-директива");
    fireEvent.change(screen.getByPlaceholderText("Имя нового шаблона"),
      { target: { value: "Авто-шаблон" } });
    fireEvent.click(screen.getByText("Сохранить в панель"));

    await waitFor(() => expect(screen.getByText("Сохранено в панели")).toBeInTheDocument());
    const post = calls.find(c => c.url === "/api/xray-templates" && c.init?.method === "POST");
    const put = calls.find(c => c.init?.method === "PUT");
    expect(post).toBeTruthy();
    expect(put).toBeTruthy();
    expect(put!.url).toBe("/api/xray-templates/t-new");
    const body = JSON.parse(String(put!.init!.body));
    expect(body.template_json.routing.domainStrategy).toBe("AsIs");
    expect(body.template_json.outbounds.map((o: { tag: string }) => o.tag)).toEqual(["direct", "block"]);
  });

  it("JSON-режим: показывает документ и ловит ошибку парсинга", async () => {
    installFetch();
    render(<AutoTemplate />);
    await screen.findByText("Remnawave-директива");
    fireEvent.click(screen.getByText("JSON"));
    const ta = await screen.findByDisplayValue(/"domainStrategy": "AsIs"/);
    fireEvent.change(ta, { target: { value: "{битый json" } });
    fireEvent.click(screen.getByText("Форма"));
    expect(await screen.findByText(/Ошибка JSON/)).toBeInTheDocument();
  });
});
