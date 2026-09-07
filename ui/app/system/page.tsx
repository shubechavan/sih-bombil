"use client";

import { useEffect } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { useHealthStore } from "@/stores/health";
import { TacticalPanel } from "@/components/tactical";
import { Button, Badge } from "@/components/ui";
import { SERVICE_LABELS } from "@/types/health";
import type { ServiceName, ServiceState, ServiceStatus } from "@/types/health";
import styles from "./system.module.css";

const STATE_VARIANT: Record<ServiceState, "accent" | "critical" | "medium" | "neutral"> = {
	online: "accent",
	degraded: "medium",
	offline: "critical",
	checking: "neutral",
};

const STATE_LABEL: Record<ServiceState, string> = {
	online: "Online",
	degraded: "Degraded",
	offline: "Offline",
	checking: "Checking…",
};

export default function SystemPage() {
	const { services, cicLive, refresh } = useHealthStore();

	useEffect(() => {
		refresh();
	}, [refresh]);

	const serviceEntries = Object.entries(services) as [ServiceName, ServiceStatus][];

	const onlineCount = serviceEntries.filter(([, s]) => s.status === "online").length;
	const totalCount = serviceEntries.length;

	return (
		<div className={styles.wrapper}>
			<TacticalPanel
				title="System Diagnostics"
				icon={<Activity size={16} />}
				headerRight={
					<Button variant="ghost" size="sm" onClick={() => refresh()}>
						<RefreshCw size={14} />
						Refresh
					</Button>
				}
			>
				<div className={styles.summary}>
					<span className={styles.summaryText}>
						{onlineCount}/{totalCount} services operational
					</span>
					<Badge variant={onlineCount === totalCount ? "accent" : onlineCount > 0 ? "medium" : "critical"}>
						{onlineCount === totalCount ? "All Systems Go" : "Degraded"}
					</Badge>
				</div>
			</TacticalPanel>

			{cicLive && (
				<TacticalPanel title="CIC Live Feed" icon={<Activity size={16} />}>
					<div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "var(--ds-space-4)" }}>
						<div>
							<span style={{ fontFamily: "var(--ds-font-mono)", fontSize: "0.72rem", color: "var(--ds-text-muted)", textTransform: "uppercase" }}>Prediction</span>
							<div style={{ fontFamily: "var(--ds-font-mono)", fontWeight: 600, fontSize: "0.9rem", color: cicLive.trigger_scrape ? "var(--ds-severity-critical)" : "var(--ds-severity-low)", marginTop: "0.25rem" }}>
								{cicLive.latest_prediction ?? "—"}
							</div>
						</div>
						<div>
							<span style={{ fontFamily: "var(--ds-font-mono)", fontSize: "0.72rem", color: "var(--ds-text-muted)", textTransform: "uppercase" }}>T-Score</span>
							<div style={{ fontFamily: "var(--ds-font-mono)", fontWeight: 600, fontSize: "0.9rem", color: (cicLive.latest_t_score ?? 0) > 0.7 ? "var(--ds-severity-critical)" : "var(--ds-text-primary)", marginTop: "0.25rem" }}>
								{cicLive.latest_t_score != null ? cicLive.latest_t_score.toFixed(3) : "—"}
							</div>
						</div>
						<div>
							<span style={{ fontFamily: "var(--ds-font-mono)", fontSize: "0.72rem", color: "var(--ds-text-muted)", textTransform: "uppercase" }}>Scrape Triggered</span>
								<div style={{ marginTop: "0.25rem" }}>
									<Badge variant={cicLive.trigger_scrape ? "critical" : "neutral"}>
										{cicLive.trigger_scrape ? "Yes" : "No"}
									</Badge>
								</div>
						</div>
					</div>
					{cicLive.last_modified_utc && (
						<div style={{ marginTop: "var(--ds-space-3)", fontFamily: "var(--ds-font-mono)", fontSize: "0.72rem", color: "var(--ds-text-muted)" }}>
							Last updated: {cicLive.last_modified_utc}
						</div>
					)}
				</TacticalPanel>
			)}

			<div className={styles.grid}>
				{serviceEntries.map(([name, status]) => (
					<div key={name} className={styles.serviceCard} data-state={status.status}>
						<div className={styles.serviceHeader}>
							<span className={styles.serviceName}>{SERVICE_LABELS[name]}</span>
							<Badge variant={STATE_VARIANT[status.status]} dot>
								{STATE_LABEL[status.status]}
							</Badge>
						</div>
						<div className={styles.serviceMeta}>
							<span className={styles.serviceId}>{name}</span>
							{status.latency !== undefined && (
								<span className={styles.serviceLatency}>{status.latency}ms</span>
							)}
						</div>
						<div className={styles.serviceIndicator} data-state={status.status} />
					</div>
				))}
			</div>
		</div>
	);
}
