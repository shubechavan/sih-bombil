"use client";

import { useRouter } from "next/navigation";
import { TacticalPanel, StatCard, RiskGauge, TerminalLog } from "@/components/tactical";
import { Button } from "@/components/ui";
import { CategoryPie, RiskHistogram } from "@/components/charts";
import { usePipelineStore } from "@/stores/pipeline";
import { formatNumber } from "@/lib/format";
import { getSeverityFromScore } from "@/lib/severity";
import styles from "./analysis.module.css";

export default function AnalysisPage() {
  const router = useRouter();
  const { results, config, reset } = usePipelineStore();

  if (results.length === 0) {
    return (
      <TacticalPanel title="Analysis" subtitle="No scan results available">
        <Button variant="primary" onClick={() => router.push("/pipeline/configure")}>
          Start New Scan
        </Button>
      </TacticalPanel>
    );
  }

  const avgRisk =
    results.reduce((sum, r) => sum + r.riskScore, 0) / results.length;
  const threatCount = results.filter((r) => r.riskScore >= 0.6).length;

  // Build category data
  const catMap = new Map<string, number>();
  for (const r of results) {
    const cat = r.category || "uncategorized";
    catMap.set(cat, (catMap.get(cat) ?? 0) + 1);
  }
  const categoryData = Array.from(catMap, ([name, value]) => ({ name, value }));

  // Build risk histogram
  const buckets = [
    { range: "0-0.2", min: 0, max: 0.2, color: "var(--ds-severity-low)" },
    { range: "0.2-0.4", min: 0.2, max: 0.4, color: "var(--ds-severity-low)" },
    { range: "0.4-0.6", min: 0.4, max: 0.6, color: "var(--ds-severity-medium)" },
    { range: "0.6-0.8", min: 0.6, max: 0.8, color: "var(--ds-severity-high)" },
    { range: "0.8-1.0", min: 0.8, max: 1.0, color: "var(--ds-severity-critical)" },
  ];
  const histogramData = buckets.map((b) => ({
    range: b.range,
    count: results.filter((r) => r.riskScore >= b.min && r.riskScore < b.max).length,
    color: b.color,
  }));

  // Build log entries
  const logEntries = results.slice(0, 20).map((r, i) => {
    const sev = getSeverityFromScore(r.riskScore);
    return {
      id: r.rowHash,
      timestamp: new Date().toLocaleTimeString(),
      message: `[${sev}] ${r.rowHash.slice(0, 12)} — score: ${r.riskScore.toFixed(3)}`,
      type: sev === "CRITICAL" ? "error" as const : sev === "HIGH" ? "warn" as const : "info" as const,
    };
  });

  return (
    <div className={styles.wrapper}>
      <div className={styles.statRow}>
        <StatCard label="Results" value={formatNumber(results.length)} />
        <StatCard label="Threats" value={threatCount} severity="high" />
        <StatCard label="Queries" value={config.queries.length} />
        <div className={styles.gaugeCard}>
          <RiskGauge score={avgRisk} />
        </div>
      </div>

      <div className={styles.chartRow}>
        <CategoryPie data={categoryData} />
        <RiskHistogram data={histogramData} />
      </div>

      <TerminalLog title="scan results" entries={logEntries} />

      <div className={styles.actions}>
        <Button variant="secondary" onClick={() => router.push("/threats")}>
          View in Threat Feed
        </Button>
        <Button variant="danger" onClick={() => { reset(); router.push("/pipeline"); }}>
          Clear & Reset
        </Button>
      </div>
    </div>
  );
}
