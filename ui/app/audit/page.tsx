"use client";

import { TacticalPanel } from "@/components/tactical";
import { Select } from "@/components/ui";
import type { AuditPage as AuditPayload } from "@/lib/attribution";
import { api, formatDate } from "@/lib/attribution";
import { useEffect, useState } from "react";
import styles from "./audit.module.css";

/**
 * Who did what. Admin only, and reading it is itself audited.
 *
 * Two tables rather than one merged stream, because they answer different
 * questions and merging them would imply a shared vocabulary they do not have.
 * `requests` is what people looked at; `scans` is what the pipeline did.
 */

function statusClass(status: number | null): string {
	if (status === null) return "";
	if (status >= 500) return "bad";
	if (status === 401 || status === 403) return "denied";
	if (status >= 400) return "warn";
	return "ok";
}

export default function AuditPage() {
	const [data, setData] = useState<AuditPayload | null>(null);
	const [operator, setOperator] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);

	useEffect(() => {
		let cancelled = false;
		setLoading(true);
		api<AuditPayload>("/audit", { limit: 200, operator: operator || undefined })
			.then((payload) => {
				if (!cancelled) {
					setData(payload);
					setError(null);
				}
			})
			.catch((err: Error) => !cancelled && setError(err.message))
			.finally(() => !cancelled && setLoading(false));
		return () => {
			cancelled = true;
		};
	}, [operator]);

	return (
		<div className={styles.page}>
			<header className={styles.header}>
				<h1 className={styles.title}>Audit</h1>
				{data && <p className={styles.lede}>{data.note}</p>}
			</header>

			{error && (
				<p className={styles.error} role="alert">
					{error}
				</p>
			)}

			{/* On an error there is nothing to filter and nothing to tabulate.
			    Rendering empty tables under a 403 reads as "the log is empty",
			    which is a different and much worse claim than "you may not read
			    this". */}
			{error ? null : (
				<>
					<div className={styles.filters}>
						<Select
							aria-label="Filter by operator"
							value={operator}
							onChange={(e) => setOperator(e.target.value)}
							options={[
								{ value: "", label: "All operators" },
								...(data?.operators ?? []).map((name) => ({ value: name, label: name })),
							]}
						/>
						{data && (
							<span className={styles.counts}>
								{data.total_requests} requests · {data.total_scans} pipeline runs
							</span>
						)}
					</div>

					<TacticalPanel title="Requests" loading={loading}>
						{data && data.requests.length === 0 ? (
							<p className={styles.empty}>No requests recorded yet.</p>
						) : (
							<div className={styles.tableWrap}>
								<table className={styles.table}>
									<caption className="sr-only">
										Every authenticated request, most recent first
									</caption>
									<thead>
										<tr>
											<th scope="col">When</th>
											<th scope="col">Operator</th>
											<th scope="col">Role</th>
											<th scope="col">Method</th>
											<th scope="col">Path</th>
											<th scope="col">Status</th>
											<th scope="col">Action hash</th>
										</tr>
									</thead>
									<tbody>
										{(data?.requests ?? []).map((row) => (
											<tr key={row.id}>
												<td className={styles.when}>{formatDate(row.at)}</td>
												<td className={styles.mono}>{row.operator_id}</td>
												<td>{row.role}</td>
												<td className={styles.mono}>{row.method}</td>
												<td className={styles.mono}>
													{row.path}
													{row.query && <span className={styles.query}>?{row.query}</span>}
												</td>
												<td>
													<span className={styles.status} data-kind={statusClass(row.status)}>
														{row.status}
													</span>
												</td>
												<td className={styles.hash} title={row.action_hash ?? ""}>
													{row.action_hash?.slice(0, 12)}
												</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						)}
					</TacticalPanel>

					<TacticalPanel title="Pipeline runs">
						<div className={styles.tableWrap}>
							<table className={styles.table}>
								<caption className="sr-only">Every run that touched the data</caption>
								<thead>
									<tr>
										<th scope="col">Started</th>
										<th scope="col">Operator</th>
										<th scope="col">Mode</th>
										<th scope="col">Action</th>
										<th scope="col">Status</th>
										<th scope="col">New</th>
										<th scope="col">Job</th>
									</tr>
								</thead>
								<tbody>
									{(data?.scans ?? []).map((row) => (
										<tr key={row.id}>
											<td className={styles.when}>{formatDate(row.started_at)}</td>
											<td className={styles.mono}>{row.operator_id}</td>
											<td>{row.mode}</td>
											<td className={styles.mono}>{row.query}</td>
											<td>
												<span
													className={styles.status}
													data-kind={row.status === "ok" ? "ok" : "warn"}
												>
													{row.status}
												</span>
												{row.error && <span className={styles.err}>{row.error}</span>}
											</td>
											<td className={styles.mono}>
												{row.personas_new ? `${row.personas_new}p ` : ""}
												{row.links_new ? `${row.links_new}l` : ""}
											</td>
											<td className={styles.hash}>{row.job_id?.slice(0, 8)}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</TacticalPanel>
				</>
			)}
		</div>
	);
}
