import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ImportFromSubscription } from "./ImportFromSubscription";

const CANDIDATES = [
  { host: "node1.example.com", port: 443, name: "AMS", country: "NL", ip: "203.0.113.10", status: "new" },
  { host: "node2.example.com", port: 8443, name: "FRA", country: "DE", ip: "203.0.113.20", status: "duplicate" },
  { host: "node3.example.com", port: 443, name: "???", country: "", ip: "", status: "unresolved" },
];

function installFetch() {
  const fn = vi.fn(async (url: string, opts?: any) => {
    if (url === "/api/subscriptions")
      return { ok: true, json: async () => [{ id: "s1", url: "https://sub.example/1" }] } as any;
    if (url === "/api/server-monitor/import/subscription") {
      const body = JSON.parse(opts.body);
      return {
        ok: true,
        json: async () => ({
          total: 3, candidates: CANDIDATES, dry_run: body.dry_run,
          imported: body.dry_run ? 0 : 1,
        }),
      } as any;
    }
    throw new Error(`unmocked ${url}`);
  });
  (globalThis as any).fetch = fn;
  return fn;
}

const importCalls = (fn: any) =>
  fn.mock.calls
    .filter(([u]: any[]) => u === "/api/server-monitor/import/subscription")
    .map(([, o]: any[]) => JSON.parse(o.body));

afterEach(() => vi.restoreAllMocks());

describe("ImportFromSubscription", () => {
  it("previews with dry_run and writes nothing until «Импортировать»", async () => {
    const fn = installFetch();
    render(<ImportFromSubscription onClose={() => {}} onImported={() => {}} />);
    fireEvent.click(await screen.findByText("Показать"));
    await screen.findByText("node1.example.com → 203.0.113.10");
    expect(importCalls(fn).map(b => b.dry_run)).toEqual([true]);
  });

  it("preselects only the new rows and disables the rest", async () => {
    installFetch();
    render(<ImportFromSubscription onClose={() => {}} onImported={() => {}} />);
    fireEvent.click(await screen.findByText("Показать"));
    const newBox = await screen.findByLabelText("AMS");
    expect((newBox as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("FRA") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("???") as HTMLInputElement).disabled).toBe(true);
  });

  it("counts the selected rows in the button label", async () => {
    installFetch();
    render(<ImportFromSubscription onClose={() => {}} onImported={() => {}} />);
    fireEvent.click(await screen.findByText("Показать"));
    await screen.findByText("Импортировать (1)");
    fireEvent.click(screen.getByLabelText("AMS"));       // deselect the only new row
    await screen.findByText("Импортировать");
  });

  it("sends dry_run=false on import and notifies the caller", async () => {
    const fn = installFetch();
    const onImported = vi.fn();
    render(<ImportFromSubscription onClose={() => {}} onImported={onImported} />);
    fireEvent.click(await screen.findByText("Показать"));
    fireEvent.click(await screen.findByText("Импортировать (1)"));
    await waitFor(() => expect(onImported).toHaveBeenCalled());
    expect(importCalls(fn).map(b => b.dry_run)).toEqual([true, false]);
  });

  it("shows the backend error instead of failing silently", async () => {
    (globalThis as any).fetch = vi.fn(async (url: string) => {
      if (url === "/api/subscriptions") return { ok: true, json: async () => [] } as any;
      return { ok: false, json: async () => ({ detail: "Не удалось загрузить подписку" }) } as any;
    });
    render(<ImportFromSubscription onClose={() => {}} onImported={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText("https://…"), { target: { value: "https://x/y" } });
    fireEvent.click(screen.getByText("Показать"));
    expect(await screen.findByText("Не удалось загрузить подписку")).toBeInTheDocument();
  });
});
