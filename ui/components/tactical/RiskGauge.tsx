"use client";

import { getSeverityFromScore, getSeverityConfig } from "@/lib/severity";
import { formatScore } from "@/lib/format";
import styles from "./RiskGauge.module.css";

interface RiskGaugeProps {
  score: number;
}

export function RiskGauge({ score }: RiskGaugeProps) {
  const severity = getSeverityFromScore(score);
  const config = getSeverityConfig(severity);
  const variant = severity.toLowerCase();

  // Semi-circle arc: 180 degrees
  const radius = 65;
  const circumference = Math.PI * radius;
  const clamped = Math.max(0, Math.min(1, score));
  const offset = circumference * (1 - clamped);

  return (
    <div className={styles.wrapper}>
      <div className={styles.gauge}>
        <svg viewBox="0 0 160 90" width="160" height="90">
          {/* Background arc */}
          <path
            className={`${styles.arc} ${styles.arcBg}`}
            d="M 15 85 A 65 65 0 0 1 145 85"
          />
          {/* Filled arc */}
          <path
            className={`${styles.arc} ${styles.arcFill}`}
            d="M 15 85 A 65 65 0 0 1 145 85"
            data-severity={variant}
            strokeDasharray={circumference}
            strokeDashoffset={offset}
          />
        </svg>
        <span className={styles.scoreLabel}>{formatScore(score)}</span>
      </div>
      <span className={styles.riskLabel} data-severity={variant}>
        {config.label}
      </span>
    </div>
  );
}
