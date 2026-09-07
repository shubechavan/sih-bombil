import { formatPercent } from "@/lib/format";
import styles from "./ConfidenceMeter.module.css";

interface ConfidenceMeterProps {
  label?: string;
  value: number; // 0-1
}

function getLevel(v: number): "high" | "medium" | "low" {
  if (v >= 0.7) return "high";
  if (v >= 0.4) return "medium";
  return "low";
}

export function ConfidenceMeter({ label, value }: ConfidenceMeterProps) {
  const level = getLevel(value);
  const pct = Math.max(0, Math.min(100, value * 100));

  return (
    <div className={styles.meter}>
      {label && <span className={styles.label}>{label}</span>}
      <div className={styles.track}>
        <div
          className={styles.fill}
          data-level={level}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={styles.value} data-level={level}>
        {formatPercent(value)}
      </span>
    </div>
  );
}
