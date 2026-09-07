import type { Severity } from "@/types/threat";

export const SEVERITY = {
  CRITICAL: {
    color: "var(--ds-critical)",
    bg: "var(--ds-critical-bg)",
    border: "var(--ds-critical-border)",
    label: "CRITICAL",
    shortLabel: "CRIT",
    threshold: 0.8,
    action: "auto_alert" as const,
    routing: "Email + Slack via n8n (immediate)",
  },
  HIGH: {
    color: "var(--ds-high)",
    bg: "var(--ds-high-bg)",
    border: "var(--ds-high-border)",
    label: "HIGH",
    shortLabel: "HIGH",
    threshold: 0.6,
    action: "slack_notify" as const,
    routing: "Slack notification via n8n",
  },
  MEDIUM: {
    color: "var(--ds-medium)",
    bg: "var(--ds-medium-bg)",
    border: "var(--ds-medium-border)",
    label: "MEDIUM",
    shortLabel: "MED",
    threshold: 0.3,
    action: "analyst_queue" as const,
    routing: "Analyst queue in PostgreSQL",
  },
  LOW: {
    color: "var(--ds-low)",
    bg: "var(--ds-low-bg)",
    border: "var(--ds-low-border)",
    label: "LOW",
    shortLabel: "LOW",
    threshold: 0.0,
    action: "log_only" as const,
    routing: "Stored in PostgreSQL only",
  },
} as const;

export function getSeverityFromScore(rScore: number): Severity {
  if (rScore >= 0.8) return "CRITICAL";
  if (rScore >= 0.6) return "HIGH";
  if (rScore >= 0.3) return "MEDIUM";
  return "LOW";
}

export function getSeverityConfig(severity: Severity) {
  return SEVERITY[severity];
}
