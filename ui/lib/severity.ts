import type { Severity } from "@/types/threat";

/**
 * Maps a 0..1 score onto the severity vocabulary the tactical components are
 * styled against.
 *
 * v1's SEVERITY table also carried `action` and `routing` fields describing an
 * n8n alerting flow that does not exist in v2; they went with the alerts page.
 * What the console needs is the threshold ladder and nothing else.
 *
 * Attribution confidence bands do NOT come through here — they have their own
 * mapping in lib/attribution.ts (BAND_TOKEN), because a band is a statement
 * about evidence rather than about urgency.
 */

const THRESHOLDS: ReadonlyArray<readonly [Severity, number]> = [
  ["CRITICAL", 0.8],
  ["HIGH", 0.6],
  ["MEDIUM", 0.3],
  ["LOW", 0.0],
];

export function getSeverityFromScore(score: number): Severity {
  for (const [severity, floor] of THRESHOLDS) {
    if (score >= floor) return severity;
  }
  return "LOW";
}
