import type { ScanResult } from "./threat";

export type StageStatus = "idle" | "processing" | "complete" | "error" | "skipped";

export type FetchProfile = "balanced" | "stealth" | "aggressive";

export interface PipelineStageState {
  index: number;
  name: string;
  status: StageStatus;
  progress: number;
  metrics: Record<string, number>;
  error?: string;
}

export interface ScanConfig {
  queries: string[];
  maxResults: number;
  fetchProfile: FetchProfile;
  enableObfuscation?: boolean;
  enablePiiScrub?: boolean;
}

export interface ScanStartResponse {
  scanId: string;
  status: "started";
}

export interface StageUpdateEvent {
  type: "stage-update";
  stage: number;
  name: string;
  status: StageStatus;
  progress: number;
  metrics: Record<string, number>;
}

export interface ScanCompleteEvent {
  type: "scan-complete";
  totalRows: number;
  safeRows: number;
  blockedRows: number;
  duration: number;
  results: ScanResult[];
}

export interface ScanErrorEvent {
  type: "scan-error";
  message: string;
}

export type PipelineEvent = StageUpdateEvent | ScanCompleteEvent | ScanErrorEvent;

export const PIPELINE_STAGES = [
  "APScheduler",
  "Ahmia Scraper",
  "ObfusLex Engine",
  "Guard Agent",
  "GLiNER-PII Scrubber",
  "RoBERTa-DDIR",
  "Blink.new Gateway",
  "Risk Engine",
  "n8n Orchestrator",
] as const;

export type { ScanResult };
