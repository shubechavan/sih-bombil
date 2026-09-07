import type { Severity } from "@/types/threat";
import styles from "./SeverityPill.module.css";

interface SeverityPillProps {
  severity: Severity;
  showDot?: boolean;
}

export function SeverityPill({ severity, showDot = true }: SeverityPillProps) {
  const variant = severity.toLowerCase() as Lowercase<Severity>;
  return (
    <span className={`${styles.pill} ${styles[variant]}`}>
      {showDot && <span className={styles.dot} />}
      {severity}
    </span>
  );
}
