"use client";

import { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useThreatStore } from "@/stores/threats";
import type { ThreatFilters } from "@/types/threat";

function parseFilterParams(params: URLSearchParams): Partial<ThreatFilters> {
  const filters: Partial<ThreatFilters> = {};
  const severity = params.get("severity");
  if (severity) filters.severity = severity.split(",") as ThreatFilters["severity"];
  const search = params.get("search");
  if (search) filters.search = search;
  const category = params.get("category");
  if (category) filters.category = category;
  const engine = params.get("engine");
  if (engine) filters.engine = engine;
  const threatsOnly = params.get("threats_only");
  if (threatsOnly) filters.threatsOnly = threatsOnly === "true";
  const obfuscatedOnly = params.get("obfuscated_only");
  if (obfuscatedOnly) filters.obfuscatedOnly = obfuscatedOnly === "true";
  return filters;
}

function buildFilterParams(filters: ThreatFilters): string {
  const params = new URLSearchParams();
  if (filters.severity?.length) params.set("severity", filters.severity.join(","));
  if (filters.search) params.set("search", filters.search);
  if (filters.category) params.set("category", filters.category);
  if (filters.engine) params.set("engine", filters.engine);
  if (filters.threatsOnly) params.set("threats_only", "true");
  if (filters.obfuscatedOnly) params.set("obfuscated_only", "true");
  return params.toString();
}

export function useThreatFilters() {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { filters, setFilters } = useThreatStore();

  useEffect(() => {
    if (typeof window === "undefined") return;
    const urlFilters = parseFilterParams(new URLSearchParams(window.location.search));
    if (Object.keys(urlFilters).length > 0) {
      setFilters(urlFilters);
    }
  // searchParams added so the effect re-runs on browser back/forward navigation
  }, [pathname, searchParams, setFilters]);

  const updateFilters = (next: Partial<ThreatFilters>) => {
    const merged = { ...filters, ...next };
    setFilters(next);
    const query = buildFilterParams(merged);
    router.push(query ? `${pathname}?${query}` : pathname);
  };

  return { filters, updateFilters };
}
