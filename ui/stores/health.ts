import { create } from "zustand";
import { bff } from "@/lib/api";
import type { BackendHealthPayload, HealthCheck, ServiceName, ServiceState, ServiceStatus } from "@/types/health";

interface HealthStore {
  services: Record<ServiceName, ServiceStatus>;
  cicLive: BackendHealthPayload["cic_live_feed"] | null;
  lastChecked: number | null;
  isChecking: boolean;

  refresh: () => Promise<void>;
}

const initialServices: Record<ServiceName, ServiceStatus> = {
  tor: { status: "checking" },
  postgres: { status: "checking" },
  n8n: { status: "checking" },
  fastapi: { status: "checking" },
  roberta: { status: "checking" },
  scheduler: { status: "checking" },
};

function normalizeState(value: unknown): ServiceState {
  const v = String(value ?? "checking").toLowerCase();
  if (v === "online" || v === "offline" || v === "degraded" || v === "checking") {
    return v;
  }
  return "checking";
}

function statusFromServiceEntry(entry: unknown): ServiceState {
  const rec = entry as { status?: unknown; state?: unknown } | undefined;
  return normalizeState(rec?.status ?? rec?.state);
}

export const useHealthStore = create<HealthStore>()((set, get) => ({
  services: initialServices,
  cicLive: null,
  lastChecked: null,
  isChecking: false,

  refresh: async () => {
    if (get().isChecking) return;
    set({ isChecking: true });

    try {
      const data = await bff.get("health").json<BackendHealthPayload>();
      const rawServices = data.services ?? (data as unknown as Partial<HealthCheck>);

      const nextServices: Record<ServiceName, ServiceStatus> = {
        tor: { status: statusFromServiceEntry(rawServices?.tor) },
        postgres: { status: statusFromServiceEntry(rawServices?.postgres) },
        n8n: { status: statusFromServiceEntry(rawServices?.n8n) },
        fastapi: { status: statusFromServiceEntry(rawServices?.fastapi) },
        roberta: { status: statusFromServiceEntry(rawServices?.roberta) },
        scheduler: { status: statusFromServiceEntry(rawServices?.scheduler) },
      };

      if (!rawServices?.fastapi) {
        nextServices.fastapi = { status: "online" };
      }

      set({
        services: nextServices,
        cicLive: data.cic_live_feed ?? null,
        lastChecked: Date.now(),
        isChecking: false,
      });
    } catch {
      set({ isChecking: false });
    }
  },
}));
