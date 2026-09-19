import { create } from "zustand";
import { bff } from "@/lib/api";
import type {
  BackendHealthPayload,
  ServiceName,
  ServiceStatus,
} from "@/types/health";

interface HealthStore {
  services: Record<ServiceName, ServiceStatus>;
  counts: Record<string, number> | null;
  hint: string | null;
  lastChecked: number | null;
  isChecking: boolean;
  refresh: () => Promise<void>;
}

const initialServices: Record<ServiceName, ServiceStatus> = {
  api: { status: "checking" },
  database: { status: "checking" },
  pipeline: { status: "checking" },
};

export const useHealthStore = create<HealthStore>()((set, get) => ({
  services: initialServices,
  counts: null,
  hint: null,
  lastChecked: null,
  isChecking: false,

  refresh: async () => {
    if (get().isChecking) return;
    set({ isChecking: true });

    try {
      const data = await bff.get("health").json<BackendHealthPayload>();
      const databaseOk = data.database === "ok";
      set({
        services: {
          // It answered, so it is up. Nothing else to infer.
          api: { status: "online" },
          database: {
            status: databaseOk ? "online" : "offline",
            detail: databaseOk ? undefined : data.database,
          },
          // `ready` is false until link.cluster has run and there are actors
          // to show. Degraded rather than offline: the API is fine, the
          // pipeline simply has not been taken to the end yet.
          pipeline: {
            status: data.ready ? "online" : "degraded",
            detail: data.ready ? undefined : (data.hint ?? undefined),
          },
        },
        counts: data.counts ?? null,
        hint: data.hint ?? null,
        lastChecked: Date.now(),
        isChecking: false,
      });
    } catch {
      set({
        services: {
          api: { status: "offline" },
          database: { status: "offline" },
          pipeline: { status: "offline" },
        },
        lastChecked: Date.now(),
        isChecking: false,
      });
    }
  },
}));
