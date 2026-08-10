import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ScanDomainsModal } from "./ScanDomainsModal";

// Wave-4 PR-3: «Авто» — скан доменов сервера и добавление выбранных.

const FOUND = {
  domains: [
    { domain: "node1.example.com", sources: ["certbot", "nginx"] },
    { domain: "mask.example.com", sources: ["env"] },
  ],
};

function installFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url === "/api/certs/scan-domains") {
      return new Response(JSON.stringify(FOUND), { status: 200 });
    }
    return new Response("{}", { status: 201 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const DEFAULTS = { ip: "1.2.3.4", ssh_user: "root", ssh_password: "pw", ssh_port: "22" };

describe("ScanDomainsModal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("сканирует, показывает домены с источниками, добавляет выбранные", async () => {
    const calls = installFetch();
    const onAdded = vi.fn();
    const onClose = vi.fn();
    render(<ScanDomainsModal defaults={DEFAULTS} onClose={onClose} onAdded={onAdded} />);

    fireEvent.click(screen.getByText("Сканировать домены"));
    // найденные домены с чипами источников
    expect(await screen.findByText("node1.example.com")).toBeInTheDocument();
    expect(screen.getByText("certbot")).toBeInTheDocument();
    expect(screen.getByText("env")).toBeInTheDocument();
    // запрос ушёл с кредами из defaults
    const scan = calls.find(c => c.url === "/api/certs/scan-domains");
    expect(JSON.parse(String(scan!.init!.body))).toMatchObject({
      ip: "1.2.3.4", ssh_user: "root", ssh_port: 22,
    });

    // снимаем один чекбокс → добавляется только второй
    fireEvent.click(screen.getByText("node1.example.com"));
    fireEvent.click(screen.getByText((_, el) =>
      el?.tagName === "BUTTON" && /Добавить выбранные \(1\)/.test(el.textContent ?? "")));
    await waitFor(() => expect(onAdded).toHaveBeenCalled());
    const posts = calls.filter(c => c.url === "/api/domains")
      .map(c => JSON.parse(String(c.init!.body)).domain);
    expect(posts).toEqual(["mask.example.com"]);
    expect(onClose).toHaveBeenCalled();
  });

  it("ошибка сканирования показывается", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "Сканирование не удалось: timed out" }), { status: 502 })));
    render(<ScanDomainsModal defaults={DEFAULTS} onClose={() => {}} onAdded={() => {}} />);
    fireEvent.click(screen.getByText("Сканировать домены"));
    expect(await screen.findByText(/Сканирование не удалось/)).toBeInTheDocument();
  });
});
