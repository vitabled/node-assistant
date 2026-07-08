import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { PanelVariables } from "./PanelVariables";
import { panelJobsKey } from "../../auth/store";
import type { PanelJobSummary } from "./PanelDashboard";
import type { PanelDeployPayload } from "./PanelDeployForm";

// Ф8 — «Переменные» render + merge-write contract. Confirms the editor reads the
// panel .env, masks secrets, and — critically — does NOT resend untouched masked
// secrets on «Применить» (the server preserves them via merge).

const savedForm: PanelDeployPayload = {
  target: "panel", ip: "1.2.3.4", ssh_user: "root", ssh_password: "pw", ssh_port: 22,
  panel_domain: "panel.example.com", sub_domain: "", email: "", reverse_proxy: "caddy",
  cert_provider: "letsencrypt", cf_api_key: "", enable_webhooks: false, webhook_url: "",
  extra_env: {}, sub_server: null, subpage_html: "", install_test_tools: true,
};

const job: PanelJobSummary = {
  id: "p1", taskId: "t1", savedForm, createdAt: Date.now(), target: "panel", finalStatus: "success",
};

// FRONT_END_DOMAIN value is deliberately distinct from the panel-picker label
// ("panel.example.com") so findByDisplayValue is unambiguous.
const readResp = {
  present: true,
  pairs: [
    { key: "JWT_AUTH_SECRET", value: "••••••••", masked: true },
    { key: "FRONT_END_DOMAIN", value: "front.example.com", masked: false },
  ],
};

const jsonRes = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });

beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("PanelVariables", () => {
  it("shows the empty state when there is no successful panel install", () => {
    render(<PanelVariables />);
    expect(screen.getByText("Нет установленных панелей")).toBeTruthy();
  });

  it("reads the panel .env on mount and masks secret rows", async () => {
    localStorage.setItem(panelJobsKey(), JSON.stringify([job]));
    const fetchMock = vi.fn().mockResolvedValue(jsonRes(readResp));
    vi.stubGlobal("fetch", fetchMock);

    render(<PanelVariables />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/panel/env/read");
    expect(JSON.parse(opts.body).ip).toBe("1.2.3.4");

    // both keys render; the secret input shows the "keep" placeholder
    await screen.findByDisplayValue("JWT_AUTH_SECRET");
    await screen.findByDisplayValue("FRONT_END_DOMAIN");
    await screen.findByDisplayValue("front.example.com");
    expect(screen.getByPlaceholderText(/оставить как есть/)).toBeTruthy();
  });

  it("on «Применить» sends only the edited non-secret pair, never the untouched secret", async () => {
    localStorage.setItem(panelJobsKey(), JSON.stringify([job]));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonRes(readResp))                                  // read
      .mockResolvedValueOnce(jsonRes({ ok: true, applied: 1, removed: 0, restarted: true, detail: "" })) // write
      .mockResolvedValueOnce(jsonRes(readResp));                                 // re-read after apply
    vi.stubGlobal("fetch", fetchMock);

    render(<PanelVariables />);
    const domInput = await screen.findByDisplayValue("front.example.com");
    fireEvent.change(domInput, { target: { value: "new.example.com" } });
    fireEvent.click(screen.getByText("Применить"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const writeBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/panel/env/write");
    expect(writeBody.pairs).toEqual([{ key: "FRONT_END_DOMAIN", value: "new.example.com" }]);
    // the untouched masked secret must NOT be in the payload
    expect(writeBody.pairs.some((p: { key: string }) => p.key === "JWT_AUTH_SECRET")).toBe(false);
    expect(writeBody.deleted).toEqual([]);
  });

  it("deleting a row queues it in `deleted` on write", async () => {
    localStorage.setItem(panelJobsKey(), JSON.stringify([job]));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonRes(readResp))
      .mockResolvedValueOnce(jsonRes({ ok: true, applied: 0, removed: 1, restarted: true, detail: "" }))
      .mockResolvedValueOnce(jsonRes(readResp));
    vi.stubGlobal("fetch", fetchMock);

    render(<PanelVariables />);
    await screen.findByDisplayValue("FRONT_END_DOMAIN");
    // two delete buttons (one per row) — remove the non-secret second row
    const delButtons = screen.getAllByTitle("Удалить");
    fireEvent.click(delButtons[1]);
    fireEvent.click(screen.getByText("Применить"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const writeBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(writeBody.deleted).toEqual(["FRONT_END_DOMAIN"]);
    expect(writeBody.pairs).toEqual([]);
  });

  it("shows the «файл не найден» notice on a 404 read", async () => {
    localStorage.setItem(panelJobsKey(), JSON.stringify([job]));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonRes({ detail: "not found" }, 404)));
    render(<PanelVariables />);
    await screen.findByText(/не найден/);
  });
});
