"use client";

import { useEffect, useState } from "react";
import { bff } from "@/lib/api";
import { TacticalPanel, StatCard, SeverityPill } from "@/components/tactical";
import { Skeleton } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import styles from "../analytics.module.css";

interface AnomalyEvent {
	id: string;
	type: string;
	source_ip: string;
	destination: string;
	severity: "critical" | "high" | "medium" | "low";
	timestamp: string;
	description: string;
}

interface NetworkData {
	totalAnomalies: number;
	criticalAnomalies: number;
	uniqueSources: number;
	activeAlerts: number;
	events: AnomalyEvent[];
}

function normalizeSeverity(value: AnomalyEvent["severity"]): "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" {
	const sev = String(value || "low").toUpperCase();
	if (sev === "CRITICAL" || sev === "HIGH" || sev === "MEDIUM" || sev === "LOW") {
		return sev;
	}
	return "LOW";
}

export default function NetworkAnomaly() {
	const [data, setData] = useState<NetworkData | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		bff.get("network")
			.json<NetworkData>()
			.then((d) => { setData(d); setError(null); })
			.catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load network data"))
			.finally(() => setLoading(false));
	}, []);

	if (loading) {
		return (
			<div className={styles.content}>
				<div className={styles.statRow}>
					{Array.from({ length: 4 }).map((_, i) => (
						<Skeleton key={i} variant="card" />
					))}
				</div>
				<Skeleton variant="card" />
			</div>
		);
	}

	if (error) {
		return <TacticalPanel title="Network Anomaly"><span style={{ color: "var(--ds-critical)" }}>Backend error: {error}</span></TacticalPanel>;
	}

	if (!data) {
		return <TacticalPanel title="Network Anomaly">No network data available</TacticalPanel>;
	}

	return (
		<div className={styles.content}>
			<div className={styles.statRow}>
				<StatCard label="Total Anomalies" value={data.totalAnomalies} />
				<StatCard label="Critical" value={data.criticalAnomalies} severity="critical" />
				<StatCard label="Unique Sources" value={data.uniqueSources} />
				<StatCard label="Active Alerts" value={data.activeAlerts} severity="high" />
			</div>

			<TacticalPanel title="Anomaly Events">
				<div className={styles.anomalyList}>
					{data.events.map((event) => (
						<div key={event.id} className={styles.anomalyCard}>
							<SeverityPill severity={normalizeSeverity(event.severity)} />
							<div className={styles.anomalyInfo}>
								<span className={styles.anomalyTitle}>{event.type}: {event.description}</span>
								<span className={styles.anomalyMeta}>
									{event.source_ip} → {event.destination} · {relativeTime(event.timestamp)}
								</span>
							</div>
						</div>
					))}
					{data.events.length === 0 && <p>No anomaly events detected.</p>}
				</div>
			</TacticalPanel>
		</div>
	);
}
