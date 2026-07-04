import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Stub the whole App so the gate can be tested without mounting the real tree.
vi.mock("../App", () => ({ default: () => <div>APP_ROOT</div> }));

import { AuthGate } from "./AuthGate";
import { addAccount, forget, getSnapshot } from "./store";

function reset() {
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}

describe("AuthGate", () => {
  beforeEach(reset);
  afterEach(cleanup);

  it("shows the login screen when no account is active", () => {
    render(<AuthGate />);
    expect(screen.getByText("Вход в аккаунт")).toBeInTheDocument();
    expect(screen.queryByText("APP_ROOT")).not.toBeInTheDocument();
  });

  it("renders the app when an account is active", () => {
    addAccount({ id: "id-a", login: "alice", token: "t" });
    render(<AuthGate />);
    expect(screen.getByText("APP_ROOT")).toBeInTheDocument();
    expect(screen.queryByText("Вход в аккаунт")).not.toBeInTheDocument();
  });
});
