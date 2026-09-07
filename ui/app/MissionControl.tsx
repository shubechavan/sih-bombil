"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Shield, Activity, AlertTriangle, Eye } from "lucide-react";
import { StatCard, TacticalPanel, ThreatRow } from "@/components/tactical";
import { SeverityTrend } from "@/components/charts";
import { useThreatStore } from "@/stores/threats";
import { useHealthStore } from "@/stores/health";
import styles from "./MissionControl.module.css";

export function MissionControl() {
  const router = useRouter();
  const { items, total, isLoading, fetchPage } = useThreatStore();
  const { services } = useHealthStore();

  useEffect(() => {
    fetchPage(1);
  }, [fetchPage]);

  const criticalCount = items.filter((t) => t.riskScore >= 0.8).length;
  const activeServices = Object.values(services).filter(
    (s) => s.status === "online"
  ).length;

  // Build simple trend data from recent items
  const trendData = items.slice(0, 7).map((t, i) => ({
    date: `T-${7 - i}`,
    critical: t.riskScore >= 0.8 ? 1 : 0,
    high: t.riskScore >= 0.6 && t.riskScore < 0.8 ? 1 : 0,
    medium: t.riskScore >= 0.3 && t.riskScore < 0.6 ? 1 : 0,
    low: t.riskScore < 0.3 ? 1 : 0,
  }));

  return (
    <div className={styles.grid}>
      <StatCard
        label="Total Threats"
        value={total}
        icon={Shield}
        onClick={() => router.push("/threats")}
      />
      <StatCard
        label="Critical"
        value={criticalCount}
        severity="critical"
        icon={AlertTriangle}
        onClick={() => router.push("/threats?severity=CRITICAL")}
      />
      <StatCard
        label="Active Services"
        value={`${activeServices}/${Object.values(services).length}`}
        icon={Activity}
        onClick={() => router.push("/system")}
      />
      <StatCard
        label="Scans Today"
        value={items.length}
        icon={Eye}
        onClick={() => router.push("/pipeline")}
      />

      <div className={styles.wide}>
        <SeverityTrend data={trendData} />
      </div>

      <div className={styles.wide}>
        <TacticalPanel title="Recent Threats" loading={isLoading}>
          <div className={styles.recentThreats}>
            {items.slice(0, 8).map((threat) => (
              <ThreatRow
                key={threat.rowHash}
                threat={threat}
                onClick={() => router.push(`/threats/${threat.rowHash}`)}
              />
            ))}
          </div>
        </TacticalPanel>
      </div>
    </div>
  );
}
