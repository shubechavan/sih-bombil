"use client";

import type { Threat } from "@/types/threat";
import { truncHash, relativeTime, formatScore } from "@/lib/format";
import { getSeverityFromScore } from "@/lib/severity";
import { SeverityPill } from "./SeverityPill";
import styles from "./ThreatRow.module.css";

interface ThreatRowProps {
  threat: Threat;
  selected?: boolean;
  onClick?: () => void;
}

export function ThreatRow({ threat, selected, onClick }: ThreatRowProps) {
  const severity = getSeverityFromScore(threat.riskScore);
  const flowMeta =
    threat.sourceIp && threat.destinationIp
      ? `${threat.sourceIp} -> ${threat.destinationIp}`
      : threat.category;

  return (
    <div
      className={styles.row}
      data-selected={selected}
      onClick={onClick}
      role="row"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onClick?.()}
    >
      <span className={styles.hash}>{truncHash(threat.rowHash)}</span>
      <div className={styles.summary}>
        <span className={styles.text}>{threat.textSnippet}</span>
        <span className={styles.meta}>{flowMeta}</span>
      </div>
      <SeverityPill severity={severity} />
      <span className={styles.score} data-severity={severity.toLowerCase()}>
        {formatScore(threat.riskScore)}
      </span>
      <span className={styles.engine}>{threat.searchEngine}</span>
      <span className={styles.time}>{relativeTime(threat.timestamp)}</span>
    </div>
  );
}
