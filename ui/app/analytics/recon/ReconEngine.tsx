"use client";

import { useEffect, useState } from "react";
import { bff } from "@/lib/api";
import { TacticalPanel, StatCard, ConfidenceMeter } from "@/components/tactical";
import { Skeleton } from "@/components/ui";
import styles from "../analytics.module.css";

interface ReconResult {
	query: string;
	totalResults: number;
	matchRate: number;
	avgConfidence: number;
	topSources: string[];
}

interface ReconData {
	totalQueries: number;
	avgMatchRate: number;
	avgConfidence: number;
	totalResults: number;
	results: ReconResult[];
}

export default function ReconEngine() {
	const [data, setData] = useState<ReconData | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		bff.get("analytics/recon")
			.json<ReconData>()
			.then((d) => { setData(d); setError(null); })
			.catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load recon data"))
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
		return <TacticalPanel title="Recon Engine"><span style={{ color: "var(--ds-critical)" }}>Backend error: {error}</span></TacticalPanel>;
	}

	if (!data) {
		return <TacticalPanel title="Recon Engine">No recon data available</TacticalPanel>;
	}

	return (
		<div className={styles.content}>
			<div className={styles.statRow}>
				<StatCard label="Total Queries" value={data.totalQueries} />
				<StatCard label="Avg Match Rate" value={`${(data.avgMatchRate * 100).toFixed(1)}%`} />
				<StatCard label="Avg Confidence" value={data.avgConfidence.toFixed(2)} />
				<StatCard label="Total Results" value={data.totalResults} />
			</div>

			<TacticalPanel title="Query Results">
				<div className={styles.tableWrapper}>
					<table className={styles.table}>
						<thead>
							<tr>
								<th>Query</th>
								<th>Results</th>
								<th>Match Rate</th>
								<th>Confidence</th>
								<th>Top Sources</th>
							</tr>
						</thead>
						<tbody>
							{data.results.map((r) => (
								<tr key={r.query}>
									<td>{r.query}</td>
									<td>{r.totalResults}</td>
									<td>{(r.matchRate * 100).toFixed(1)}%</td>
									<td>
										<ConfidenceMeter value={r.avgConfidence} />
									</td>
									<td>{r.topSources.slice(0, 3).join(", ")}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</TacticalPanel>
		</div>
	);
}
