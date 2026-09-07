"use client";

import { StatCard, TacticalPanel, ThreatRow } from "@/components/tactical";
import { Button, Select } from "@/components/ui";
import { useThreatFilters } from "@/hooks";
import { THREAT_CATEGORIES } from "@/lib/constants";
import { formatPercent } from "@/lib/format";
import { useThreatStore } from "@/stores/threats";
import type { Severity } from "@/types/threat";
import { Download, FilterX, Search, ShieldAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import styles from "./threats.module.css";

const RESET_FILTERS = {
	severity: [],
	search: null,
	category: null,
	engine: null,
	threatsOnly: false,
	obfuscatedOnly: false,
	limit: 50,
};

function ThreatsContent() {
	const router = useRouter();
	const { filters, updateFilters } = useThreatFilters();
	const {
		items,
		total,
		page,
		pages,
		isLoading,
		source,
		fallbackActive,
		fallbackReason,
		fetchPage,
		exportCSV,
	} = useThreatStore();
	const [searchDraft, setSearchDraft] = useState(filters.search ?? "");

	useEffect(() => {
		if (!filters) return;
		fetchPage(1);
	}, [fetchPage, filters]);

	useEffect(() => {
		setSearchDraft(filters.search ?? "");
	}, [filters.search]);

	const criticalCount = useMemo(() => items.filter((t) => t.riskScore >= 0.8).length, [items]);
	const threatRatio = useMemo(() => {
		if (items.length === 0) return "0%";
		const threatLike = items.filter((t) => t.riskScore >= 0.6).length;
		return formatPercent(threatLike / items.length, 0);
	}, [items]);
	const torTaggedCount = useMemo(
		() =>
			items.filter((t) => `${t.category} ${t.label ?? ""}`.toLowerCase().includes("tor")).length,
		[items],
	);

	const hasFilters =
		Boolean(filters.search?.trim()) ||
		Boolean(filters.category?.trim()) ||
		Boolean(filters.engine?.trim()) ||
		Boolean(filters.severity?.length) ||
		filters.threatsOnly ||
		filters.obfuscatedOnly;

	const applySearch = () => {
		updateFilters({ search: searchDraft.trim() || null });
	};

	return (
		<div className={styles.wrapper}>
			<section className={styles.hero}>
				<div>
					<h1 className={styles.title}>Threat Feed</h1>
					<p className={styles.subtitle}>
						Prioritized incident queue with severity triage, category slicing, and export-ready flow
						data.
					</p>
				</div>

				<div className={styles.heroActions}>
					<Button variant="secondary" size="sm" onClick={() => fetchPage(page)}>
						Refresh
					</Button>
					<Button variant="secondary" size="sm" icon={Download} onClick={exportCSV}>
						Export CSV
					</Button>
				</div>
			</section>

			<section className={styles.statGrid}>
				<StatCard label="Visible Events" value={items.length} />
				<StatCard label="Critical" value={criticalCount} severity="critical" />
				<StatCard
					label="Threat Ratio"
					value={threatRatio}
					severity={criticalCount > 0 ? "high" : "low"}
				/>
				<StatCard label="Tor-Tagged" value={torTaggedCount} />
			</section>

			<section className={fallbackActive ? styles.sourceBannerWarn : styles.sourceBannerOk}>
				<strong>Data Source:</strong> {source === "backend" ? "FastAPI + PostgreSQL" : "In-memory backup"}
				{fallbackActive && (
					<span>
						{" "}· PostgreSQL unavailable, using in-memory alert history
						{fallbackReason ? ` (${fallbackReason})` : ""}
					</span>
				)}
			</section>

			<TacticalPanel
				title="Operational Threat Queue"
				subtitle={`${total} records across all pages`}
			>
				<div className={styles.filters}>
					<div className={styles.searchWrap}>
						<label htmlFor="threat-search" className={styles.searchLabel}>
							Search
						</label>
						<div className={styles.searchInputWrap}>
							<Search size={14} />
							<input
								id="threat-search"
								className={styles.searchInput}
								placeholder="hash, IP, category, protocol..."
								value={searchDraft}
								onChange={(e) => setSearchDraft(e.target.value)}
								onKeyDown={(e) => {
									if (e.key === "Enter") applySearch();
								}}
							/>
							<Button variant="ghost" size="sm" onClick={applySearch}>
								Apply
							</Button>
						</div>
					</div>

					<div className={styles.filterControls}>
						<Select
							label="Severity"
							value={filters.severity?.[0] ?? ""}
							onChange={(e) =>
								updateFilters({
									severity: e.target.value ? [e.target.value as Severity] : [],
								})
							}
							options={[
								{ value: "", label: "All Severities" },
								{ value: "CRITICAL", label: "Critical" },
								{ value: "HIGH", label: "High" },
								{ value: "MEDIUM", label: "Medium" },
								{ value: "LOW", label: "Low" },
							]}
						/>

						<Select
							label="Category"
							value={filters.category ?? ""}
							onChange={(e) => updateFilters({ category: e.target.value || null })}
							options={[
								{ value: "", label: "All Categories" },
								...THREAT_CATEGORIES.map((c) => ({ value: c, label: c })),
							]}
						/>

						{hasFilters && (
							<Button variant="ghost" size="sm" onClick={() => updateFilters(RESET_FILTERS)}>
								<FilterX size={14} />
								Clear
							</Button>
						)}
					</div>
				</div>

				<div className={styles.listHeader}>
					<span>Hash</span>
					<span>Flow / Context</span>
					<span>Severity</span>
					<span>Score</span>
					<span>Engine</span>
					<span>Seen</span>
				</div>

				<div className={styles.list}>
					{isLoading ? (
						<div className={styles.loading}>Loading threats…</div>
					) : items.length === 0 ? (
						<div className={styles.emptyState}>
							<ShieldAlert size={18} />
							<p>No records matched your current filters.</p>
						</div>
					) : (
						items.map((threat) => (
							<ThreatRow
								key={threat.rowHash}
								threat={threat}
								onClick={() => router.push(`/threats/${threat.rowHash}`)}
							/>
						))
					)}
				</div>

				{pages > 1 && (
					<div className={styles.pagination}>
						<Button
							variant="ghost"
							size="sm"
							disabled={page <= 1}
							onClick={() => fetchPage(page - 1)}
						>
							Previous
						</Button>
						<span className={styles.pageInfo}>
							Page {page} of {pages}
						</span>
						<Button
							variant="ghost"
							size="sm"
							disabled={page >= pages}
							onClick={() => fetchPage(page + 1)}
						>
							Next
						</Button>
					</div>
				)}
			</TacticalPanel>
		</div>
	);
}
export default function ThreatsPage() {
	return (
		<Suspense>
			<ThreatsContent />
		</Suspense>
	);
}