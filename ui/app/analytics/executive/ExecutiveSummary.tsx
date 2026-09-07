"use client";

import { useEffect, useState } from "react";
import { bff } from "@/lib/api";
import { TacticalPanel, StatCard, SeverityPill } from "@/components/tactical";
import { SeverityTrend, RiskHistogram, CategoryPie } from "@/components/charts";
import { Skeleton } from "@/components/ui";
import styles from "../analytics.module.css";

interface ExecData {
	totalThreats: number;
	criticalCount: number;
	averageRisk: number;
	topCategory: string;
	severityTrend: Array<{ date: string; critical: number; high: number; medium: number; low: number }>;
	riskDistribution: Array<{ bucket: string; count: number }>;
	categories: Array<{ name: string; value: number }>;
	recentCritical: Array<{ hash: string; title: string; severity: string; score: number }>;
}

function bucketColor(bucket: string): string {
	if (bucket.startsWith("0.8")) return "var(--ds-severity-critical)";
	if (bucket.startsWith("0.6")) return "var(--ds-severity-high)";
	if (bucket.startsWith("0.4")) return "var(--ds-severity-medium)";
	return "var(--ds-severity-low)";
}

function normalizeSeverity(value: string): "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" {
	const sev = String(value || "LOW").toUpperCase();
	if (sev === "CRITICAL" || sev === "HIGH" || sev === "MEDIUM" || sev === "LOW") {
		return sev;
	}
	return "LOW";
}

export default function ExecutiveSummary() {
	const [data, setData] = useState<ExecData | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		bff.get("analytics/executive")
			.json<ExecData>()
			.then((d) => { setData(d); setError(null); })
			.catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load analytics"))
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
		return <TacticalPanel title="Executive Summary"><span style={{ color: "var(--ds-critical)" }}>Backend error: {error}</span></TacticalPanel>;
	}

	if (!data) {
		return <TacticalPanel title="Executive Summary">No data available</TacticalPanel>;
	}

	const histogramData = data.riskDistribution.map((item) => ({
		range: item.bucket,
		count: item.count,
		color: bucketColor(item.bucket),
	}));

	return (
		<div className={styles.content}>
			<div className={styles.statRow}>
				<StatCard label="Total Threats" value={data.totalThreats} />
				<StatCard label="Critical" value={data.criticalCount} severity="critical" />
				<StatCard label="Avg Risk Score" value={data.averageRisk.toFixed(2)} />
				<StatCard label="Top Category" value={data.topCategory} />
			</div>

			<div className={styles.chartGrid}>
				<SeverityTrend data={data.severityTrend} />
				<RiskHistogram data={histogramData} />
				<div className={styles.fullWidth}>
					<CategoryPie data={data.categories} />
				</div>
			</div>

			<TacticalPanel title="Recent Critical Threats">
				<div className={styles.tableWrapper}>
					<table className={styles.table}>
						<thead>
							<tr>
								<th>Hash</th>
								<th>Title</th>
								<th>Severity</th>
								<th>Score</th>
							</tr>
						</thead>
						<tbody>
							{data.recentCritical.map((t) => (
								<tr key={t.hash}>
									<td>{t.hash.slice(0, 12)}…</td>
									<td>{t.title}</td>
									<td>
										<SeverityPill severity={normalizeSeverity(t.severity)} />
									</td>
									<td>{t.score.toFixed(2)}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</TacticalPanel>
		</div>
	);
}
