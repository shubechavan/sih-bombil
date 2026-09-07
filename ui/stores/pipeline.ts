import { create } from "zustand";
import { persist } from "zustand/middleware";
import { bff } from "@/lib/api";
import type { PipelineStageState, ScanConfig, ScanResult } from "@/types/pipeline";
import { PIPELINE_STAGES } from "@/lib/constants";

interface PipelineStore {
  scanId: string | null;
  config: ScanConfig;
  stages: PipelineStageState[];
  results: ScanResult[];
  isRunning: boolean;
  error: string | null;

  startScan: (config: ScanConfig) => Promise<void>;
  updateStage: (index: number, update: Partial<PipelineStageState>) => void;
  setScanComplete: (results: ScanResult[]) => void;
  cancelScan: () => void;
  reset: () => void;
}

const defaultStages: PipelineStageState[] = PIPELINE_STAGES.map((name, index) => ({
  index,
  name,
  status: "idle" as const,
  progress: 0,
  metrics: {},
}));

const defaultConfig: ScanConfig = {
  queries: [],
  fetchProfile: "stealth",
  maxResults: 100,
  enableObfuscation: true,
  enablePiiScrub: true,
};

export const usePipelineStore = create<PipelineStore>()(
  persist(
    (set, get) => ({
      scanId: null,
      config: defaultConfig,
      stages: defaultStages,
      results: [],
      isRunning: false,
      error: null,

      startScan: async (config) => {
        set({
          config,
          stages: defaultStages,
          results: [],
          isRunning: true,
          error: null,
        });

        try {
          const res = await bff.post("scan", { json: config }).json<{ scanId: string }>();
          set({ scanId: res.scanId });
        } catch (err) {
          set({
            isRunning: false,
            error: err instanceof Error ? err.message : "Scan failed",
          });
        }
      },

      updateStage: (index, update) => {
        const stages = [...get().stages];
        if (stages[index]) {
          stages[index] = { ...stages[index], ...update };
          set({ stages });
        }
      },

      setScanComplete: (results) => {
        set({ results, isRunning: false });
      },

      cancelScan: () => {
        set({ isRunning: false });
      },

      reset: () => {
        set({
          scanId: null,
          config: defaultConfig,
          stages: defaultStages,
          results: [],
          isRunning: false,
          error: null,
        });
      },
    }),
    {
      name: "ds-pipeline",
      partialize: (state) => ({
        scanId: state.scanId,
        config: state.config,
        // results intentionally excluded: can exceed localStorage quota
      }),
    }
  )
);
