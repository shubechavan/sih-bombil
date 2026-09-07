"use client";

import { useCallback, useEffect, useRef } from "react";
import { usePipelineStore } from "@/stores/pipeline";
import { useSSE } from "./useSSE";
import type { PipelineEvent } from "@/types/pipeline";

export function usePipelineState() {
  const store = usePipelineStore();
  const isRunningRef = useRef(store.isRunning);
  isRunningRef.current = store.isRunning;

  const handleEvent = useCallback(
    (event: PipelineEvent) => {
      if (event.type === "stage-update") {
        store.updateStage(event.stage, {
          status: event.status,
          progress: event.progress,
          metrics: event.metrics,
        });
      }
      if (event.type === "scan-complete") {
        store.setScanComplete(event.results);
      }
    },
    [store.updateStage, store.setScanComplete]
  );

  const sseUrl = store.scanId && store.isRunning
    ? `/api/scan/status?scanId=${store.scanId}`
    : null;

  const sseStatus = useSSE<PipelineEvent>(sseUrl, handleEvent);

  // If the SSE stream closes unexpectedly while a scan is running, unlock the UI.
  useEffect(() => {
    if (sseStatus === "closed" && isRunningRef.current) {
      store.cancelScan();
    }
  }, [sseStatus, store.cancelScan]);

  return { ...store, sseStatus };
}
