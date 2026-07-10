import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Profiles } from "./Profiles";
import { useConfigStore } from "./store/configStore";

// getActiveId() is null in the test env → the store persists under this key.
const KEY = "xray_profile_none";

beforeEach(() => {
  localStorage.clear();
  useConfigStore.setState({ config: null, dirty: false });
});

describe("Profiles (rw-profiles) runtime render", () => {
  it("shows the empty state and can create an empty config", async () => {
    render(<Profiles />);
    expect(await screen.findByText(/Загрузите или создайте конфиг/)).toBeInTheDocument();

    fireEvent.click(screen.getByText(/Пустой конфиг/));
    // Editor shell now renders the section cards.
    expect(await screen.findByText(/Inbounds/)).toBeInTheDocument();
    expect(screen.getByText(/Outbounds/)).toBeInTheDocument();
  });

  it("parses a seeded config into sections and surfaces ajv diagnostics for an invalid one", async () => {
    // An invalid config: a proxy outbound missing its server address + a routing
    // rule pointing at an outbound tag that does not exist.
    const invalid = {
      inbounds: [{ tag: "in", protocol: "vless", port: 443, settings: { clients: [{ id: "u" }] } }],
      outbounds: [{ tag: "proxy", protocol: "vless", settings: {} }],
      routing: { rules: [{ outboundTag: "ghost", domain: ["x.com"] }], balancers: [] },
    };
    localStorage.setItem(KEY, JSON.stringify(invalid));

    render(<Profiles />);

    // Sections parsed → counts reflected.
    expect(await screen.findByText(/Inbounds \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Outbounds \(1\)/)).toBeInTheDocument();

    // Diagnostics panel highlights the invalid config (ajv + reference checks).
    await waitFor(() => {
      expect(screen.getByText(/неизвестный outbound: "ghost"/)).toBeInTheDocument();
    });
  });
});
