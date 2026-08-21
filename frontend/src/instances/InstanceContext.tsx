import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getActiveInstanceId, setActiveInstanceId } from "../auth/store";

export interface Instance {
  id: string;
  name: string;
  account_id: string;
}

interface InstanceState {
  instances: Instance[];
  activeInstanceId: string;
  selectInstance: (id: string) => void;
  createInstance: (name: string) => Promise<void>;
  loading: boolean;
}

const standaloneDefault: InstanceState = {
  instances: [{ id: "default", name: "Default", account_id: "" }],
  activeInstanceId: "default",
  selectInstance: () => {},
  createInstance: async () => {},
  loading: false,
};
const Context = createContext<InstanceState>(standaloneDefault);

export function InstanceProvider({ children }: { children: ReactNode }) {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [activeInstanceId, setActive] = useState(() => getActiveInstanceId());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    // Always list through Default so a stale/deleted selection cannot lock the
    // user out of the very endpoint needed to recover their workspace choice.
    fetch("/api/instances", { headers: { "X-Instance-Id": "default" } })
      .then(async response => {
        if (!response.ok) throw new Error("instances");
        return response.json() as Promise<Instance[]>;
      })
      .then(payload => {
        if (!alive) return;
        const rows = Array.isArray(payload) ? payload : standaloneDefault.instances;
        setInstances(rows);
        const selected = rows.some(row => row.id === activeInstanceId)
          ? activeInstanceId : (rows[0]?.id || "default");
        setActiveInstanceId(selected);
        setActive(selected);
      })
      .catch(() => {
        if (alive) setInstances(standaloneDefault.instances);
      })
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);

  const selectInstance = (id: string) => {
    if (!instances.some(row => row.id === id)) return;
    setActiveInstanceId(id);
    setActive(id);
  };

  const createInstance = async (name: string) => {
    const response = await fetch("/api/instances", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Instance-Id": "default" },
      body: JSON.stringify({ name }),
    });
    if (!response.ok) throw new Error("Не удалось создать инстанс");
    const created: Instance = await response.json();
    setInstances(rows => [...rows, created]);
    setActiveInstanceId(created.id);
    setActive(created.id);
  };

  return (
    <Context.Provider value={{ instances, activeInstanceId, selectInstance, createInstance, loading }}>
      {children}
    </Context.Provider>
  );
}

export function useInstance(): InstanceState {
  return useContext(Context);
}