import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Bridges } from "./Bridges";

// Wave-4 PR-6: список мостов и форма создания.

const OPTIONS = {
  nodes: [
    { uuid: "n1", name: "DE-1", address: "de.example.com", isDisabled: false,
      profileUuid: "p1",
      inbounds: [
        { uuid: "i1", tag: "vless-tcp", type: "vless", port: 443 },
        { uuid: "i2", tag: "vless-ws", type: "vless", port: 8443 },
      ] },
    { uuid: "n2", name: "NL-1", address: "nl.example.com", isDisabled: false,
      profileUuid: "p2", inbounds: [] },
  ],
  profiles: [{ uuid: "p1", name: "Main" }, { uuid: "p2", name: "Backup" }],
};

const CREATED = {
  id: "ab12", name: "Тест", outbound_matched: true,
  exit_node: { uuid: "n2", name: "NL-1", address: "nl.example.com" },
  inbound_tags: ["vless-tcp"], profile_uuids: ["p1"], applied_profiles: ["p1"],
  matchers: { domain: ["ads.example"], ip: [], protocol: [], port: "", network: "" },
  profile_errors: [],
};

function installFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url === "/api/bridges/options") return new Response(JSON.stringify(OPTIONS), { status: 200 });
    if (url === "/api/bridges" && init?.method === "POST") return new Response(JSON.stringify(CREATED), { status: 201 });
    if (url === "/api/bridges") return new Response(JSON.stringify({ bridges: [] }), { status: 200 });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

describe("Bridges", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("форма: нода-вход → её инбаунды, создание шлёт полный body", async () => {
    const calls = installFetch();
    render(<Bridges />);
    fireEvent.click(await screen.findByText("Новый мост"));

    // выбор ноды-входа показывает её инбаунды
    const selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "n1" } });
    const chip = await screen.findByText("vless-tcp");
    fireEvent.click(chip);

    // нода-выход + профиль
    fireEvent.change(selects[1], { target: { value: "n2" } });
    fireEvent.click(screen.getByText("Main"));

    fireEvent.change(screen.getByPlaceholderText(/doubleclick/), { target: { value: "ads.example" } });
    fireEvent.change(screen.getByPlaceholderText("EU → DE выход"), { target: { value: "DE→NL" } });

    fireEvent.click(screen.getByText("Создать мост"));
    await waitFor(() => {
      const post = calls.find(c => c.url === "/api/bridges" && c.init?.method === "POST");
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post!.init!.body));
      expect(body.exit_node_uuid).toBe("n2");
      expect(body.inbound_tags).toEqual(["vless-tcp"]);
      expect(body.profile_uuids).toEqual(["p1"]);
      expect(body.matchers.domain).toEqual(["ads.example"]);
      expect(body.name).toBe("DE→NL");
    });
  });

  it("кнопка создания заблокирована без выхода и профилей", async () => {
    installFetch();
    render(<Bridges />);
    fireEvent.click(await screen.findByText("Новый мост"));
    await screen.findAllByRole("combobox");
    expect(screen.getByText("Создать мост")).toBeDisabled();
  });
});
