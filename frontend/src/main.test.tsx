import { describe, expect, it, vi } from "vitest";

const renderSpy = vi.fn();
const createRootSpy = vi.fn(() => ({ render: renderSpy, unmount: vi.fn() }));
vi.mock("react-dom/client", () => ({ default: { createRoot: createRootSpy } }));

const installSpy = vi.fn();
vi.mock("./auth/apiClient", () => ({ installApiClient: installSpy }));
vi.mock("./auth/AuthGate", () => ({ AuthGate: () => null }));

describe("main entry", () => {
  it("installs the fetch interceptor and mounts AuthGate into #root", async () => {
    document.body.innerHTML = '<div id="root"></div>';
    await import("./main");
    expect(installSpy).toHaveBeenCalledTimes(1);
    expect(createRootSpy).toHaveBeenCalledTimes(1);
    expect(renderSpy).toHaveBeenCalledTimes(1);
  });
});
