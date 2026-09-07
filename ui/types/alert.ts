import type { Severity } from "./threat";

export interface AlertPayload {
  severity: Severity;
  message: string;
  source?: string;
  timestamp: string;
}

export interface BackendAlertPayload {
  severity: Severity;
  category: string;
  r_score: number;
  title: string;
  engine: string;
  query: string;
  source_hash: string;
  reasoning: string;
  timestamp: string;
  action: "auto_alert" | "analyst_queue" | "log_only";
}

export interface N8nResponse {
  severity?: Severity | string;
  r_score?: number;
  routed_to?: string[];
  n8n_sent?: boolean;
  n8n_queued?: boolean;
  pg_stored?: boolean;
  errors?: string[];
  test?: string;
  [key: string]: unknown;
}

export interface AlertState {
  id: string;
  payload: AlertPayload;
  status: "pending" | "sent" | "failed";
  timestamp: number;
  response?: N8nResponse;
  error?: string;
}

export const SEVERITY_ROUTING: Record<Severity, string> = {
  CRITICAL: "Email + Slack via n8n (immediate)",
  HIGH: "Slack notification via n8n",
  MEDIUM: "Analyst queue in PostgreSQL",
  LOW: "Stored in PostgreSQL only",
};
