import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AccountMenu } from "./AccountMenu";
import { addAccount, forget, getSnapshot, switchTo } from "./store";

function reset() {
  localStorage.clear();
  getSnapshot().accounts.slice().forEach(a => forget(a.id));
}
const A = { id: "id-a", login: "alice", token: "ta" };
const B = { id: "id-b", login: "bob", token: "tb" };

function openMenu() {
  // trigger is the only button when closed
  fireEvent.click(screen.getAllByRole("button")[0]);
}

describe("AccountMenu", () => {
  beforeEach(reset);
  afterEach(cleanup);

  it("renders nothing when signed out", () => {
    const { container } = render(<AccountMenu />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the active account and menu actions", () => {
    addAccount(A);
    render(<AccountMenu />);
    openMenu();
    expect(screen.getByText("активный аккаунт")).toBeInTheDocument();
    expect(screen.getByText("Добавить аккаунт")).toBeInTheDocument();
    expect(screen.getByText("Выйти из аккаунта")).toBeInTheDocument();
  });

  it("switches to another added account", () => {
    addAccount(A);
    addAccount(B); // B active
    switchTo("id-a"); // make A active so B shows as "other"
    render(<AccountMenu />);
    openMenu();
    // Bob appears as a switch target; clicking it activates B.
    fireEvent.click(screen.getByText("bob"));
    expect(getSnapshot().activeId).toBe("id-b");
  });

  it("logs out of the active account", async () => {
    addAccount(A);
    render(<AccountMenu />);
    openMenu();
    fireEvent.click(screen.getByText("Выйти из аккаунта"));
    await waitFor(() => expect(getSnapshot().activeId).toBeNull());
  });
});
