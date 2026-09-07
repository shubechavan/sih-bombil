export type ServiceState = "online" | "offline" | "degraded" | "checking";

export interface ServiceStatus {
  status: ServiceState;
  latency?: number;
  error?: string;
  lastChecked?: number;
}

export interface HealthCheck {
  tor: ServiceStatus;
  postgres: ServiceStatus;
  n8n: ServiceStatus;
  fastapi: ServiceStatus;
  roberta: ServiceStatus;
  scheduler: ServiceStatus;
}

export interface BackendHealthPayload {
  status?: string;
  services?: Partial<Record<ServiceName, ServiceStatus | { state?: ServiceState; status?: ServiceState }>>;
  cic_live_feed?: {
    last_modified_utc?: string;
    latest_prediction?: string;
    latest_t_score?: number;
    trigger_scrape?: boolean;
    detected_at?: string;
  };
}

export type ServiceName = keyof HealthCheck;

export const SERVICE_LABELS: Record<ServiceName, string> = {
  tor: "Tor Proxy",
  postgres: "PostgreSQL",
  n8n: "n8n Orchestrator",
  fastapi: "FastAPI Backend",
  roberta: "RoBERTa Model",
  scheduler: "APScheduler",
};
