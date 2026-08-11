import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DeployedEditor } from "./DeployedEditor";

// Wave-4 PR-10: редактор развёрнутой подписочной страницы.

const INFO = {
  mode: "dir",
  mount: "/opt/remnawave-subpage/frontend",
  files: [{ path: "index.html", size: 10 }, { path: "assets/app.css", size: 20 }],
};

function installFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url === "/api/subpages/deployed/inspect") return new Response(JSON.stringify(INFO), { status: 200 });
    if (url === "/api/subpages/deployed/read") return new Response(JSON.stringify({ path: "index.html", content: "<html>1</html>" }), { status: 200 });
    if (url === "/api/subpages/deployed/write") return new Response(JSON.stringify({ ok: true, restarted: true }), { status: 200 });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const CREDS = { ip: "1.2.3.4", ssh_port: "22", ssh_user: "root", ssh_password: "pw" };

function fillCreds() {
  fireEvent.change(screen.getByPlaceholderText("1.2.3.4"), { target: { value: CREDS.ip } });
  fireEvent.change(screen.getByPlaceholderText(""), { target: { value: CREDS.ssh_password } });
}

describe("DeployedEditor", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("inspect → файлы → чтение → правка → запись с перезапуском", async () => {
    const calls = installFetch();
    render(<DeployedEditor />);
    fireEvent.change(screen.getByPlaceholderText("1.2.3.4"), { target: { value: "1.2.3.4" } });
    const pwd = document.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(pwd, { target: { value: "pw" } });

    fireEvent.click(screen.getByText("Подключить и найти страницу"));
    // список файлов и автопрочтение первого
    expect(await screen.findByText("assets/app.css")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByDisplayValue("<html>1</html>")).toBeInTheDocument());
    // креды ушли в inspect
    const ins = calls.find(c => c.url === "/api/subpages/deployed/inspect");
    expect(JSON.parse(String(ins!.init!.body))).toMatchObject({ ip: "1.2.3.4", ssh_user: "root" });

    // правка → «изменён» → запись
    fireEvent.change(screen.getByDisplayValue("<html>1</html>"), { target: { value: "<html>2</html>" } });
    expect(await screen.findByText("изменён")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Записать"));
    await waitFor(() => {
      const wr = calls.find(c => c.url === "/api/subpages/deployed/write");
      expect(wr).toBeTruthy();
      expect(JSON.parse(String(wr!.init!.body))).toMatchObject({
        path: "index.html", content: "<html>2</html>", restart: true,
      });
    });
  });

  it("builtin: объяснение вместо редактора", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.url;
      if (url === "/api/subpages/deployed/inspect") {
        return new Response(JSON.stringify({ mode: "builtin", mount: "", files: [] }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    }));
    render(<DeployedEditor />);
    fireEvent.change(screen.getByPlaceholderText("1.2.3.4"), { target: { value: "1.2.3.4" } });
    const pwd = document.querySelector('input[type="password"]') as HTMLInputElement;
    fireEvent.change(pwd, { target: { value: "pw" } });
    fireEvent.click(screen.getByText("Подключить и найти страницу"));
    expect(await screen.findByText(/встроена в образ/)).toBeInTheDocument();
    expect(screen.queryByText("Записать")).toBeNull();
  });
});
