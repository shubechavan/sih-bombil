"use client";

import { useEffect, useState } from "react";
import { bff } from "@/lib/api";
import { TacticalPanel, StatCard, ConfidenceMeter, SeverityPill } from "@/components/tactical";
import { ConfidenceRadar } from "@/components/charts";
import { Skeleton, Badge } from "@/components/ui";
import styles from "../analytics.module.css";

interface EngineMetric {
	engine: string;
	accuracy: number;
	avgConfidence: number;
	totalAnalyzed: number;
	falsePositiveRate: number;
}

interface IntelData {
	totalAnalyses: number;
	avgAccuracy: number;
	consensusRate: number;
	engines: EngineMetric[];
	radarData: Array<{ subject: string; value: number }>;
	recentDisagreements: Array<{
		hash: string;
		roberta: string;
		blink: string;
		consensus: string;
	}>;
}

function normalizeSeverity(value: string): "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" {
	const sev = String(value || "LOW").toUpperCase();
	if (sev === "CRITICAL" || sev === "HIGH" || sev === "MEDIUM" || sev === "LOW") {
		return sev;
	}
	return "LOW";
}

export default function IntelligenceMatrix() {
	const [data, setData] = useState<IntelData | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		bff.get("analytics/intelligence")
			.json<IntelData>()
			.then((d) => { setData(d); setError(null); })
			.catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load intelligence data"))
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
		return <TacticalPanel title="AI Intelligence Matrix"><span style={{ color: "var(--ds-critical)" }}>Backend error: {error}</span></TacticalPanel>;
	}

	if (!data) {
		return <TacticalPanel title="AI Intelligence Matrix">No intelligence data available</TacticalPanel>;
	}

	const radarSeries = data.radarData.map((point) => ({
		axis: point.subject,
		value: point.value,
	}));

	return (
		<div className={styles.content}>
			<div className={styles.statRow}>
				<StatCard label="Total Analyses" value={data.totalAnalyses} />
				<StatCard label="Avg Accuracy" value={`${(data.avgAccuracy * 100).toFixed(1)}%`} />
				<StatCard label="Consensus Rate" value={`${(data.consensusRate * 100).toFixed(1)}%`} />
				<StatCard label="Active Engines" value={data.engines.length} />
			</div>

			<div className={styles.chartGrid}>
				<TacticalPanel title="Engine Performance">
					<div className={styles.tableWrapper}>
						<table className={styles.table}>
							<thead>
								<tr>
									<th>Engine</th>
									<th>Accuracy</th>
									<th>Confidence</th>
									<th>Analyzed</th>
									<th>FP Rate</th>
								</tr>
							</thead>
							<tbody>
								{data.engines.map((e) => (
									<tr key={e.engine}>
										<td>{e.engine}</td>
										<td>{(e.accuracy * 100).toFixed(1)}%</td>
										<td>
											<ConfidenceMeter value={e.avgConfidence} />
										</td>
										<td>{e.totalAnalyzed}</td>
										<td>{(e.falsePositiveRate * 100).toFixed(1)}%</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				</TacticalPanel>

				<TacticalPanel title="Capability Radar">
					<ConfidenceRadar data={radarSeries} />
				</TacticalPanel>
			</div>

			<TacticalPanel title="Recent Disagreements">
				<div className={styles.tableWrapper}>
					<table className={styles.table}>
						<thead>
							<tr>
								<th>Hash</th>
								<th>RoBERTa</th>
								<th>BLINK</th>
								<th>Consensus</th>
							</tr>
						</thead>
						<tbody>
							{data.recentDisagreements.map((d) => (
								<tr key={d.hash}>
									<td>{d.hash.slice(0, 12)}…</td>
									<td>{d.roberta}</td>
									<td>{d.blink}</td>
									<td>
										<Badge
											variant={
												d.consensus === "THREAT_CONFIRMED"
													? "critical"
													: d.consensus === "HUMAN_REVIEW"
														? "medium"
														: "accent"
											}
										>
											{d.consensus}
										</Badge>
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</TacticalPanel>
		</div>
	);
}
