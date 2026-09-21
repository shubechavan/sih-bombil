"use client";

import { RefusalNotice } from "@/components/attribution";
import { TacticalPanel } from "@/components/tactical";
import { Button, Select, Toggle } from "@/components/ui";
import { useAuthStore } from "@/stores/auth";
import { Download, Play } from "lucide-react";
import { useState } from "react";
import styles from "./export.module.css";

/**
 * Column sets mirror api/routers/export.py. The `*_reason` columns are listed
 * as first-class choices and selected by default: a CSV that drops them turns
 * every unmeasured component into an empty cell with no explanation, which is
 * the spreadsheet version of rendering 0.00.
 */
const DATASETS = {
	links: [
		"persona_a",
		"handle_a",
		"persona_b",
		"handle_b",
		"score",
		"band",
		"method",
		"H",
		"H_reason",
		"S",
		"S_reason",
		"B",
		"B_reason",
		"I",
		"I_reason",
		"evidence",
	],
	actors: [
		"id",
		"label",
		"category",
		"persona_count",
		"source_count",
		"band",
		"confidence",
		"identifier_count",
		"first_seen",
		"last_seen",
		"handles",
	],
	personas: [
		"id",
		"handle",
		"handle_normalized",
		"source_id",
		"source_name",
		"category",
		"post_count",
		"stylometry_refused",
		"stylometry_refused_reason",
		"char_count",
	],
} as const;

type Dataset = keyof typeof DATASETS;

const SCAN_STEPS = ["ingest", "resolve", "cluster", "recon", "correlate"] as const;

export default function ExportPage() {
	const [dataset, setDataset] = useState<Dataset>("links");
	const [format, setFormat] = useState("csv");
	const [selected, setSelected] = useState<string[]>([...DATASETS.links]);
	const [steps, setSteps] = useState<string[]>(["ingest", "resolve", "cluster"]);
	const [job, setJob] = useState<{ id: string; status: string; stage?: string | null } | null>(
		null,
	);
	const [scanError, setScanError] = useState<string | null>(null);
	const canScan = useAuthStore((state) => state.identity?.can_scan ?? false);

	function changeDataset(next: Dataset) {
		setDataset(next);
		setSelected([...DATASETS[next]]);
	}

	function toggleColumn(column: string) {
		setSelected((current) =>
			current.includes(column)
				? current.filter((c) => c !== column)
				: [...DATASETS[dataset]].filter((c) => current.includes(c) || c === column),
		);
	}

	const href = `/api/attribution/export/${format}?dataset=${dataset}${
		selected.length && selected.length !== DATASETS[dataset].length
			? `&columns=${selected.join(",")}`
			: ""
	}`;

	async function runScan() {
		setScanError(null);
		try {
			const res = await fetch("/api/attribution/scan", {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify({ steps }),
			});
			const body = await res.json();
			if (!res.ok) throw new Error(body.detail ?? "Scan failed to start");
			setJob({ id: body.job_id, status: body.status, stage: body.stage });
			poll(body.job_id);
		} catch (err) {
			setScanError(err instanceof Error ? err.message : "Scan failed to start");
		}
	}

	function poll(jobId: string) {
		const timer = setInterval(async () => {
			try {
				const res = await fetch(`/api/attribution/scan/${jobId}`);
				const body = await res.json();
				setJob({ id: jobId, status: body.status, stage: body.stage });
				if (body.status === "ok" || body.status === "failed") {
					clearInterval(timer);
					if (body.status === "failed") setScanError(body.error);
				}
			} catch {
				clearInterval(timer);
			}
		}, 2500);
	}

	return (
		<div className={styles.page}>
			<header className={styles.header}>
				<h1 className={styles.title}>
					<Download size={20} /> Export
				</h1>
				<p className={styles.subtitle}>
					CSV or JSON of the current result set. PDF case reports are Phase 5 and are not built —
					the API says so rather than returning an empty file.
				</p>
			</header>

			<TacticalPanel title="Build an export" subtitle={`${dataset} · ${format}`}>
				<div className={styles.controls}>
					<div className={styles.control}>
						<span>Dataset</span>
						<Select
							aria-label="Dataset"
							value={dataset}
							onChange={(e) => changeDataset(e.target.value as Dataset)}
							options={[
								{ value: "links", label: "Links (persona pairs + evidence)" },
								{ value: "actors", label: "Actors (resolved clusters)" },
								{ value: "personas", label: "Personas (raw observations)" },
							]}
						/>
					</div>
					<div className={styles.control}>
						<span>Format</span>
						<Select
							aria-label="Format"
							value={format}
							onChange={(e) => setFormat(e.target.value)}
							options={[
								{ value: "csv", label: "CSV" },
								{ value: "json", label: "JSON" },
							]}
						/>
					</div>
				</div>

				<h3 className={styles.sectionTitle}>Columns</h3>
				<div className={styles.columns}>
					{DATASETS[dataset].map((column) => (
						<label key={column} className={styles.column}>
							<input
								type="checkbox"
								checked={selected.includes(column)}
								onChange={() => toggleColumn(column)}
							/>
							<span
								className={
									column.endsWith("_reason") || column === "stylometry_refused_reason"
										? styles.reasonColumn
										: undefined
								}
							>
								{column}
							</span>
						</label>
					))}
				</div>

				{dataset === "links" && (
					<RefusalNotice
						title="Keep the _reason columns"
						reason="An unmeasured component is written as an empty cell. Its companion *_reason column carries the sentence explaining why it was not assessed — on this corpus, that is every I value."
						detail="Drop those columns and the export becomes a table of blanks that a reader will mistake for zeros."
					/>
				)}

				<div className={styles.actions}>
					<a href={href} download>
						<Button>
							<Download size={14} /> Download {format.toUpperCase()}
						</Button>
					</a>
					<span className={styles.count}>
						{selected.length} of {DATASETS[dataset].length} columns
					</span>
				</div>
			</TacticalPanel>

			{/* Running the pipeline rewrites the links table, so it is admin-only
			    on the API. Showing an analyst a button that will 403 teaches them
			    the restriction by refusing them; saying so does not. */}
			<TacticalPanel
				title="Run the pipeline"
				subtitle={
					canScan
						? "each step writes its own audit row"
						: "admin only — each step rewrites what every analyst reads"
				}
			>
				<div className={styles.steps}>
					{SCAN_STEPS.map((step) => (
						<Toggle
							key={step}
							checked={steps.includes(step)}
							onChange={(next) =>
								setSteps((current) =>
									next
										? [...SCAN_STEPS].filter((s) => current.includes(s) || s === step)
										: current.filter((s) => s !== step),
								)
							}
							label={step}
						/>
					))}
				</div>
				<div className={styles.actions}>
					<Button
						type="button"
						onClick={runScan}
						disabled={!canScan || steps.length === 0 || job?.status === "running"}
					>
						<Play size={14} /> Start scan
					</Button>
					{job && (
						<span className={styles.job}>
							job {job.id.slice(0, 8)} — <strong>{job.status}</strong>
							{job.stage ? ` (${job.stage})` : ""}
						</span>
					)}
				</div>
				{!canScan && (
					<p className={styles.note}>
						Your role is analyst. Exports above are yours to run; starting a pipeline run is an
						admin action because it changes what everyone else sees.
					</p>
				)}
				{scanError && (
					<p className={styles.error} role="alert">
						{scanError}
					</p>
				)}
			</TacticalPanel>
		</div>
	);
}
