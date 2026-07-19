import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Stub the xterm-backed terminal (needs matchMedia/canvas — not in jsdom).
vi.mock("../TerminalOutput", () => ({ TerminalOutput: ({ lines }: any) => <pre>{(lines || []).join("\n")}</pre> }));

import { SyncGroupPanel } from "./SyncGroupPanel";

// Minimal WebSocket stub (useTaskStream opens one for the sync stream modal).
class FakeWS { close() {} send() {} set onmessage(_: any) {} set onclose(_: any) {} set onopen(_: any) {} set onerror(_: any) {} }
(globalThis as any).WebSocket = FakeWS as any;

const JOBS: any[] = [
  { id: "pk-A", taskId: "t1", finalStatus: "success", savedForm: { ip: "1.1.1.1", ssh_port: "22", ssh_user: "root", ssh_password: "x", panel_domain: "a.example" } },
  { id: "pk-B", taskId: "t2", finalStatus: "success", savedForm: { ip: "2.2.2.2", ssh_port: "22", ssh_user: "root", ssh_password: "y", panel_domain: "b.example" } },
];

const GROUP = {
  id: "g1", name: "Grp", auto_sync: false, interval_hours: 24, last_sync_at: null, last_sync_status: null,
  members: [
    { panel_key: "pk-A", priority: 20, role: "primary" },
    { panel_key: "pk-B", priority: 10, role: "standby" },
  ],
};

type H = (body: any) => { status?: number; body?: any };
let ROUTES: Record<string, H> = {};
function route(m: string, re: string, h: H) { ROUTES[`${m} ${re}`] = h; }
function installFetch() {
  const fn = vi.fn(async (url: string, opts?: any) => {
    const method = (opts?.method || "GET").toUpperCase();
    const body = opts?.body ? JSON.parse(opts.body) : undefined;
    for (const [k, h] of Object.entries(ROUTES)) {
      const [mm, ...rest] = k.split(" ");
      if (mm === method && new RegExp(rest.join(" ")).test(url)) {
        const { status = 200, body: rb } = h(body);
        return { ok: status < 400, status, json: async () => rb } as any;
      }
    }
    throw new Error(`unmocked ${method} ${url}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

beforeEach(() => { ROUTES = {}; });
afterEach(() => vi.restoreAllMocks());

describe("SyncGroupPanel", () => {
  it("creates a group from selected panels", async () => {
    let posted: any = null;
    route("GET", "/api/sync/groups$", () => ({ body: [] }));
    route("POST", "/api/sync/groups$", (b) => { posted = b; return { status: 201, body: { ...GROUP } }; });
    installFetch();

    render(<SyncGroupPanel jobs={JOBS} />);
    fireEvent.click(await screen.findByText("Группа")); // open editor
    // both panels pre-selected for a new group → save directly
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted.members).toHaveLength(2);
    expect(posted.members.map((m: any) => m.role).sort()).toEqual(["primary", "standby"]);
  });

  it("runs a sync for a standby with a confirm gate", async () => {
    let runBody: any = null;
    route("GET", "/api/sync/groups$", () => ({ body: [GROUP] }));
    route("POST", "/api/sync/groups/g1/run$", (b) => { runBody = b; return { body: { task_id: "task-9", task_type: "panel-sync" } }; });
    installFetch();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SyncGroupPanel jobs={JOBS} />);
    // the standby row exposes a "Синхронизировать" button
    fireEvent.click(await screen.findByText("Синхронизировать"));

    await waitFor(() => expect(runBody).not.toBeNull());
    expect(runBody.confirm).toBe(true);
    expect(runBody.standby_key).toBe("pk-B");
    expect(runBody.primary_creds.ip).toBe("1.1.1.1"); // nearest-higher primary's creds
  });

  it("aborts the sync when the confirm is declined", async () => {
    let runCalled = false;
    route("GET", "/api/sync/groups$", () => ({ body: [GROUP] }));
    route("POST", "/api/sync/groups/g1/run$", () => { runCalled = true; return { body: {} }; });
    installFetch();
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<SyncGroupPanel jobs={JOBS} />);
    fireEvent.click(await screen.findByText("Синхронизировать"));
    await new Promise(r => setTimeout(r, 20));
    expect(runCalled).toBe(false);
  });

  it("needs ≥2 deployed panels", async () => {
    route("GET", "/api/sync/groups$", () => ({ body: [] }));
    installFetch();
    render(<SyncGroupPanel jobs={[JOBS[0]]} />);
    expect(await screen.findByText(/Нужно ≥2/)).toBeInTheDocument();
  });

  it("surfaces a run error and does not open the stream modal", async () => {
    route("GET", "/api/sync/groups$", () => ({ body: [GROUP] }));
    route("POST", "/api/sync/groups/g1/run$", () => ({ status: 500, body: { detail: "boom" } }));
    installFetch();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<SyncGroupPanel jobs={JOBS} />);
    fireEvent.click(await screen.findByText("Синхронизировать"));
    // no crash, no terminal modal (task never opened)
    await waitFor(() => expect(screen.queryByText(/Синхронизация \.\.\./)).not.toBeInTheDocument());
  });

  it("picks the NEAREST higher primary among several (backend parity)", async () => {
    const jobs3 = [
      { id: "A", taskId: "tA", finalStatus: "success", savedForm: { ip: "10.0.0.30", ssh_port: "22", ssh_user: "root", ssh_password: "a", panel_domain: "top" } },
      { id: "B", taskId: "tB", finalStatus: "success", savedForm: { ip: "10.0.0.20", ssh_port: "22", ssh_user: "root", ssh_password: "b", panel_domain: "mid" } },
      { id: "C", taskId: "tC", finalStatus: "success", savedForm: { ip: "10.0.0.10", ssh_port: "22", ssh_user: "root", ssh_password: "c", panel_domain: "low" } },
    ];
    const g3 = {
      id: "g3", name: "G3", auto_sync: false, interval_hours: 24, last_sync_at: null, last_sync_status: null,
      members: [
        { panel_key: "A", priority: 30, role: "primary" },
        { panel_key: "B", priority: 20, role: "primary" },
        { panel_key: "C", priority: 10, role: "standby" },
      ],
    };
    let runBody: any = null;
    route("GET", "/api/sync/groups$", () => ({ body: [g3] }));
    route("POST", "/api/sync/groups/g3/run$", (b) => { runBody = b; return { body: { task_id: "tk" } }; });
    installFetch();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<SyncGroupPanel jobs={jobs3 as any} />);
    fireEvent.click(await screen.findByText("Синхронизировать"));
    // C (10) → nearest higher primary is B (20), NOT A (30).
    await waitFor(() => expect(runBody).not.toBeNull());
    expect(runBody.primary_creds.ip).toBe("10.0.0.20");
  });
});
