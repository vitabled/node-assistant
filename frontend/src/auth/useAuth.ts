import { useSyncExternalStore } from "react";
import { subscribe, getSnapshot } from "./store";

// Reactive view of the device account store.
export function useAuth() {
  return useSyncExternalStore(subscribe, getSnapshot);
}
