/**
 * DarkSentinel Risk Score Formula:
 * R = 0.35·T + 0.45·C + 0.20·H
 *
 * T = Threat classification confidence (RoBERTa)
 * C = Consensus weight (dual-model agreement)
 * H = Historical frequency signal
 */

const W_THREAT = 0.35;
const W_CONSENSUS = 0.45;
const W_HISTORICAL = 0.20;

export interface RiskInputs {
  threatConfidence: number;
  consensusWeight: number;
  historicalSignal: number;
}

export function computeRiskScore({ threatConfidence, consensusWeight, historicalSignal }: RiskInputs): number {
  const raw = W_THREAT * threatConfidence + W_CONSENSUS * consensusWeight + W_HISTORICAL * historicalSignal;
  return Math.round(raw * 100) / 100;
}

export function getRiskFormula(inputs: RiskInputs): string {
  const t = inputs.threatConfidence.toFixed(2);
  const c = inputs.consensusWeight.toFixed(2);
  const h = inputs.historicalSignal.toFixed(2);
  return `R = 0.35(${t}) + 0.45(${c}) + 0.20(${h})`;
}
