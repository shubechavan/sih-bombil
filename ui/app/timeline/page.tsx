"use client";

import { ChartShell } from "@/components/charts";
import { StatCard, TacticalPanel } from "@/components/tactical";
import { Button, Select } from "@/components/ui";
import { type TimelineBucket, api } from "@/lib/attribution";
import { CalendarRange, FilterX } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
	Area,
	AreaChart,
	CartesianGrid,
	ResponsiveContainer,
	Tooltip,
	XAxis,
	YAxis,
} from "recharts";
import styles from "./timeline.module.css";

export default function TimelinePage() {
	const [buckets, setBuckets] = useState<TimelineBucket[]>([]);
	const [granularity, setGranularity] = useState("month");
	const [start, setStart] = useState("");
	const [end, setEnd] = useState("");
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		setLoading(true);
		api<TimelineBucket[]>("/timeline", {
			bucket: granularity,
			start: start || undefined,
			end: end || undefined,
		})
			.then((rows) => {
				if (!cancelled) {
					setBuckets(rows);
					setError(null);
				}
			})
			.catch((err: Error) => !cancelled && setError(err.message))
			.finally(() => !cancelled && setLoading(false));
		return () => {
			cancelled = true;
		};
	}, [granularity, start, end]);

	const totals = useMemo(
		() => ({
			posts: buckets.reduce((sum, b) => sum + b.posts, 0),
			peak: buckets.reduce((max, b) => Math.max(max, b.posts), 0),
			span: buckets.length,
		}),
		[buckets],
	);

	return (
		<div className={styles.page}>
			<header className={styles.header}>
				<h1 className={styles.title}>
					<CalendarRange size={20} /> Timeline
				</h1>
				<p className={styles.subtitle}>
					Activity from <code>posts.posted_at</code> — when an actor was actually posting, not when
					we happened to scrape them. Posting-hour rhythm is the dominant behavioural sub-signal,
					and it is what survives a rebrand.
				</p>
			</header>

			<div className={styles.stats}>
				<StatCard label="Posts" value={String(totals.posts)} />
				<StatCard label="Buckets" value={String(totals.span)} />
				<StatCard label="Busiest bucket" value={String(totals.peak)} />
			</div>

			<TacticalPanel
				title="Activity over time"
				subtitle={granularity}
				loading={loading}
				headerRight={
					<div className={styles.filters}>
						<Select
							value={granularity}
							onChange={(e) => setGranularity(e.target.value)}
							aria-label="Bucket granularity"
							options={[
								{ value: "day", label: "Daily" },
								{ value: "week", label: "Weekly" },
								{ value: "month", label: "Monthly" },
							]}
						/>
						<label className={styles.dateLabel}>
							from
							<input
								type="date"
								className={styles.date}
								value={start}
								onChange={(e) => setStart(e.target.value)}
							/>
						</label>
						<label className={styles.dateLabel}>
							to
							<input
								type="date"
								className={styles.date}
								value={end}
								onChange={(e) => setEnd(e.target.value)}
							/>
						</label>
						<Button
							variant="ghost"
							onClick={() => {
								setStart("");
								setEnd("");
							}}
						>
							<FilterX size={14} /> Clear range
						</Button>
					</div>
				}
			>
				{error ? (
					<p className={styles.error}>{error}</p>
				) : (
					<ChartShell title="Posts per bucket" empty={buckets.length === 0}>
						<ResponsiveContainer width="100%" height={300}>
							<AreaChart data={buckets} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
								<defs>
									<linearGradient id="postsFill" x1="0" y1="0" x2="0" y2="1">
										<stop offset="0%" stopColor="var(--ds-accent, #4da3ff)" stopOpacity={0.55} />
										<stop offset="100%" stopColor="var(--ds-accent, #4da3ff)" stopOpacity={0.04} />
									</linearGradient>
								</defs>
								<CartesianGrid
									strokeDasharray="3 3"
									stroke="var(--ds-border-subtle, rgba(255,255,255,0.06))"
								/>
								<XAxis
									dataKey="bucket"
									tick={{ fontSize: 10, fill: "var(--ds-text-dim, #6b7280)" }}
									stroke="var(--ds-border, #2b3039)"
								/>
								<YAxis
									tick={{ fontSize: 10, fill: "var(--ds-text-dim, #6b7280)" }}
									stroke="var(--ds-border, #2b3039)"
								/>
								<Tooltip
									contentStyle={{
										background: "var(--ds-glass-heavy, #12151b)",
										border: "1px solid var(--ds-border, #2b3039)",
										borderRadius: "6px",
										fontSize: "0.75rem",
									}}
									formatter={(value: number, name: string) => [
										value,
										name === "posts" ? "posts" : "personas active",
									]}
								/>
								<Area
									type="monotone"
									dataKey="posts"
									stroke="var(--ds-accent, #4da3ff)"
									strokeWidth={2}
									fill="url(#postsFill)"
								/>
								<Area
									type="monotone"
									dataKey="personas"
									stroke="var(--ds-medium, #f0b429)"
									strokeWidth={1.5}
									fillOpacity={0}
								/>
							</AreaChart>
						</ResponsiveContainer>
					</ChartShell>
				)}
				{buckets.length === 0 && !loading && !error && (
					<p className={styles.empty}>No activity in this range.</p>
				)}
			</TacticalPanel>
		</div>
	);
}
