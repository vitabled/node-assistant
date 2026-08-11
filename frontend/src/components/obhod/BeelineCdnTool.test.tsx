import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BeelineCdnTool } from "./BeelineCdnTool";

// Wave-4 PR-9: Beeline CDN — выбор хостов и применение CDN-домена.

const HOSTS = {
  hosts: [
    { uuid: "h1", remark: "DE-1", address: "de.example.com", port: 443, sni: "old.example.com" },
    { uuid: "h2", remark: "NL-1", address: "nl.example.com", port: 443 },
  ],
};

function installFetch(applyRes: Response | null = null) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url === "/api/obhod/hosts") return new Response(JSON.stringify(HOSTS), { status: 200 });
    if (url === "/api/obhod/beeline/apply") {
      return applyRes ?? new Response(JSON.stringify({ ok: true, applied: ["h1"], errors: [], domain: "cdn123.b-cdn.net" }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

describe("BeelineCdnTool", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("выбор хоста + домен → apply с правильным body", async () => {
    const calls = installFetch();
    render(<BeelineCdnTool />);
    fireEvent.click(await screen.findByText("DE-1"));
    fireEvent.change(screen.getByPlaceholderText("cdn123.b-cdn.net"),
      { target: { value: "CDN123.B-CDN.net" } });   // приводится к lower
    fireEvent.click(screen.getByText("Применить CDN-домен"));

    await waitFor(() => expect(
      screen.getByText((_, el) => el?.tagName === "SPAN" && /Применено к 1 хостам/.test(el.textContent ?? "")),
    ).toBeInTheDocument());
    const post = calls.find(c => c.url === "/api/obhod/beeline/apply");
    expect(JSON.parse(String(post!.init!.body))).toEqual({
      host_uuids: ["h1"], domain: "cdn123.b-cdn.net",
    });
  });

  it("битый домен не уходит на сервер", async () => {
    const calls = installFetch();
    render(<BeelineCdnTool />);
    fireEvent.click(await screen.findByText("DE-1"));
    fireEvent.change(screen.getByPlaceholderText("cdn123.b-cdn.net"),
      { target: { value: "не домен" } });
    fireEvent.click(screen.getByText("Применить CDN-домен"));
    expect(await screen.findByText(/Некорректный CDN-домен/)).toBeInTheDocument();
    expect(calls.find(c => c.url === "/api/obhod/beeline/apply")).toBeUndefined();
  });
});
