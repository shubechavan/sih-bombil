import "server-only";

import type { Severity, Threat, ThreatPage } from "@/types/threat";

function normalizeSeverity(value: unknown): Severity | null {
  const sev = String(value ?? "").toUpperCase();
  if (sev === "CRITICAL" || sev === "HIGH" || sev === "MEDIUM" || sev === "LOW") {
    return sev;
  }
  return null;
}

function clamp01(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function fallbackSeverity(score: number): Severity {
  if (score >= 0.8) return "CRITICAL";
  if (score >= 0.6) return "HIGH";
  if (score >= 0.3) return "MEDIUM";
  return "LOW";
}

function toThreat(raw: Record<string, unknown>, index: number): Threat {
  const riskScore = clamp01(raw.riskScore ?? raw.r_score ?? 0);
  const severity = normalizeSeverity(raw.severity) ?? fallbackSeverity(riskScore);
  const rowHash = String(raw.rowHash ?? raw.source_hash ?? `row-${index}`);

  const textSnippet = String(
    raw.textSnippet ?? raw.clean_text ?? raw.title ?? raw.query ?? "No content",
  );

  const timestamp = String(raw.timestamp ?? raw.created_at ?? new Date().toISOString());

  return {
    rowHash,
    source_hash: String(raw.source_hash ?? rowHash),
    riskScore,
    r_score: riskScore,
    severity,
    category: String(raw.category ?? "unknown"),
    searchEngine: String(raw.searchEngine ?? raw.engine ?? "unknown"),
    engine: String(raw.engine ?? raw.searchEngine ?? "unknown"),
    query: String(raw.query ?? ""),
    title: String(raw.title ?? textSnippet.slice(0, 120)),
    clean_text: String(raw.clean_text ?? textSnippet),
    textSnippet,
    timestamp,
    created_at: timestamp,
    sourceIp: raw.sourceIp as string | undefined,
    destinationIp: raw.destinationIp as string | undefined,
    sourcePort: raw.sourcePort as number | undefined,
    destinationPort: raw.destinationPort as number | undefined,
    protocol: raw.protocol as string | undefined,
    flowDuration: raw.flowDuration as number | undefined,
    bytesPerSecond: raw.bytesPerSecond as number | undefined,
    packetsPerSecond: raw.packetsPerSecond as number | undefined,
    label: raw.label as string | undefined,
    llm_mitre: raw.llm_mitre as string | null | undefined,
    llm_mitre_tactic: raw.llm_mitre_tactic as string | null | undefined,
    llm_mitre_tactic_id: raw.llm_mitre_tactic_id as string | null | undefined,
    llm_mitre_technique_name: raw.llm_mitre_technique_name as string | null | undefined,
  };
}

function resolveBackend(backendUrl?: string): string {
  const envUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
  const fallback = process.env.NODE_ENV === "production" ? "" : "http://localhost:8000";
  const base = (backendUrl?.trim() || envUrl || fallback).replace(/\/+$/, "");
  if (!base) {
    throw new Error("Backend URL is not configured. Set NEXT_PUBLIC_API_URL.");
  }
  return base;
}

export async function getThreatPage(
  backendUrl: string,
  params: URLSearchParams,
): Promise<{ page: ThreatPage; source: "backend"; fallbackReason?: string }> {
  const query = params.toString();
  const url = `${resolveBackend(backendUrl)}/threats${query ? `?${query}` : ""}`;

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Failed to fetch threats");
  }

  const raw = (await res.json()) as {
    items?: Array<Record<string, unknown>>;
    total?: number;
    page?: number;
    pages?: number;
  };

  const itemsRaw = Array.isArray(raw.items) ? raw.items : [];
  const items = itemsRaw.map((item, index) => toThreat(item, index));

  return {
    page: {
      items,
      total: Number(raw.total ?? items.length),
      page: Number(raw.page ?? params.get("page") ?? 1),
      pages: Number(raw.pages ?? 1),
    },
    source: "backend",
  };
}

export async function exportThreatCsv(backendUrl: string, params: URLSearchParams): Promise<string> {
  const query = params.toString();
  const url = `${resolveBackend(backendUrl)}/threats/export/csv${query ? `?${query}` : ""}`;

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "CSV export failed");
  }

  return await res.text();
}
