import { fireEvent, render, screen, cleanup, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Heavy children are stubbed — we only exercise DeployDashboard's job loading
// and the per-account localStorage key.
vi.mock("./DeployCard", () => ({
  DeployCard: ({ job }: { job: { domain: string } }) => <div>CARD:{job.domain}</div>,
}));
let capturedPreset: Record<string, unknown> | undefined;
vi.mock("./DeployForm", () => ({
  DeployForm: ({ preset }: { preset?: Record<string, unknown> }) => {
    capturedPreset = preset;
    return <div>FORM</div>;
  },
}));

import { DeployDashboard } from "./DeployDashboard";
import { addAccount, forget, getSnapshot } from "../auth/store";

function reset() {
  capturedPreset = undefined;
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

const job = (domain: string) => ({
  taskId: "t1", domain, ip: "1.2.3.4", newSshPort: 2222, startedAt: 1, savedForm: {},
});

describe("DeployDashboard", () => {
  beforeEach(() => { reset(); addAccount({ id: "id-a", login: "alice", token: "t" }); });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads jobs from the active account's per-account key", () => {
    localStorage.setItem("deploy_jobs_id-a", JSON.stringify([job("node1.example")]));
    render(<DeployDashboard />);
    expect(screen.getByText("CARD:node1.example")).toBeInTheDocument();
  });

  it("shows the empty state when the account has no jobs", () => {
    render(<DeployDashboard />);
    expect(screen.getAllByText("Нет задач деплоя").length).toBeGreaterThan(0);
  });

  it("ignores the legacy un-suffixed deploy_jobs key (isolation)", () => {
    // Old global key must NOT leak into an account's view.
    localStorage.setItem("deploy_jobs", JSON.stringify([job("leaked.example")]));
    render(<DeployDashboard />);
    expect(screen.queryByText("CARD:leaked.example")).not.toBeInTheDocument();
    expect(screen.getAllByText("Нет задач деплоя").length).toBeGreaterThan(0);
  });

  it.each([
    ["ничего", [], []],
    ["только Remnanode", ["Remnanode"], ["remnanode"]],
    ["только SSL", ["SSL-сертификат"], ["ssl"]],
    ["Remnanode и SSL", ["Remnanode", "SSL-сертификат"], ["remnanode", "ssl"]],
  ])("existing server sends positive selection: %s", async (_name, labels, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: { remnanode: "present", ssl: "present" },
        settings: {},
      }),
    }));
    render(<DeployDashboard />);

    fireEvent.click(screen.getByRole("button", { name: /Существующий сервер/ }));
    fireEvent.change(screen.getByPlaceholderText("1.2.3.4"), { target: { value: "1.2.3.4" } });
    const password = document.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(password, { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Определить компоненты" }));
    await screen.findByText("Что доустановить");

    for (const label of labels) {
      fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(label) }));
    }
    fireEvent.click(screen.getByRole("button", { name: "Продолжить к деплою" }));

    await waitFor(() => expect(capturedPreset?.install_components).toEqual(expected));
    expect(capturedPreset?.skip_components).toEqual([]);
  });
});
