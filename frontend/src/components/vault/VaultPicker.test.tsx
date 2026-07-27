import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { VaultPicker } from "./VaultPicker";

// The security property this file exists for: a private key must NEVER be
// revealed into the browser by the picker. It rides as an opaque `ssh_key_ref`
// because a deploy card persists its whole form into localStorage.

const ENTRIES = [
  { id: "e-pass", name: "prod-root", kind: "ssh_password", resource: "10.0.0.1",
    username: "root", hint: "pw***", has_secret: true, broken: false },
  { id: "e-key", name: "deploy-key", kind: "ssh_key", resource: "",
    username: "", hint: "---***", has_secret: true, broken: false },
  { id: "e-note", name: "заметка", kind: "note", resource: "",
    username: "", hint: "", has_secret: false, broken: false },
];

let revealCalls: string[] = [];

function installFetch() {
  const fn = vi.fn(async (url: string, opts?: RequestInit) => {
    const method = (opts?.method || "GET").toUpperCase();
    if (method === "GET" && url.endsWith("/api/vault")) {
      return { ok: true, status: 200, json: async () => ENTRIES } as unknown as Response;
    }
    const m = /\/api\/vault\/([^/]+)\/reveal$/.exec(url);
    if (method === "POST" && m) {
      revealCalls.push(m[1]);
      return {
        ok: true, status: 200,
        json: async () => ({ fields: { password: "s3cret", private_key: "KEY-MATERIAL" } }),
      } as unknown as Response;
    }
    throw new Error(`unexpected ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fn);
}

beforeEach(() => { revealCalls = []; installFetch(); });
afterEach(() => { vi.unstubAllGlobals(); });

async function openPicker(
  props: Partial<Parameters<typeof VaultPicker>[0]> = {},
  awaitName = "prod-root",
) {
  const onPickValue = vi.fn();
  const onPickKeyRef = vi.fn();
  render(<VaultPicker kinds={["ssh_password", "ssh_key"]}
    onPickValue={onPickValue} onPickKeyRef={onPickKeyRef} {...props} />);
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(screen.getByText(awaitName)).toBeTruthy());
  return { onPickValue, onPickKeyRef };
}

describe("VaultPicker", () => {
  it("picking an SSH KEY passes a ref and never calls reveal", async () => {
    const { onPickValue, onPickKeyRef } = await openPicker();

    fireEvent.click(screen.getByText("deploy-key"));

    await waitFor(() => expect(onPickKeyRef).toHaveBeenCalledWith("e-key", "deploy-key"));
    expect(onPickValue).not.toHaveBeenCalled();
    expect(revealCalls, "приватный ключ не должен раскрываться в браузер").toEqual([]);
  });

  it("picking an SSH PASSWORD reveals it and hands over the value", async () => {
    const { onPickValue, onPickKeyRef } = await openPicker();

    fireEvent.click(screen.getByText("prod-root"));

    await waitFor(() => expect(onPickValue).toHaveBeenCalledWith("s3cret"));
    expect(onPickKeyRef).not.toHaveBeenCalled();
    expect(revealCalls).toEqual(["e-pass"]);
  });

  it("pickRefOnly never reveals anything, whatever the kind", async () => {
    const { onPickValue, onPickKeyRef } = await openPicker({
      kinds: ["ssh_password", "ssh_key", "note"], pickRefOnly: true,
    });

    fireEvent.click(screen.getByText("prod-root"));

    await waitFor(() => expect(onPickKeyRef).toHaveBeenCalledWith("e-pass", "prod-root"));
    expect(onPickValue).not.toHaveBeenCalled();
    expect(revealCalls).toEqual([]);
  });

  it("only entries of the requested kinds are offered", async () => {
    await openPicker({ kinds: ["ssh_key"] }, "deploy-key");
    expect(screen.getByText("deploy-key")).toBeTruthy();
    expect(screen.queryByText("prod-root")).toBeNull();
  });
});
