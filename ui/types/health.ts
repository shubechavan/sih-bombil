/**
 * Service health, as v2's API actually reports it.
 *
 * v1 tracked six services — tor, postgres, n8n, fastapi, roberta, scheduler —
 * against a payload shape this backend never returned, so the strip showed
 * "checking" indefinitely for all of them and forced `fastapi` to online.
 * These three are what `GET /health` genuinely knows: whether it answered,
 * whether it can reach Postgres, and whether the pipeline has been run far
 * enough to have actors to show.
 */
export type ServiceState = "online" | "offline" | "degraded" | "checking";

export interface ServiceStatus {
  status: ServiceState;
  detail?: string;
  lastChecked?: number;
}

export interface HealthCheck {
  api: ServiceStatus;
  database: ServiceStatus;
  pipeline: ServiceStatus;
}

export type ServiceName = keyof HealthCheck;

export const SERVICE_LABELS: Record<ServiceName, string> = {
  api: "API",
  database: "PostgreSQL",
  pipeline: "Pipeline",
};

/** The shape api/main.py:health() returns. */
export interface BackendHealthPayload {
  status?: string;
  database?: string;
  ready?: boolean;
  hint?: string | null;
  counts?: Record<string, number>;
}
