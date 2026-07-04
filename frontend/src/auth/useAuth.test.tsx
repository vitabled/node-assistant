import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useAuth } from "./useAuth";
import { addAccount, forget, getSnapshot } from "./store";

describe("useAuth", () => {
  beforeEach(() => {
    localStorage.clear();
    // reset the singleton store to empty
    getSnapshot().accounts.slice().forEach(a => forget(a.id));
  });

  it("returns the current snapshot", () => {
    const { result } = renderHook(() => useAuth());
    expect(result.current.accounts).toEqual([]);
    expect(result.current.activeId).toBeNull();
  });

  it("re-renders when the store changes", () => {
    const { result } = renderHook(() => useAuth());
    act(() => addAccount({ id: "id-a", login: "alice", token: "t" }));
    expect(result.current.activeId).toBe("id-a");
    expect(result.current.accounts).toHaveLength(1);
  });
});
