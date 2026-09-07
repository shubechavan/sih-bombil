"use client";

import { useEffect, useRef } from "react";
import { useHealthStore } from "@/stores/health";

export function useServiceHealth(intervalMs = 30_000) {
  const { refresh } = useHealthStore();
  const timerRef = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    refresh();
    // refresh() self-guards against concurrent calls via isChecking in the store
    timerRef.current = setInterval(refresh, intervalMs);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [refresh, intervalMs]);

  return useHealthStore();
}
