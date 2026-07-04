import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Heavy children are stubbed — we only exercise DeployDashboard's job loading
// and the per-account localStorage key.
vi.mock("./DeployCard", () => ({
  DeployCard: ({ job }: { job: { domain: string } }) => <div>CARD:{job.domain}</div>,
}));
vi.mock("./DeployForm", () => ({ DeployForm: () => <div>FORM</div> }));

import { DeployDashboard } from "./DeployDashboard";
import { addAccount, forget, getSnapshot } from "../auth/store";

function reset() {
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

const job = (domain: string) => ({
  taskId: "t1", domain, ip: "1.2.3.4", newSshPort: 2222, startedAt: 1, savedForm: {},
});

describe("DeployDashboard", () => {
  beforeEach(() => { reset(); addAccount({ id: "id-a", login: "alice", token: "t" }); });
  afterEach(cleanup);

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
});
