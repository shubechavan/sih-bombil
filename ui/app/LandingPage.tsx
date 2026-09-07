"use client";

import { CategoryPie, ConfidenceRadar, RiskHistogram, SeverityTrend } from "@/components/charts";
import { RiskGauge } from "@/components/tactical";
import { Badge, Button } from "@/components/ui";
import { usePipelineState } from "@/hooks";
import { bff } from "@/lib/api";
import { getSeverityFromScore } from "@/lib/severity";
import type { ScanConfig } from "@/types/pipeline";
import type { Severity, Threat } from "@/types/threat";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./LandingPage.module.css";

type AppMode = "home" | "auto" | "manual";
type AutoStep = 0 | 1 | 2 | 3;

const MANUAL_PAGES = [
	"Dashboard",
	"Search & Scrape",
	"AI Analysis",
	"Analytics",
	"Threat Feed",
	"PII Protection",
	"Network Anomaly",
	"n8n Alerts",
	"Model Testing",
	"System Status",
] as const;

type ManualPage = (typeof MANUAL_PAGES)[number];

type AnalyticsView =
	| "Executive Summary"
	| "Network Anomaly"
	| "Recon Engine"
	| "AI Intelligence Matrix";

type FetchProfileLabel =
	| "Safe (default)"
	| "Balanced (faster)"
	| "High Throughput (multi-instance Tor)";

interface FetchProfileConfig {
	searchWorkers: number;
	scrapeWorkers: number;
	storeValue: ScanConfig["fetchProfile"];
}

const FETCH_PROFILES: Record<FetchProfileLabel, FetchProfileConfig> = {
	"Safe (default)": { searchWorkers: 8, scrapeWorkers: 5, storeValue: "stealth" },
	"Balanced (faster)": { searchWorkers: 12, scrapeWorkers: 8, storeValue: "balanced" },
	"High Throughput (multi-instance Tor)": {
		searchWorkers: 16,
		scrapeWorkers: 12,
		storeValue: "aggressive",
	},
};

const THREAT_QUERIES = [
	"drug marketplace vendor",
	"hacking tools exploit sale",
	"fraud carding fullz dumps",
	"counterfeit documents identity",
	"ransomware malware botnet",
];

const SEVERITY_ORDER: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

const SEVERITY_COLORS: Record<Severity, string> = {
	CRITICAL: "#ef4444",
	HIGH: "#f59e0b",
	MEDIUM: "#f97316",
	LOW: "#38bdf8",
};

const API_BASE_LABEL = process.env.NEXT_PUBLIC_API_URL ?? "FastAPI backend";

interface HealthPayload {
	status?: string;
	n8n_reachable?: boolean;
	postgres_connected?: boolean;
	postgres_rows?: number;
	notify_medium?: boolean;
	n8n_webhook?: string;
	auth_required?: boolean;
	services?: Partial<
		Record<
			"tor" | "postgres" | "n8n" | "fastapi" | "roberta" | "scheduler",
			{ status?: string; state?: string }
		>
	>;
	n8n_outbox?: {
		queue_depth?: number;
		delivered?: number;
		failed?: number;
	};
	session_stats?: {
		total_processed?: number;
		n8n_sent?: number;
		pg_stored?: number;
		failed?: number;
	};
	severity_routing?: Record<string, string>;
	cic_live_feed?: {
		last_modified_utc?: string;
		latest_prediction?: string;
		latest_t_score?: number;
		trigger_scrape?: boolean;
		detected_at?: string;
	};
}

interface AnalyzeResponse {
	category?: string;
	severity?: string;
	r_score?: number;
	c_score?: number;
	verdict?: string;
	action?: string;
	llm_reasoning?: string;
	roberta_category?: string;
	llm_category?: string;
	llm_mitre?: string | null;
	llm_mitre_tactic?: string | null;
	llm_mitre_tactic_id?: string | null;
	llm_mitre_technique_name?: string | null;
	analyzed_at?: string;
}

interface ExplainResponse {
	summary?: string;
	error?: string | null;
	model?: string;
	generated_at?: string;
}

interface AnalysisRow extends Threat {
	c_score?: number;
	verdict?: string;
	action?: string;
	llm_reasoning?: string;
	roberta_category?: string;
	llm_category?: string;
	analyzed_at?: string;
}

interface ExecData {
	totalThreats: number;
	criticalCount: number;
	averageRisk: number;
	topCategory: string;
	severityTrend: Array<{
		date: string;
		critical: number;
		high: number;
		medium: number;
		low: number;
	}>;
	riskDistribution: Array<{ bucket: string; count: number }>;
	categories: Array<{ name: string; value: number }>;
	recentCritical: Array<{ hash: string; title: string; severity: string; score: number }>;
}

interface NetworkData {
	totalAnomalies: number;
	criticalAnomalies: number;
	uniqueSources: number;
	activeAlerts: number;
	events: Array<{
		id: string;
		type: string;
		source_ip: string;
		destination: string;
		severity: string;
		timestamp: string;
		description: string;
	}>;
}

interface ReconData {
	totalQueries: number;
	avgMatchRate: number;
	avgConfidence: number;
	totalResults: number;
	results: Array<{
		query: string;
		totalResults: number;
		matchRate: number;
		avgConfidence: number;
		topSources: string[];
	}>;
}

interface IntelData {
	totalAnalyses: number;
	avgAccuracy: number;
	consensusRate: number;
	engines: Array<{
		engine: string;
		accuracy: number;
		avgConfidence: number;
		totalAnalyzed: number;
		falsePositiveRate: number;
	}>;
	radarData: Array<{ subject: string; value: number }>;
	recentDisagreements: Array<{
		hash: string;
		roberta: string;
		blink: string;
		consensus: string;
	}>;
}

interface ModelTestResult {
	sourceHash: string;
	input: string;
	expected: string;
	predicted: string;
	confidence: number;
	match: boolean;
	severity: Severity;
	risk: number;
}

interface PiiResult {
	original: string;
	scrubbed: string;
	redactions: Array<{ type: string; original: string; replacement: string }>;
}

interface NetworkSimulation {
	prediction: string;
	tScore: number;
	triggerScrape: boolean;
	probabilities: Array<{ label: string; p: number }>;
	detectedAt?: string;
	dataSource?: string;
	fallbackUsed?: boolean;
	error?: string;
}

type ServiceState = "online" | "offline" | "degraded" | "checking";

function normalizeServiceState(value: unknown): ServiceState {
	const v = String(value ?? "checking").toLowerCase();
	if (v === "online" || v === "offline" || v === "degraded" || v === "checking") {
		return v;
	}
	return "checking";
}

function serviceHealthy(state: ServiceState): boolean {
	return state === "online";
}

function toNumber(value: unknown, fallback = 0): number {
	const n = Number(value);
	return Number.isFinite(n) ? n : fallback;
}

function clamp01(value: number): number {
	if (value < 0) return 0;
	if (value > 1) return 1;
	return value;
}

function toSeverity(value: unknown, fallback: Severity = "LOW"): Severity {
	const v = String(value ?? "").toUpperCase();
	if (v === "CRITICAL" || v === "HIGH" || v === "MEDIUM" || v === "LOW") {
		return v;
	}
	return fallback;
}

function riskScore(row: Partial<Threat>): number {
	const score = toNumber(row.riskScore ?? row.r_score, 0);
	return clamp01(score);
}

function rowSeverity(row: Partial<Threat>): Severity {
	return toSeverity(row.severity, getSeverityFromScore(riskScore(row)));
}

function rowHash(row: Partial<Threat>, fallback: string): string {
	return String(row.rowHash ?? row.source_hash ?? fallback);
}

function rowTitle(row: Partial<Threat>): string {
	const t = String(row.title ?? row.textSnippet ?? row.clean_text ?? "Untitled").trim();
	return t || "Untitled";
}

function rowText(row: Partial<Threat>): string {
	const txt = String(row.clean_text ?? row.textSnippet ?? row.title ?? "").trim();
	return txt || rowTitle(row);
}

function rowEngine(row: Partial<Threat>): string {
	return String(row.engine ?? row.searchEngine ?? "Unknown");
}

function rowCategory(row: Partial<Threat>): string {
	return String(row.category ?? "unknown");
}

function rowTimestamp(row: Partial<Threat>): string {
	return String(row.timestamp ?? row.created_at ?? new Date().toISOString());
}

function isObfuscated(row: Partial<Threat>): boolean {
	const legacy = row as Partial<Threat> & { obfuscation_detected?: boolean };
	return Boolean(row.obfuscationDetected ?? legacy.obfuscation_detected);
}

function guardStatus(row: Partial<Threat>): "safe" | "blocked" | "unknown" {
	const status = String(row.guard_status ?? "").toLowerCase();
	if (status === "safe" || status === "blocked") return status;
	return "unknown";
}

function isGuardBlocked(row: Partial<Threat>): boolean {
	return guardStatus(row) === "blocked";
}

function confidenceValue(row: AnalysisRow): number {
	const c = toNumber(row.c_score ?? row.robertaConfidence, 0.5);
	return clamp01(c);
}

function normalizeThreatRows(items: Threat[]): Threat[] {
	return items.map((item, index) => {
		const normalizedRisk = riskScore(item);
		const normalizedSeverity = toSeverity(item.severity, getSeverityFromScore(normalizedRisk));

		return {
			...item,
			rowHash: rowHash(item, `row-${index}`),
			source_hash: String(item.source_hash ?? item.rowHash ?? `row-${index}`),
			title: rowTitle(item),
			textSnippet: rowText(item),
			clean_text: String(item.clean_text ?? rowText(item)),
			engine: rowEngine(item),
			searchEngine: String(item.searchEngine ?? rowEngine(item)),
			category: rowCategory(item),
			timestamp: rowTimestamp(item),
			created_at: String(item.created_at ?? rowTimestamp(item)),
			severity: normalizedSeverity,
			riskScore: normalizedRisk,
			r_score: normalizedRisk,
		};
	});
}

function csvEscape(value: unknown): string {
	const raw = String(value ?? "");
	if (raw.includes(",") || raw.includes('"') || raw.includes("\n")) {
		return `"${raw.replace(/\"/g, '""')}"`;
	}
	return raw;
}

function exportRowsCsv(rows: Array<Partial<Threat>>): string {
	const headers = [
		"rowHash",
		"riskScore",
		"severity",
		"category",
		"engine",
		"query",
		"title",
		"timestamp",
	];

	const lines = rows.map((row, index) => {
		const values = [
			rowHash(row, `row-${index}`),
			riskScore(row).toFixed(4),
			rowSeverity(row),
			rowCategory(row),
			rowEngine(row),
			String(row.query ?? ""),
			rowTitle(row),
			rowTimestamp(row),
		];
		return values.map(csvEscape).join(",");
	});

	return [headers.join(","), ...lines].join("\n");
}

function downloadTextFile(filename: string, text: string, mime = "text/plain") {
	const blob = new Blob([text], { type: mime });
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = filename;
	a.click();
	URL.revokeObjectURL(url);
}

function parseCsvLine(line: string): string[] {
	const out: string[] = [];
	let current = "";
	let inQuotes = false;

	for (let i = 0; i < line.length; i += 1) {
		const ch = line[i];
		if (ch === '"') {
			if (inQuotes && line[i + 1] === '"') {
				current += '"';
				i += 1;
			} else {
				inQuotes = !inQuotes;
			}
		} else if (ch === "," && !inQuotes) {
			out.push(current);
			current = "";
		} else {
			current += ch;
		}
	}

	out.push(current);
	return out;
}

function parseThreatCsv(csv: string): Threat[] {
	const lines = csv.split(/\r?\n/).filter((line) => line.trim().length > 0);
	if (lines.length < 2) return [];

	const headers = parseCsvLine(lines[0]).map((h) => h.trim());

	return lines.slice(1).map((line, index) => {
		const cols = parseCsvLine(line);
		const row = Object.fromEntries(headers.map((h, i) => [h, cols[i] ?? ""])) as Record<
			string,
			string
		>;

		const base: Threat = {
			rowHash: String(row.rowHash || row.source_hash || `csv-${index}`),
			source_hash: String(row.source_hash || row.rowHash || `csv-${index}`),
			riskScore: clamp01(toNumber(row.riskScore || row.r_score, 0)),
			r_score: clamp01(toNumber(row.riskScore || row.r_score, 0)),
			severity: toSeverity(
				row.severity,
				getSeverityFromScore(clamp01(toNumber(row.riskScore || row.r_score, 0))),
			),
			category: String(row.category || "unknown"),
			searchEngine: String(row.searchEngine || row.engine || "Unknown"),
			engine: String(row.engine || row.searchEngine || "Unknown"),
			query: String(row.query || ""),
			title: String(row.title || row.textSnippet || row.clean_text || "Untitled"),
			clean_text: String(row.clean_text || row.textSnippet || row.title || ""),
			textSnippet: String(row.textSnippet || row.clean_text || row.title || ""),
			timestamp: String(row.timestamp || row.created_at || new Date().toISOString()),
			created_at: String(row.created_at || row.timestamp || new Date().toISOString()),
		};

		return base;
	});
}

function severityCount(rows: Array<Partial<Threat>>, sev: Severity): number {
	return rows.filter((r) => rowSeverity(r) === sev).length;
}

function SeveritySpectrum() {
	return (
		<div className={styles.severityGrid}>
			<div className={`${styles.severityCard} ${styles.low}`}>
				<div className={styles.sevRange}>R &lt; 0.3</div>
				<div className={styles.sevLabel}>LOW</div>
				<div className={styles.sevAction}>Stored in PostgreSQL only · no alert sent</div>
			</div>
			<div className={`${styles.severityCard} ${styles.medium}`}>
				<div className={styles.sevRange}>0.3 to 0.6</div>
				<div className={styles.sevLabel}>MEDIUM</div>
				<div className={styles.sevAction}>
					Written to analyst_queue table · manual analyst review
				</div>
			</div>
			<div className={`${styles.severityCard} ${styles.high}`}>
				<div className={styles.sevRange}>0.6 to 0.8</div>
				<div className={styles.sevLabel}>HIGH</div>
				<div className={styles.sevAction}>Slack notification via n8n · urgent analyst action</div>
			</div>
			<div className={`${styles.severityCard} ${styles.critical}`}>
				<div className={styles.sevRange}>R &gt; 0.8</div>
				<div className={styles.sevLabel}>CRITICAL</div>
				<div className={styles.sevAction}>Email + Slack auto-fired via n8n</div>
			</div>
		</div>
	);
}

function ConfidenceBar({ value }: { value: number }) {
	const safe = clamp01(value);
	return (
		<div className={styles.confidenceWrap}>
			<div className={styles.confidenceTrack}>
				<div className={styles.confidenceFill} style={{ width: `${(safe * 100).toFixed(1)}%` }} />
			</div>
			<div className={styles.confidenceText}>{(safe * 100).toFixed(1)}%</div>
		</div>
	);
}

function StatusRow({ name, ok, message }: { name: string; ok: boolean; message: string }) {
	return (
		<div className={styles.statusRow}>
			<span className={`${styles.statusDot} ${ok ? styles.dotOk : styles.dotWarn}`} />
			<span className={styles.statusName}>{name}</span>
			<span className={styles.statusDesc}>{message}</span>
		</div>
	);
}

export function LandingPage() {
	const pipeline = usePipelineState();
	const { startScan, stages, isRunning, scanId, error, results: pipelineResults, reset } = pipeline;

	const [mode, setMode] = useState<AppMode>("home");
	const [autoStep, setAutoStep] = useState<AutoStep>(0);
	const [manualPage, setManualPage] = useState<ManualPage>("Dashboard");

	const [autoMaxResults, setAutoMaxResults] = useState<number>(20);
	const [autoProfile, setAutoProfile] = useState<FetchProfileLabel>("Safe (default)");

	const [manualQueryMode, setManualQueryMode] = useState<"All 5 Threat Queries" | "Custom Query">(
		"All 5 Threat Queries",
	);
	const [manualCustomQuery, setManualCustomQuery] = useState("");
	const [manualMaxResults, setManualMaxResults] = useState<number>(20);

	const [manualProfile, setManualProfile] = useState<FetchProfileLabel>("Safe (default)");

	const [scanRows, setScanRows] = useState<Threat[]>([]);
	const [analysisRows, setAnalysisRows] = useState<AnalysisRow[]>([]);
	const [analysisTimeline, setAnalysisTimeline] = useState<string[]>([]);
	const [aiRunning, setAiRunning] = useState(false);
	const [manualAnalysis, setManualAnalysis] = useState<AnalysisRow | null>(null);
	const [manualSelectedIndex, setManualSelectedIndex] = useState(0);

	const [health, setHealth] = useState<HealthPayload | null>(null);
	const [healthLoading, setHealthLoading] = useState(false);

	const [n8nAlertsSent, setN8nAlertsSent] = useState(0);
	const [pgRowsSaved, setPgRowsSaved] = useState(0);

	const [analyticsView, setAnalyticsView] = useState<AnalyticsView>("Executive Summary");
	const [execData, setExecData] = useState<ExecData | null>(null);
	const [reconData, setReconData] = useState<ReconData | null>(null);
	const [intelData, setIntelData] = useState<IntelData | null>(null);
	const [networkData, setNetworkData] = useState<NetworkData | null>(null);

	const [feedCategory, setFeedCategory] = useState("All");
	const [feedSeverity, setFeedSeverity] = useState<"All" | Severity>("All");
	const [feedEngine, setFeedEngine] = useState("All");
	const [feedThreatsOnly, setFeedThreatsOnly] = useState(false);
	const [feedObfuscatedOnly, setFeedObfuscatedOnly] = useState(false);

	const [piiExample, setPiiExample] = useState("Custom input");
	const [piiInput, setPiiInput] = useState("");
	const [piiResult, setPiiResult] = useState<PiiResult | null>(null);
	const [piiLoading, setPiiLoading] = useState(false);
	const [piiError, setPiiError] = useState<string | null>(null);

	const [networkSimulation, setNetworkSimulation] = useState<NetworkSimulation | null>(null);
	const [networkSimulationLoading, setNetworkSimulationLoading] = useState(false);
	const [networkError, setNetworkError] = useState<string | null>(null);

	const [testMode, setTestMode] = useState<"RoBERTa only" | "RoBERTa + Blink.new AI Gateway">(
		"RoBERTa only",
	);
	const [modelTesting, setModelTesting] = useState(false);
	const [modelResults, setModelResults] = useState<ModelTestResult[]>([]);
	const [modelTestNotice, setModelTestNotice] = useState<string | null>(null);
	const [customTestInput, setCustomTestInput] = useState("");
	const [customTestResult, setCustomTestResult] = useState<AnalysisRow | null>(null);
	const [manualExplain, setManualExplain] = useState<ExplainResponse | null>(null);
	const [manualExplainLoading, setManualExplainLoading] = useState(false);
	const [n8nActionNotice, setN8nActionNotice] = useState<string | null>(null);

	const autoScanStarted = useRef(false);
	const manualScanStarted = useRef(false);
	const autoAnalysisStarted = useRef(false);

	const activeRows = useMemo(() => {
		return analysisRows.length > 0 ? analysisRows : scanRows;
	}, [analysisRows, scanRows]);

	const criticalCount = useMemo(() => severityCount(activeRows, "CRITICAL"), [activeRows]);

	const totalRows = activeRows.length;
	const highRows = severityCount(activeRows, "HIGH");
	const mediumRows = severityCount(activeRows, "MEDIUM");
	const lowRows = severityCount(activeRows, "LOW");

	const avgRisk = useMemo(() => {
		if (activeRows.length === 0) return 0;
		return clamp01(activeRows.reduce((sum, row) => sum + riskScore(row), 0) / activeRows.length);
	}, [activeRows]);

	const categoryPieData = useMemo(() => {
		const map = new Map<string, number>();
		for (const row of activeRows) {
			const cat = rowCategory(row);
			map.set(cat, (map.get(cat) ?? 0) + 1);
		}
		return Array.from(map.entries()).map(([name, value]) => ({ name, value }));
	}, [activeRows]);

	const riskHistogramData = useMemo(() => {
		const buckets = [
			{ range: "0-0.2", min: 0, max: 0.2, color: "#22c55e" },
			{ range: "0.2-0.4", min: 0.2, max: 0.4, color: "#34d399" },
			{ range: "0.4-0.6", min: 0.4, max: 0.6, color: "#f97316" },
			{ range: "0.6-0.8", min: 0.6, max: 0.8, color: "#f59e0b" },
			{ range: "0.8-1.0", min: 0.8, max: 1.01, color: "#ef4444" },
		];

		return buckets.map((b) => ({
			range: b.range,
			count: activeRows.filter((row) => {
				const r = riskScore(row);
				return r >= b.min && r < b.max;
			}).length,
			color: b.color,
		}));
	}, [activeRows]);

	const severityTrendData = useMemo(() => {
		const days = 7;
		const now = new Date();
		const output: Array<{
			date: string;
			critical: number;
			high: number;
			medium: number;
			low: number;
		}> = [];

		for (let i = days - 1; i >= 0; i -= 1) {
			const d = new Date(now);
			d.setDate(now.getDate() - i);
			const key = d.toISOString().slice(5, 10);
			output.push({ date: key, critical: 0, high: 0, medium: 0, low: 0 });
		}

		for (const row of activeRows) {
			const ts = rowTimestamp(row);
			const key = ts.slice(5, 10);
			const found = output.find((x) => x.date === key);
			if (!found) continue;
			const sev = rowSeverity(row);
			if (sev === "CRITICAL") found.critical += 1;
			else if (sev === "HIGH") found.high += 1;
			else if (sev === "MEDIUM") found.medium += 1;
			else found.low += 1;
		}

		return output;
	}, [activeRows]);

	const filteredThreatFeed = useMemo(() => {
		let rows = [...activeRows];

		if (feedCategory !== "All") {
			rows = rows.filter((row) => rowCategory(row) === feedCategory);
		}
		if (feedSeverity !== "All") {
			rows = rows.filter((row) => rowSeverity(row) === feedSeverity);
		}
		if (feedEngine !== "All") {
			rows = rows.filter((row) => rowEngine(row) === feedEngine);
		}
		if (feedThreatsOnly) {
			rows = rows.filter((row) => riskScore(row) >= 0.6);
		}
		if (feedObfuscatedOnly) {
			rows = rows.filter((row) => isObfuscated(row));
		}

		return rows;
	}, [activeRows, feedCategory, feedSeverity, feedEngine, feedThreatsOnly, feedObfuscatedOnly]);

	const threatFeedCategories = useMemo(() => {
		const all = new Set<string>(["All"]);
		for (const row of activeRows) all.add(rowCategory(row));
		return Array.from(all);
	}, [activeRows]);

	const threatFeedEngines = useMemo(() => {
		const all = new Set<string>(["All"]);
		for (const row of activeRows) all.add(rowEngine(row));
		return Array.from(all);
	}, [activeRows]);

	const refreshHealth = useCallback(async () => {
		setHealthLoading(true);
		try {
			const payload = await bff.get("health").json<HealthPayload>();
			setHealth(payload);
		} catch {
			setHealth(null);
		} finally {
			setHealthLoading(false);
		}
	}, []);

	const refreshAnalyticsData = useCallback(async () => {
		try {
			const [exec, recon, intel] = await Promise.all([
				bff.get("analytics/executive").json<ExecData>(),
				bff.get("analytics/recon").json<ReconData>(),
				bff.get("analytics/intelligence").json<IntelData>(),
			]);
			setExecData(exec);
			setReconData(recon);
			setIntelData(intel);
		} catch {
			// Preserve last successful analytics snapshot on fetch failures.
		}
	}, []);

	const refreshNetworkData = useCallback(async () => {
		try {
			const payload = await bff.get("network").json<NetworkData>();
			setNetworkData(payload);
		} catch {
			setNetworkData(null);
		}
	}, []);

	useEffect(() => {
		void refreshHealth();
		const timer = window.setInterval(() => {
			void refreshHealth();
		}, 30000);
		return () => window.clearInterval(timer);
	}, [refreshHealth]);

	useEffect(() => {
		if (mode === "manual" && (manualPage === "Analytics" || manualPage === "Network Anomaly")) {
			void refreshNetworkData();
		}
		if (mode === "manual" && manualPage === "Analytics") {
			void refreshAnalyticsData();
		}
	}, [mode, manualPage, refreshAnalyticsData, refreshNetworkData]);

	useEffect(() => {
		if (isRunning) return;
		if (!pipelineResults || pipelineResults.length === 0) return;

		const normalized = normalizeThreatRows(pipelineResults);
		setScanRows(normalized);

		if (autoScanStarted.current && mode === "auto" && autoStep === 1) {
			setAutoStep(2);
			autoScanStarted.current = false;
			autoAnalysisStarted.current = false;
		}

		if (manualScanStarted.current) {
			manualScanStarted.current = false;
		}
	}, [isRunning, pipelineResults, mode, autoStep]);

	const buildScanConfig = useCallback(
		(queries: string[], maxPerQuery: number, profile: FetchProfileLabel): ScanConfig => {
			const cleanQueries = queries.map((q) => q.trim()).filter(Boolean);
			const totalMax = Math.max(5, cleanQueries.length * Math.max(1, maxPerQuery));
			return {
				queries: cleanQueries,
				maxResults: totalMax,
				fetchProfile: FETCH_PROFILES[profile].storeValue,
				enableObfuscation: true,
				enablePiiScrub: true,
			};
		},
		[],
	);

	const toAnalysisRow = useCallback(
		(row: Threat, resp: AnalyzeResponse, index: number): AnalysisRow => {
			const fallbackRisk = riskScore(row);
			const computedRisk = clamp01(toNumber(resp.r_score, fallbackRisk));
			const computedSeverity = toSeverity(resp.severity, getSeverityFromScore(computedRisk));

			return {
				...row,
				rowHash: rowHash(row, `a-${index}`),
				source_hash: String(row.source_hash ?? row.rowHash ?? `a-${index}`),
				riskScore: computedRisk,
				r_score: computedRisk,
				severity: computedSeverity,
				category: String(resp.category ?? rowCategory(row)),
				verdict: String(resp.verdict ?? "HUMAN_REVIEW"),
				action: String(resp.action ?? "analyst_queue"),
				c_score: clamp01(toNumber(resp.c_score, row.robertaConfidence ?? 0.5)),
				llm_reasoning: String(resp.llm_reasoning ?? ""),
				roberta_category: String(resp.roberta_category ?? row.robertaCategory ?? ""),
				llm_category: String(resp.llm_category ?? row.blinkCategory ?? ""),
				llm_mitre: resp.llm_mitre ?? null,
				llm_mitre_tactic: resp.llm_mitre_tactic ?? null,
				llm_mitre_tactic_id: resp.llm_mitre_tactic_id ?? null,
				llm_mitre_technique_name: resp.llm_mitre_technique_name ?? null,
				analyzed_at: String(resp.analyzed_at ?? new Date().toISOString()),
				engine: rowEngine(row),
				searchEngine: String(row.searchEngine ?? rowEngine(row)),
				title: rowTitle(row),
				clean_text: rowText(row),
				timestamp: rowTimestamp(row),
				created_at: String(row.created_at ?? rowTimestamp(row)),
			};
		},
		[],
	);

	const analyzeOneRow = useCallback(
		async (row: Threat, index: number): Promise<AnalysisRow> => {
			const payload = {
				text: rowText(row),
				clean_text: rowText(row),
				source_hash: rowHash(row, `h-${index}`),
				t_score: riskScore(row),
				h_score: 0.5,
			};

			const resp = await bff.post("analyze", { json: payload }).json<AnalyzeResponse>();
			return toAnalysisRow(row, resp, index);
		},
		[toAnalysisRow],
	);

	const fireAlertForRow = useCallback(async (row: AnalysisRow): Promise<boolean> => {
		const sev = rowSeverity(row);
		if (!(sev === "CRITICAL" || sev === "HIGH")) return false;

		const payload = {
			severity: sev,
			category: rowCategory(row),
			r_score: riskScore(row),
			title: rowTitle(row),
			engine: rowEngine(row),
			query: String(row.query ?? ""),
			source_hash: rowHash(row, "manual"),
			llm_mitre: String(row.llm_mitre ?? ""),
			llm_mitre_tactic: String(row.llm_mitre_tactic ?? ""),
			llm_mitre_tactic_id: String(row.llm_mitre_tactic_id ?? ""),
			llm_mitre_technique_name: String(row.llm_mitre_technique_name ?? ""),
			reasoning: String(row.llm_reasoning ?? ""),
			action: String(row.action ?? "auto_alert"),
			timestamp: new Date().toISOString(),
		};

		try {
			const resp = await bff.post("alerts/send", { json: payload }).json<{ n8n_sent?: boolean }>();
			return Boolean(resp.n8n_sent);
		} catch {
			return false;
		}
	}, []);

	const runAutoAnalysis = useCallback(async () => {
		if (scanRows.length === 0) return;

		setAiRunning(true);
		setAnalysisTimeline([]);

		const batch = scanRows.slice(0, 30);
		const output: AnalysisRow[] = [];
		const timelineLines: string[] = [];

		for (let i = 0; i < batch.length; i += 1) {
			const row = batch[i];
			try {
				const analyzed = await analyzeOneRow(row, i);
				output.push(analyzed);

				timelineLines.push(
					`${rowSeverity(analyzed)} | ${rowCategory(analyzed)} | ${rowTitle(analyzed).slice(0, 52)}`,
				);
			} catch {
				timelineLines.push(`ERROR | analysis failed | ${rowTitle(row).slice(0, 52)}`);
			}
			setAnalysisTimeline([...timelineLines]);
		}

		let sent = 0;
		for (const row of output) {
			const fired = await fireAlertForRow(row);
			if (fired) sent += 1;
		}

		setAnalysisRows(output);
		setPgRowsSaved(output.length);
		setN8nAlertsSent(sent);
		setAiRunning(false);
		setAutoStep(3);
	}, [analyzeOneRow, fireAlertForRow, scanRows]);

	useEffect(() => {
		if (mode !== "auto" || autoStep !== 2) return;
		if (aiRunning) return;
		if (scanRows.length === 0) return;
		if (autoAnalysisStarted.current) return;

		autoAnalysisStarted.current = true;
		void runAutoAnalysis();
	}, [mode, autoStep, aiRunning, scanRows.length, runAutoAnalysis]);

	const launchAutoMode = useCallback(() => {
		setMode("auto");
		setAutoStep(0);
	}, []);

	const launchManualMode = useCallback(() => {
		setMode("manual");
		setManualPage("Dashboard");
	}, []);

	const goHome = useCallback(() => {
		setMode("home");
	}, []);

	const startAutoScan = useCallback(async () => {
		reset();
		setScanRows([]);
		setAnalysisRows([]);
		setAnalysisTimeline([]);
		setN8nAlertsSent(0);
		setPgRowsSaved(0);
		setManualAnalysis(null);
		setManualExplain(null);

		autoAnalysisStarted.current = false;
		autoScanStarted.current = true;

		setAutoStep(1);
		const config = buildScanConfig(THREAT_QUERIES, autoMaxResults, autoProfile);
		await startScan(config);
	}, [reset, buildScanConfig, autoMaxResults, autoProfile, startScan]);

	const startManualScan = useCallback(async () => {
		const queries = manualQueryMode === "Custom Query" ? [manualCustomQuery] : THREAT_QUERIES;

		const cleanQueries = queries.map((q) => q.trim()).filter(Boolean);
		if (cleanQueries.length === 0) return;

		reset();
		setScanRows([]);
		setAnalysisRows([]);
		setManualAnalysis(null);
		setManualExplain(null);
		setN8nAlertsSent(0);
		setPgRowsSaved(0);

		manualScanStarted.current = true;

		const config = buildScanConfig(cleanQueries, manualMaxResults, manualProfile);
		await startScan(config);
	}, [
		manualQueryMode,
		manualCustomQuery,
		manualMaxResults,
		manualProfile,
		reset,
		buildScanConfig,
		startScan,
	]);

	const runManualAnalysis = useCallback(async () => {
		if (scanRows.length === 0) return;
		const idx = Math.max(0, Math.min(scanRows.length - 1, manualSelectedIndex));
		const row = scanRows[idx];
		let analyzed: AnalysisRow;
		try {
			analyzed = await analyzeOneRow(row, idx);
		} catch {
			setManualAnalysis(null);
			setManualExplain({
				summary: "",
				error: "analysis endpoint unavailable",
				model: "backend",
				generated_at: new Date().toISOString(),
			});
			return;
		}
		setManualAnalysis(analyzed);
		setManualExplain(null);
		setManualExplainLoading(true);

		try {
			const explain = await bff
				.post("analyze/explain", {
					json: {
						text: rowText(analyzed),
						title: rowTitle(analyzed),
						roberta_category: String(analyzed.roberta_category ?? ""),
						llm_category: String(analyzed.llm_category ?? ""),
						severity: rowSeverity(analyzed),
						r_score: riskScore(analyzed),
						verdict: String(analyzed.verdict ?? "HUMAN_REVIEW"),
						action: String(analyzed.action ?? "analyst_queue"),
					},
				})
				.json<ExplainResponse>();
			setManualExplain(explain);
		} catch {
			setManualExplain({
				summary: "",
				error: "explain endpoint unavailable",
				model: "backend",
				generated_at: new Date().toISOString(),
			});
		} finally {
			setManualExplainLoading(false);
		}
	}, [scanRows, manualSelectedIndex, analyzeOneRow]);

	const runAllModelTests = useCallback(async () => {
		setModelTestNotice(null);
		const corpus = activeRows.filter((row) => rowText(row).trim().length >= 4).slice(0, 25);
		if (corpus.length === 0) {
			setModelResults([]);
			setModelTestNotice("No real rows available. Run Search & Scrape first.");
			return;
		}

		setModelTesting(true);
		setModelTestNotice(`Running live evaluation on ${corpus.length} row(s).`);
		const out: ModelTestResult[] = [];

		for (let i = 0; i < corpus.length; i += 1) {
			const sample = corpus[i];
			const expected = rowCategory(sample).toLowerCase();

			let analyzed: AnalysisRow;
			try {
				analyzed = await analyzeOneRow(sample, i);
			} catch {
				out.push({
					sourceHash: rowHash(sample, `test-${i}`),
					input: rowText(sample),
					expected,
					predicted: "error",
					confidence: 0,
					match: false,
					severity: "LOW",
					risk: 0,
				});
				continue;
			}
			const predicted = String(analyzed.category ?? "unknown").toLowerCase();
			const match =
				expected.length > 0 && expected !== "unknown"
					? predicted.includes(expected) || expected.includes(predicted)
					: false;

			out.push({
				sourceHash: rowHash(sample, `test-${i}`),
				input: rowText(sample),
				expected,
				predicted,
				confidence: confidenceValue(analyzed),
				match,
				severity: rowSeverity(analyzed),
				risk: riskScore(analyzed),
			});
		}

		setModelResults(out);
		const matched = out.filter((r) => r.match).length;
		setModelTestNotice(`Live evaluation complete: ${matched}/${out.length} category matches.`);
		setModelTesting(false);
	}, [activeRows, analyzeOneRow]);

	const classifyCustomModelInput = useCallback(async () => {
		const text = customTestInput.trim();
		if (!text) return;

		const row: Threat = {
			rowHash: `custom-${Date.now()}`,
			source_hash: `custom-${Date.now()}`,
			riskScore: 0.5,
			r_score: 0.5,
			textSnippet: text,
			clean_text: text,
			searchEngine: "custom",
			engine: "custom",
			timestamp: new Date().toISOString(),
			category: "unknown",
			severity: "MEDIUM",
			title: text.slice(0, 120),
		};

		try {
			const analyzed = await analyzeOneRow(row, 0);
			setCustomTestResult(analyzed);
		} catch {
			setCustomTestResult(null);
		}
	}, [customTestInput, analyzeOneRow]);

	const scrubPii = useCallback(async () => {
		const text = piiInput.trim();
		if (!text) return;

		setPiiLoading(true);
		setPiiError(null);
		try {
			const payload = await bff.post("pii/scrub", { json: { text } }).json<PiiResult>();
			setPiiResult(payload);
		} catch (err) {
			setPiiResult(null);
			setPiiError(err instanceof Error ? err.message : "PII scrub endpoint failed");
		} finally {
			setPiiLoading(false);
		}
	}, [piiInput]);

	const sendCriticalTestAlert = useCallback(async () => {
		setN8nActionNotice(null);
		if (!healthApi) {
			setN8nActionNotice("FastAPI bridge is offline. Start alert_api backend first.");
			return;
		}
		try {
			const payload = await bff
				.post("alerts/test")
				.json<{ n8n_sent?: boolean; n8n_queued?: boolean; errors?: string[] }>();
			setN8nAlertsSent((prev) => prev + 1);
			if (payload.n8n_sent) {
				setN8nActionNotice("CRITICAL test alert sent to n8n successfully.");
			} else if (payload.n8n_queued) {
				setN8nActionNotice(
					"CRITICAL test alert queued in backend outbox. n8n appears unreachable right now.",
				);
			} else {
				const reason = Array.isArray(payload.errors) && payload.errors.length > 0 ? payload.errors[0] : "unknown";
				setN8nActionNotice(`Test alert failed routing: ${reason}`);
			}
			void refreshHealth();
		} catch (err) {
			setN8nActionNotice(err instanceof Error ? err.message : "Failed to send test alert");
		}
	}, [refreshHealth, health, healthLoading]);

	const fireAlertsForCurrentRows = useCallback(async () => {
		setN8nActionNotice(null);
		if (activeRows.length === 0) {
			setN8nActionNotice("No rows available to route.");
			return;
		}
		if (!healthApi) {
			setN8nActionNotice("FastAPI bridge is offline. Start alert_api backend first.");
			return;
		}

		let sent = 0;
		let attempted = 0;
		for (const row of activeRows) {
			const fire = rowSeverity(row) === "CRITICAL" || rowSeverity(row) === "HIGH";
			if (!fire) continue;
			attempted += 1;
			const ok = await fireAlertForRow(row as AnalysisRow);
			if (ok) sent += 1;
		}

		setN8nAlertsSent((prev) => prev + sent);
		if (attempted === 0) {
			setN8nActionNotice("No CRITICAL/HIGH rows found in current dataset.");
		} else if (sent === attempted) {
			setN8nActionNotice(`All ${sent}/${attempted} alerts routed successfully.`);
		} else {
			setN8nActionNotice(
				`Partial delivery: ${sent}/${attempted} alerts routed. Check n8n and backend outbox.`,
			);
		}
		void refreshHealth();
	}, [activeRows, fireAlertForRow, refreshHealth, health, healthLoading]);

	const uploadCsv = useCallback(async (file: File) => {
		const text = await file.text();
		const parsed = parseThreatCsv(text);
		setScanRows(normalizeThreatRows(parsed));
		setAnalysisRows([]);
		setManualAnalysis(null);
		setManualExplain(null);
	}, []);

	const runNetworkSimulation = useCallback(
		async (modeType: "normal" | "tor") => {
			setNetworkError(null);
			if (!healthApi) {
				setNetworkError("FastAPI bridge is offline. Start alert_api backend first.");
				return;
			}
			setNetworkSimulationLoading(true);
			try {
				const payload = await bff.post("network", { json: { mode: modeType } }).json<{
					prediction?: string;
					t_score?: number;
					trigger_scrape?: boolean;
					all_probabilities?: Record<string, number>;
					detected_at?: string;
					data_source?: string;
					fallback_used?: boolean;
					error?: string;
				}>();

				const rawT = Number(payload.t_score);
				if (!Number.isFinite(rawT)) {
					throw new Error("Network endpoint returned no T-score.");
				}

				const probs = payload.all_probabilities ?? {};
				const probabilities = Object.entries(probs).map(([label, value]) => ({
					label,
					p: clamp01(toNumber(value, 0)),
				}));

				const tScore = clamp01(rawT);
				const simulation: NetworkSimulation = {
					prediction: String(payload.prediction ?? "unknown"),
					tScore,
					triggerScrape: Boolean(payload.trigger_scrape),
					probabilities,
					detectedAt: payload.detected_at ? String(payload.detected_at) : undefined,
					dataSource: payload.data_source,
					fallbackUsed: Boolean(payload.fallback_used),
					error: payload.error,
				};

				setNetworkSimulation(simulation);
				if (simulation.fallbackUsed) {
					setNetworkError(
						"Backend reported fallback mode for network model. Validate darknet dependencies and CIC model files.",
					);
				}
				void refreshNetworkData();
			} catch (err) {
				setNetworkSimulation(null);
				setNetworkError(err instanceof Error ? err.message : "Network endpoint failed");
			} finally {
				setNetworkSimulationLoading(false);
			}
		},
		[refreshNetworkData, health, healthLoading],
	);

	const torState = normalizeServiceState(
		health?.services?.tor?.status ??
			health?.services?.tor?.state ??
			(health?.cic_live_feed?.latest_prediction ? "online" : "checking"),
	);
	const postgresState = normalizeServiceState(
		health?.services?.postgres?.status ??
			health?.services?.postgres?.state ??
			(health?.postgres_connected ? "online" : "offline"),
	);
	const n8nState = normalizeServiceState(
		health?.services?.n8n?.status ??
			health?.services?.n8n?.state ??
			(health?.n8n_reachable ? "online" : "offline"),
	);
	const fastapiState = normalizeServiceState(
		health?.services?.fastapi?.status ??
			health?.services?.fastapi?.state ??
			(health ? "online" : "offline"),
	);
	const robertaState = normalizeServiceState(
		health?.services?.roberta?.status ?? health?.services?.roberta?.state ?? "degraded",
	);
	const schedulerState = normalizeServiceState(
		health?.services?.scheduler?.status ?? health?.services?.scheduler?.state ?? "checking",
	);

	const healthN8n = serviceHealthy(n8nState);
	const healthPg = serviceHealthy(postgresState);
	const healthApi = serviceHealthy(fastapiState) || Boolean(health && !healthLoading);
	const healthTor = torState !== "offline";
	const healthRoberta = robertaState === "online";
	const healthScheduler = schedulerState !== "offline";
	const healthPgRows = toNumber(health?.postgres_rows, pgRowsSaved);
	const cicPrediction = String(health?.cic_live_feed?.latest_prediction ?? "unknown");
	const cicLastModified = String(health?.cic_live_feed?.last_modified_utc ?? "");

	const manualQueries =
		manualQueryMode === "Custom Query"
			? [manualCustomQuery].filter((q) => q.trim().length > 0)
			: THREAT_QUERIES;

	const analyticsRadar = useMemo(() => {
		if (!intelData?.radarData) return [];
		return intelData.radarData.map((p) => ({
			axis: p.subject,
			value: clamp01(toNumber(p.value, 0)),
		}));
	}, [intelData]);

	const topThreats = useMemo(() => {
		return [...activeRows].sort((a, b) => riskScore(b) - riskScore(a)).slice(0, 20);
	}, [activeRows]);

	const analysisFallbackCount = useMemo(() => {
		return analysisRows.filter((row) =>
			String(row.llm_reasoning ?? "")
				.toLowerCase()
				.includes("fallback"),
		).length;
	}, [analysisRows]);

	const renderNavbar = () => {
		return (
			<div className={styles.navbar}>
				<div className={styles.brand}>
					Dark<span>Sentinel</span>
					{criticalCount > 0 && <span className={styles.criticalBadge}>{criticalCount}</span>}
				</div>
				<div className={styles.navRight}>
					<span className={styles.navItem}>{healthN8n ? "n8n online" : "n8n idle"}</span>
					<span className={styles.navItem}>pg rows: {healthPgRows.toLocaleString()}</span>
					<span className={styles.navItem}>{healthLoading ? "live updating" : "live on"}</span>
					<span className={styles.navItem}>ISEA Phase-III · 2026</span>
				</div>
			</div>
		);
	};

	const renderFooter = () => {
		return (
			<div className={styles.footer}>
				<div className={styles.footerBrand}>DarkSentinel</div>
				<div className={styles.footerText}>
					Autonomous Dark Web Threat Intelligence · ISEA Phase-III 2026
					<br />
					IIT Madras · BITS Pilani Goa · Team Code Icon
					<br />
					9-stage pipeline: Tor · ObfusLex · Guard · GLiNER-PII · RoBERTa · Blink.new · Risk Engine
					· n8n · PostgreSQL
					<br />
					Passive OSINT only · IT Act 2000 Sec 69 · DPDP Act 2023 Sec 8(5)
				</div>
			</div>
		);
	};

	const renderHome = () => {
		return (
			<>
				{renderNavbar()}

				<section className={styles.hero}>
					<div className={styles.heroTitle}>Autonomous Dark Web Threat Intelligence</div>
					<div className={styles.heroSub}>
						9-stage AI pipeline: scrape · analyse · score · alert. Built for law enforcement OSINT.
					</div>
					<div className={styles.tagRow}>
						<span className={styles.tag}>RoBERTa Fine-tuned</span>
						<span className={styles.tag}>Blink.new AI Gateway</span>
						<span className={styles.tag}>GLiNER PII</span>
						<span className={styles.tag}>ObfusLex</span>
						<span className={styles.tag}>CIC-RF</span>
						<span className={styles.tag}>n8n Alerts</span>
						<span className={styles.tag}>PostgreSQL</span>
						<span className={styles.tag}>DPDP Compliant</span>
					</div>
				</section>

				<section className={styles.sectionBlock}>
					<div className={styles.sectionKicker}>SEVERITY ROUTING · HOW ALERTS ARE HANDLED</div>
					<SeveritySpectrum />
				</section>

				<section className={styles.modeGrid}>
					<div className={styles.modeCard}>
						<div className={styles.modeIcon}>Auto Pipeline</div>
						<div className={styles.modeTitle}>Fully Automated</div>
						<div className={styles.modeDesc}>
							One click. Select result count, press Start. All 9 stages run automatically.
						</div>
						<ul className={styles.modeFeatures}>
							<li>All 5 threat categories searched in parallel</li>
							<li>AI analysis, risk scoring, n8n alerts automatic</li>
							<li>CRITICAL: email + Slack · HIGH: Slack</li>
							<li>MEDIUM: analyst_queue · LOW: PostgreSQL</li>
							<li>Rich visual dashboard auto-generated</li>
						</ul>
						<Button variant="primary" fullWidth onClick={launchAutoMode}>
							Launch Automated Mode
						</Button>
					</div>

					<div className={styles.modeCard}>
						<div className={styles.modeIcon}>Manual Console</div>
						<div className={styles.modeTitle}>Manual Mode</div>
						<div className={styles.modeDesc}>
							Page-by-page control. Inspect every stage with full transparency.
						</div>
						<ul className={styles.modeFeatures}>
							<li>Custom queries and result counts</li>
							<li>Per-row AI analysis with explainability</li>
							<li>PII scrubber · CIC-RF network anomaly</li>
							<li>n8n alert test panel · PostgreSQL viewer</li>
							<li>Model testing · System status diagnostics</li>
						</ul>
						<Button variant="secondary" fullWidth onClick={launchManualMode}>
							Launch Manual Mode
						</Button>
					</div>
				</section>

				<section className={styles.sectionBlock}>
					<div className={styles.sectionKicker}>SYSTEM STATUS</div>
					<div className={styles.statusGrid}>
						<div className={styles.statusCol}>
							<StatusRow
								name="RoBERTa Model"
								ok={healthRoberta}
								message={healthRoberta ? "Model loaded" : `Status: ${robertaState}`}
							/>
							<StatusRow
								name="Blink.new AI Gateway"
								ok={healthApi}
								message={healthApi ? "Gateway route active" : "FastAPI bridge offline"}
							/>
						</div>
						<div className={styles.statusCol}>
							<StatusRow
								name="Tor + CIC Feed"
								ok={healthTor}
								message={
									cicLastModified
										? `${cicPrediction} @ ${cicLastModified.replace("T", " ").slice(0, 19)} UTC`
										: `Status: ${torState}`
								}
							/>
							<StatusRow
								name="ObfusLex Route"
								ok={healthApi}
								message={healthApi ? "Scrub endpoint active" : "Backend offline"}
							/>
						</div>
						<div className={styles.statusCol}>
							<StatusRow
								name="PostgreSQL"
								ok={healthPg}
								message={healthPg ? "Connected" : "Not connected"}
							/>
							<StatusRow
								name="n8n + FastAPI"
								ok={healthN8n && healthApi}
								message={healthN8n && healthApi ? "Both running" : "Start n8n + uvicorn"}
							/>
						</div>
					</div>

					{activeRows.length > 0 && (
						<>
							<div className={styles.sectionKicker} style={{ marginTop: 22 }}>
								LAST SCAN
							</div>
							<div className={styles.lastScanGrid}>
								<div className={styles.statCard}>
									<div className={styles.statValue}>{totalRows}</div>
									<div className={styles.statLabel}>TOTAL</div>
								</div>
								<div className={`${styles.statCard} ${styles.statCritical}`}>
									<div className={styles.statValue}>{criticalCount}</div>
									<div className={styles.statLabel}>CRITICAL</div>
								</div>
								<div className={`${styles.statCard} ${styles.statHigh}`}>
									<div className={styles.statValue}>{highRows}</div>
									<div className={styles.statLabel}>HIGH</div>
								</div>
								<div className={`${styles.statCard} ${styles.statMedium}`}>
									<div className={styles.statValue}>{mediumRows}</div>
									<div className={styles.statLabel}>MEDIUM</div>
								</div>
								<div className={`${styles.statCard} ${styles.statLow}`}>
									<div className={styles.statValue}>{lowRows}</div>
									<div className={styles.statLabel}>LOW</div>
								</div>
							</div>
						</>
					)}
				</section>

				{renderFooter()}
			</>
		);
	};

	const renderAutoRail = () => {
		return (
			<div className={styles.rail}>
				<div className={styles.railHead}>
					<div className={styles.railTitle}>Automated Mode Controls</div>
					<div className={styles.railMeta}>Configure, run, review</div>
				</div>
				<div className={styles.railActions}>
					<Button variant="secondary" onClick={goHome}>
						Home
					</Button>
					{autoStep === 3 ? (
						<Button
							variant="secondary"
							onClick={() => {
								setAutoStep(0);
								setAnalysisRows([]);
								setAnalysisTimeline([]);
								autoAnalysisStarted.current = false;
							}}
						>
							New Scan
						</Button>
					) : (
						<span />
					)}
					<div className={styles.stripMeta}>
						n8n alerts: <b>{n8nAlertsSent}</b> | pg rows: <b>{pgRowsSaved}</b>
					</div>
				</div>
			</div>
		);
	};

	const renderAutoConfigure = () => {
		const profile = FETCH_PROFILES[autoProfile];
		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Automated Intelligence Pipeline</div>
				<div className={styles.sectionSub}>Configure → Start → all 9 stages run automatically</div>

				<div className={styles.twoColGrid}>
					<div className={styles.panel}>
						<label className={styles.fieldLabel} htmlFor="auto-max-results">
							Max results per query
						</label>
						<select
							id="auto-max-results"
							className={styles.fieldInput}
							value={String(autoMaxResults)}
							onChange={(e) => setAutoMaxResults(Number(e.target.value))}
						>
							{[5, 10, 20, 50, 75, 100].map((n) => (
								<option key={n} value={n}>
									{n}
								</option>
							))}
						</select>

						<label className={styles.fieldLabel} htmlFor="auto-fetch-profile">
							Fetch profile
						</label>
						<select
							id="auto-fetch-profile"
							className={styles.fieldInput}
							value={autoProfile}
							onChange={(e) => setAutoProfile(e.target.value as FetchProfileLabel)}
						>
							{Object.keys(FETCH_PROFILES).map((label) => (
								<option key={label} value={label}>
									{label}
								</option>
							))}
						</select>

						<div className={`${styles.alert} ${styles.alertInfo}`}>
							<b>{autoMaxResults * 5}</b> total pages across 5 categories
						</div>
						<div className={`${styles.alert} ${styles.alertInfo}`}>
							Profile: <b>{autoProfile}</b> · search workers <b>{profile.searchWorkers}</b> · scrape
							workers <b>{profile.scrapeWorkers}</b>
						</div>
						<div className={`${styles.alert} ${healthN8n ? styles.alertOk : styles.alertWarn}`}>
							{healthN8n
								? "n8n detected - alerts will fire automatically"
								: "n8n not running - run n8n start"}
						</div>
						<div className={`${styles.alert} ${healthApi ? styles.alertOk : styles.alertWarn}`}>
							{healthApi ? "FastAPI bridge on :8000" : "FastAPI bridge offline"}
						</div>

						<Button variant="primary" fullWidth onClick={() => void startAutoScan()}>
							Start Automated Scan
						</Button>
					</div>

					<div className={styles.panel}>
						<div className={styles.panelHeader}>
							Severity routing (what happens to each result):
						</div>
						<SeveritySpectrum />
						<div className={styles.panelHeader}>Queries:</div>
						<div className={styles.queryList}>
							{THREAT_QUERIES.map((q, i) => (
								<div key={q} className={styles.queryItem}>
									<span className={styles.queryIndex}>{i + 1}</span>
									{q}
								</div>
							))}
						</div>
					</div>
				</div>
			</section>
		);
	};

	const renderAutoScraping = () => {
		const progress =
			stages.length > 0
				? Math.round(
						stages.reduce((sum, stage) => sum + toNumber(stage.progress, 0), 0) / stages.length,
					)
				: 0;

		const profile = FETCH_PROFILES[autoProfile];

		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Stage 1 - Search & Scrape</div>
				<div className={styles.sectionSub}>
					Live stage stream from scanId: {scanId ?? "pending"}
				</div>

				<div className={`${styles.alert} ${styles.alertInfo}`}>
					Runtime active: search workers {profile.searchWorkers} · scrape workers{" "}
					{profile.scrapeWorkers} · scrape full content enabled
				</div>

				{error && <div className={`${styles.alert} ${styles.alertCrit}`}>Scan error: {error}</div>}

				<div className={styles.progressWrap}>
					<div className={styles.progressTrack}>
						<div className={styles.progressFill} style={{ width: `${progress}%` }} />
					</div>
					<div className={styles.progressMeta}>
						{isRunning ? `Running... ${progress}%` : `Current progress ${progress}%`}
					</div>
				</div>

				<div className={styles.stageList}>
					{stages.map((stage) => (
						<div key={stage.name} className={styles.stageItem}>
							<div className={styles.stageHead}>
								<div className={styles.stageName}>{stage.name}</div>
								<Badge
									variant={
										stage.status === "complete"
											? "accent"
											: stage.status === "error"
												? "critical"
												: "neutral"
									}
								>
									{stage.status}
								</Badge>
							</div>
							<div className={styles.stageMeta}>progress: {toNumber(stage.progress, 0)}%</div>
						</div>
					))}
				</div>

				{!isRunning && scanRows.length > 0 && (
					<div className={`${styles.alert} ${styles.alertOk}`}>
						{scanRows.length} results collected. Moving to AI analysis stage.
					</div>
				)}

				{!isRunning && scanRows.length === 0 && (
					<div className={`${styles.alert} ${styles.alertWarn}`}>
						Waiting for scan output. If this persists, start a new scan.
					</div>
				)}
			</section>
		);
	};

	const renderAutoAiStage = () => {
		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Stage 2 - AI Analysis</div>
				<div className={styles.sectionSub}>
					RoBERTa + Blink.new consensus, risk engine, and alert routing
				</div>

				<div className={styles.twoColGrid}>
					<div className={styles.panel}>
						<div className={`${styles.alert} ${healthRoberta ? styles.alertOk : styles.alertWarn}`}>
							RoBERTa: {healthRoberta ? "model loaded" : `status ${robertaState}`}
						</div>
						<div className={`${styles.alert} ${healthApi ? styles.alertOk : styles.alertWarn}`}>
							Blink.new AI Gateway: {healthApi ? "route active" : "backend offline"}
						</div>
						<div className={`${styles.alert} ${styles.alertInfo}`}>
							Batch size: {Math.min(scanRows.length, 30)} rows
						</div>
						<div className={`${styles.alert} ${styles.alertInfo}`}>
							PostgreSQL save target: analysis rows
						</div>
					</div>

					<div className={styles.panel}>
						<div className={styles.panelHeader}>Live timeline</div>
						<div className={styles.timeline}>
							{analysisTimeline.slice(-10).map((line, i) => (
								<div key={i} className={styles.timelineItem}>
									<span className={styles.timelineDot} />
									<span>{line}</span>
								</div>
							))}
							{analysisTimeline.length === 0 && (
								<div className={styles.empty}>Timeline will appear as rows are processed.</div>
							)}
						</div>
					</div>
				</div>

				{aiRunning ? (
					<div className={`${styles.alert} ${styles.alertInfo}`}>AI analysis in progress...</div>
				) : (
					<div className={`${styles.alert} ${styles.alertOk}`}>
						Analysis complete. {analysisRows.length} rows analyzed ·{" "}
						{severityCount(analysisRows, "CRITICAL")} critical.
					</div>
				)}
			</section>
		);
	};

	const renderAutoReport = () => {
		const threatsConfirmed = analysisRows.filter(
			(row) => String(row.verdict ?? "") === "THREAT_CONFIRMED",
		).length;
		const obfuscatedCount = analysisRows.filter((row) => isObfuscated(row)).length;
		const guardBlockedCount = scanRows.filter((row) => isGuardBlocked(row)).length;
		const unresolvedCount = Math.max(0, scanRows.length - analysisRows.length);
		const blockedCount = guardBlockedCount > 0 ? guardBlockedCount : unresolvedCount;

		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Intelligence Report</div>
				<div className={styles.sectionSub}>
					{analysisRows.length} pages analysed ·{" "}
					{new Date().toISOString().slice(0, 19).replace("T", " ")} UTC
				</div>

				<div
					className={`${styles.alert} ${criticalCount > 2 ? styles.alertCrit : highRows > 0 ? styles.alertWarn : styles.alertOk}`}
				>
					<b>
						Executive Summary - Threat Level:{" "}
						{criticalCount > 2 ? "HIGH ALERT" : highRows > 0 ? "ELEVATED" : "MODERATE"}
					</b>
					<br />
					Scanned <b>{analysisRows.length}</b> dark web pages across 5 threat categories.
					<br />
					Dual-model consensus confirmed <b>{threatsConfirmed}</b> verified threats.
					<br />
					{n8nAlertsSent} n8n alerts dispatched · {pgRowsSaved} rows saved to PostgreSQL.
				</div>

				{analysisFallbackCount > 0 && (
					<div className={`${styles.alert} ${styles.alertWarn}`}>
						Fallback in use: {analysisFallbackCount} analysis row(s) used fallback reasoning due to
						backend/model unavailability.
					</div>
				)}

				<div className={styles.summaryGrid}>
					<div className={styles.summaryCard}>
						<div className={styles.summaryValue}>{scanRows.length}</div>
						<div className={styles.summaryLabel}>Sources scraped</div>
					</div>
					<div className={styles.summaryCard}>
						<div className={styles.summaryValue}>{threatsConfirmed}</div>
						<div className={styles.summaryLabel}>Threats detected</div>
					</div>
					<div className={styles.summaryCard}>
						<div className={styles.summaryValue}>{obfuscatedCount}</div>
						<div className={styles.summaryLabel}>PII redactions / obfus</div>
					</div>
					<div className={styles.summaryCard}>
						<div className={styles.summaryValue}>{n8nAlertsSent}</div>
						<div className={styles.summaryLabel}>Escalated to n8n</div>
					</div>
				</div>

				<div className={styles.sectionKicker}>SEVERITY ROUTING APPLIED</div>
				<SeveritySpectrum />

				<div className={styles.kpiGrid}>
					<div className={styles.statCard}>
						<div className={styles.statValue}>{analysisRows.length}</div>
						<div className={styles.statLabel}>Scanned</div>
					</div>
					<div className={`${styles.statCard} ${styles.statCritical}`}>
						<div className={styles.statValue}>{criticalCount}</div>
						<div className={styles.statLabel}>Critical</div>
					</div>
					<div className={`${styles.statCard} ${styles.statHigh}`}>
						<div className={styles.statValue}>{highRows}</div>
						<div className={styles.statLabel}>High</div>
					</div>
					<div className={`${styles.statCard} ${styles.statMedium}`}>
						<div className={styles.statValue}>{threatsConfirmed}</div>
						<div className={styles.statLabel}>Confirmed</div>
					</div>
					<div className={`${styles.statCard} ${styles.statLow}`}>
						<div className={styles.statValue}>{obfuscatedCount}</div>
						<div className={styles.statLabel}>Obfuscated</div>
					</div>
					<div className={styles.statCard}>
						<div className={styles.statValue}>{blockedCount}</div>
						<div className={styles.statLabel}>Blocked</div>
					</div>
				</div>

				<div className={styles.chartGrid}>
					<div className={styles.chartCard}>
						<RiskGauge score={avgRisk} />
					</div>
					<div className={styles.chartCard}>
						<RiskHistogram data={riskHistogramData} />
					</div>
					<div className={styles.chartCard}>
						<CategoryPie data={categoryPieData} />
					</div>
				</div>

				<div className={styles.sectionTitleSmall}>Top Flagged Pages</div>
				<div className={styles.topThreatList}>
					{topThreats.map((row, idx) => {
						const sev = rowSeverity(row);
						const conf = confidenceValue(row as AnalysisRow);
						const techniqueId = String(row.llm_mitre ?? "").trim();
						const techniqueName = String(row.llm_mitre_technique_name ?? "").trim();
						const tacticId = String(row.llm_mitre_tactic_id ?? "").trim();
						const tacticName = String(row.llm_mitre_tactic ?? "").trim();
						const techniqueTag =
							techniqueId && techniqueName ? `${techniqueId} - ${techniqueName}` : techniqueId;
						const tacticTag = tacticId && tacticName ? `${tacticId} - ${tacticName}` : tacticName;
						return (
							<div key={rowHash(row, `top-${idx}`)} className={styles.threatCard}>
								<div className={styles.threatMain}>
									<div className={styles.threatTitle}>{rowTitle(row).slice(0, 110)}</div>
									<div className={styles.threatMeta}>
										<span
											className={`${styles.sevPill} ${styles[`pill${sev}` as keyof typeof styles]}`}
										>
											{sev}
										</span>
										<span>{rowCategory(row)}</span>
										<span>{rowEngine(row)}</span>
										{techniqueTag && <span className={styles.mitreChip}>[{techniqueTag}]</span>}
										{tacticTag && <span className={styles.mitreChip}>[{tacticTag}]</span>}
									</div>
									<ConfidenceBar value={conf} />
								</div>
								<div className={styles.threatScore} style={{ color: SEVERITY_COLORS[sev] }}>
									{riskScore(row).toFixed(3)}
								</div>
							</div>
						);
					})}
				</div>

				<div className={styles.n8nPanel}>
					<div className={styles.n8nTitle}>n8n Alert Summary</div>
					{n8nAlertsSent > 0 ? (
						<div className={`${styles.alert} ${styles.alertOk}`}>
							{n8nAlertsSent} alert(s) dispatched (CRITICAL/HIGH)
						</div>
					) : (
						<div className={`${styles.alert} ${styles.alertWarn}`}>
							No alerts sent. Either no CRITICAL/HIGH rows or n8n not running.
						</div>
					)}
					<div className={styles.n8nMeta}>
						Webhook route: /api/alerts/send · test endpoint: /api/alerts/test
					</div>
				</div>

				<details className={styles.details}>
					<summary>Pipeline Explained (for judges)</summary>
					<div className={styles.tableWrap}>
						<table className={styles.table}>
							<thead>
								<tr>
									<th>Stage</th>
									<th>Component</th>
									<th>What it does</th>
								</tr>
							</thead>
							<tbody>
								<tr>
									<td>1</td>
									<td>Tor + darksearch.py</td>
									<td>Queries dark web sources via Tor SOCKS5.</td>
								</tr>
								<tr>
									<td>2</td>
									<td>ObfusLex</td>
									<td>Decodes obfuscated illegal jargon.</td>
								</tr>
								<tr>
									<td>3</td>
									<td>Guard Agent</td>
									<td>Blocks prompt injection patterns.</td>
								</tr>
								<tr>
									<td>4</td>
									<td>GLiNER-PII</td>
									<td>Removes sensitive personal data.</td>
								</tr>
								<tr>
									<td>5</td>
									<td>RoBERTa</td>
									<td>Threat category classifier.</td>
								</tr>
								<tr>
									<td>6</td>
									<td>Blink.new AI Gateway</td>
									<td>Contextual reasoning and mapping.</td>
								</tr>
								<tr>
									<td>7</td>
									<td>Dual-Consensus</td>
									<td>Agreement and verdict generation.</td>
								</tr>
								<tr>
									<td>8</td>
									<td>Risk Engine</td>
									<td>Computes final risk and severity.</td>
								</tr>
								<tr>
									<td>9</td>
									<td>Severity Router</td>
									<td>Routes alert actions and escalation.</td>
								</tr>
							</tbody>
						</table>
					</div>
				</details>

				<div className={styles.actionRow}>
					<Button
						variant="secondary"
						onClick={() => {
							downloadTextFile(
								`analysis_${Date.now()}.csv`,
								exportRowsCsv(analysisRows),
								"text/csv",
							);
						}}
					>
						Analysis CSV
					</Button>
					<Button
						variant="secondary"
						onClick={() => {
							downloadTextFile(`scrape_${Date.now()}.csv`, exportRowsCsv(scanRows), "text/csv");
						}}
					>
						Scrape CSV
					</Button>
				</div>
			</section>
		);
	};

	const renderAutoMode = () => {
		return (
			<>
				{renderNavbar()}
				{renderAutoRail()}

				{autoStep === 0 && renderAutoConfigure()}
				{autoStep === 1 && renderAutoScraping()}
				{autoStep === 2 && renderAutoAiStage()}
				{autoStep === 3 && renderAutoReport()}

				{renderFooter()}
			</>
		);
	};

	const renderManualRail = () => {
		return (
			<div className={styles.rail}>
				<div className={styles.railHead}>
					<div className={styles.railTitle}>Manual Intelligence Console</div>
					<div className={styles.railMeta}>Direct page control</div>
				</div>

				<div className={styles.manualTabRow}>
					{MANUAL_PAGES.map((page) => (
						<button
							key={page}
							className={`${styles.manualTab} ${manualPage === page ? styles.manualTabActive : ""}`}
							onClick={() => setManualPage(page)}
							type="button"
						>
							{page}
						</button>
					))}
				</div>

				<div className={styles.railActions}>
					<div className={styles.stripMeta}>
						results: <b>{activeRows.length}</b> | critical: <b>{criticalCount}</b>
					</div>
					<Button variant="secondary" onClick={goHome}>
						Home
					</Button>
				</div>
			</div>
		);
	};

	const renderManualDashboard = () => {
		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Command Center</div>

				{activeRows.length === 0 ? (
					<div className={`${styles.alert} ${styles.alertInfo}`}>
						No scan data. Go to Search & Scrape to run your first scan.
					</div>
				) : (
					<>
						<div className={styles.kpiGrid4}>
							<div className={`${styles.statCard} ${styles.statCritical}`}>
								<div className={styles.statValue}>{criticalCount}</div>
								<div className={styles.statLabel}>CRITICAL</div>
							</div>
							<div className={`${styles.statCard} ${styles.statHigh}`}>
								<div className={styles.statValue}>{highRows}</div>
								<div className={styles.statLabel}>HIGH</div>
							</div>
							<div className={`${styles.statCard} ${styles.statMedium}`}>
								<div className={styles.statValue}>{mediumRows}</div>
								<div className={styles.statLabel}>MEDIUM</div>
							</div>
							<div className={`${styles.statCard} ${styles.statLow}`}>
								<div className={styles.statValue}>{lowRows}</div>
								<div className={styles.statLabel}>LOW</div>
							</div>
						</div>

						<div className={styles.chartGrid}>
							<div className={styles.chartCard}>
								<RiskGauge score={avgRisk} />
							</div>
							<div className={styles.chartCard}>
								<CategoryPie data={categoryPieData} />
							</div>
						</div>
					</>
				)}

				<SeveritySpectrum />
			</section>
		);
	};

	const renderManualSearch = () => {
		const profile = FETCH_PROFILES[manualProfile];
		const blockedRows = scanRows.filter((row) => isGuardBlocked(row)).length;

		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Search & Scrape</div>

				<div className={styles.twoColGrid}>
					<div className={styles.panel}>
						<label className={styles.fieldLabel} htmlFor="manual-query-mode">
							Query mode
						</label>
						<select
							id="manual-query-mode"
							className={styles.fieldInput}
							value={manualQueryMode}
							onChange={(e) =>
								setManualQueryMode(e.target.value as "All 5 Threat Queries" | "Custom Query")
							}
						>
							<option>All 5 Threat Queries</option>
							<option>Custom Query</option>
						</select>

						{manualQueryMode === "Custom Query" && (
							<>
								<label className={styles.fieldLabel} htmlFor="manual-custom-query">
									Query
								</label>
								<input
									id="manual-custom-query"
									className={styles.fieldInput}
									value={manualCustomQuery}
									onChange={(e) => setManualCustomQuery(e.target.value)}
									placeholder="Enter custom threat query"
								/>
							</>
						)}

						<label className={styles.fieldLabel} htmlFor="manual-max-results">
							Max results per query
						</label>
						<select
							id="manual-max-results"
							className={styles.fieldInput}
							value={String(manualMaxResults)}
							onChange={(e) => setManualMaxResults(Number(e.target.value))}
						>
							{[5, 10, 20, 50, 75, 100].map((n) => (
								<option key={n} value={n}>
									{n}
								</option>
							))}
						</select>

						<label className={styles.fieldLabel} htmlFor="manual-fetch-profile">
							Fetch profile
						</label>
						<select
							id="manual-fetch-profile"
							className={styles.fieldInput}
							value={manualProfile}
							onChange={(e) => setManualProfile(e.target.value as FetchProfileLabel)}
						>
							{Object.keys(FETCH_PROFILES).map((label) => (
								<option key={label} value={label}>
									{label}
								</option>
							))}
						</select>

						<div className={`${styles.alert} ${styles.alertInfo}`}>
							Profile: <b>{manualProfile}</b> · search workers <b>{profile.searchWorkers}</b> ·
							scrape workers <b>{profile.scrapeWorkers}</b>
						</div>

						<Button variant="primary" fullWidth onClick={() => void startManualScan()}>
							Start Scan
						</Button>
					</div>

					<div className={styles.panel}>
						<div className={styles.panelHeader}>Queries</div>
						<div className={styles.queryList}>
							{manualQueries.length > 0 ? (
								manualQueries.map((q) => (
									<div key={q} className={styles.queryItem}>
										<code>{q}</code>
									</div>
								))
							) : (
								<div className={styles.empty}>No queries selected.</div>
							)}
						</div>

						<div className={styles.panelHeader}>CSV Upload</div>
						<input
							type="file"
							accept=".csv"
							className={styles.uploadInput}
							onChange={(e) => {
								const file = e.target.files?.[0];
								if (!file) return;
								void uploadCsv(file);
							}}
						/>
					</div>
				</div>

				{isRunning && (
					<div className={styles.panel}>
						<div className={styles.panelHeader}>Live scan status</div>
						<div className={styles.stageList}>
							{stages.map((stage) => (
								<div key={stage.name} className={styles.stageItem}>
									<div className={styles.stageHead}>
										<div className={styles.stageName}>{stage.name}</div>
										<Badge
											variant={
												stage.status === "complete"
													? "accent"
													: stage.status === "error"
														? "critical"
														: "neutral"
											}
										>
											{stage.status}
										</Badge>
									</div>
									<div className={styles.stageMeta}>{toNumber(stage.progress, 0)}%</div>
								</div>
							))}
						</div>
					</div>
				)}

				{scanRows.length > 0 && (
					<div className={styles.panel}>
						<div className={styles.panelHeader}>Preview ({scanRows.length} rows)</div>
						<div
							className={`${styles.alert} ${blockedRows > 0 ? styles.alertWarn : styles.alertInfo}`}
						>
							Guard blocked rows: {blockedRows}
						</div>
						<div className={styles.tableWrap}>
							<table className={styles.table}>
								<thead>
									<tr>
										<th>Title</th>
										<th>Engine</th>
										<th>Category</th>
										<th>Guard</th>
										<th>Risk</th>
										<th>Severity</th>
									</tr>
								</thead>
								<tbody>
									{scanRows.slice(0, 12).map((row, i) => (
										<tr key={rowHash(row, `preview-${i}`)}>
											<td>{rowTitle(row).slice(0, 80)}</td>
											<td>{rowEngine(row)}</td>
											<td>{rowCategory(row)}</td>
											<td>{guardStatus(row)}</td>
											<td>{riskScore(row).toFixed(3)}</td>
											<td>{rowSeverity(row)}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>

						<div className={styles.actionRow}>
							<Button
								variant="secondary"
								onClick={() =>
									downloadTextFile(`manual_${Date.now()}.csv`, exportRowsCsv(scanRows), "text/csv")
								}
							>
								Download CSV
							</Button>
						</div>
					</div>
				)}
			</section>
		);
	};

	const renderManualAi = () => {
		const selected = scanRows[Math.max(0, Math.min(scanRows.length - 1, manualSelectedIndex))];
		const manualFallbackActive =
			Boolean(manualExplain?.model === "fallback") ||
			String(manualAnalysis?.llm_reasoning ?? "")
				.toLowerCase()
				.includes("fallback");

		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>AI Analysis</div>

				{scanRows.length === 0 ? (
					<div className={`${styles.alert} ${styles.alertInfo}`}>
						No scan data. Run Search & Scrape first.
					</div>
				) : (
					<>
						<div className={styles.panel}>
							<label className={styles.fieldLabel} htmlFor="manual-selected-row">
								Select row
							</label>
							<select
								id="manual-selected-row"
								className={styles.fieldInput}
								value={String(manualSelectedIndex)}
								onChange={(e) => setManualSelectedIndex(Number(e.target.value))}
							>
								{scanRows.map((row, idx) => (
									<option key={rowHash(row, `select-${idx}`)} value={idx}>
										[{rowEngine(row)}] {rowTitle(row).slice(0, 70)}
									</option>
								))}
							</select>

							<div className={styles.manualMeta}>
								Engine: {rowEngine(selected)} · Query: {String(selected?.query ?? "-")}
							</div>

							<details className={styles.details}>
								<summary>Raw text</summary>
								<pre className={styles.codeBlock}>{rowText(selected).slice(0, 1200)}</pre>
							</details>

							<Button variant="primary" onClick={() => void runManualAnalysis()}>
								Run Full Analysis
							</Button>
						</div>

						{manualAnalysis && (
							<>
								{manualFallbackActive && (
									<div className={`${styles.alert} ${styles.alertWarn}`}>
										Fallback in use for this row: results include fallback model output.
									</div>
								)}

								<div className={styles.twoColGrid}>
									<div className={styles.panel}>
										<div className={styles.panelHeader}>RoBERTa</div>
										<div className={styles.bigMetric}>
											{String(
												manualAnalysis.roberta_category ?? manualAnalysis.category ?? "UNKNOWN",
											).toUpperCase()}
										</div>
										<div className={styles.inlineMetric}>Confidence</div>
										<ConfidenceBar value={confidenceValue(manualAnalysis)} />
									</div>

									<div className={styles.panel}>
										<div className={styles.panelHeader}>Blink.new AI Gateway</div>
										<div className={styles.bigMetric}>
											{String(
												manualAnalysis.llm_category ?? manualAnalysis.category ?? "UNKNOWN",
											).toUpperCase()}
										</div>
										<div className={styles.inlineMetric}>Severity</div>
										<div style={{ color: SEVERITY_COLORS[rowSeverity(manualAnalysis)] }}>
											{rowSeverity(manualAnalysis)}
										</div>
										{(manualAnalysis.llm_mitre ||
											manualAnalysis.llm_mitre_tactic ||
											manualAnalysis.llm_mitre_technique_name) && (
											<div className={styles.manualMeta}>
												{manualAnalysis.llm_mitre
													? `[${String(manualAnalysis.llm_mitre)}${manualAnalysis.llm_mitre_technique_name ? ` - ${String(manualAnalysis.llm_mitre_technique_name)}` : ""}]`
													: ""}
												{manualAnalysis.llm_mitre_tactic
													? ` ${manualAnalysis.llm_mitre_tactic_id ? `[${String(manualAnalysis.llm_mitre_tactic_id)} - ${String(manualAnalysis.llm_mitre_tactic)}]` : `[${String(manualAnalysis.llm_mitre_tactic)}]`}`
													: ""}
											</div>
										)}
									</div>
								</div>

								<div className={styles.twoColGrid}>
									<div className={styles.panel}>
										<div className={styles.panelHeader}>Dual Consensus</div>
										<div className={styles.bigMetric}>
											{String(manualAnalysis.verdict ?? "HUMAN_REVIEW")}
										</div>
										<div className={styles.inlineMetric}>
											C-score: {confidenceValue(manualAnalysis).toFixed(3)}
										</div>
									</div>

									<div className={styles.panel}>
										<div className={styles.panelHeader}>Risk Engine</div>
										<div
											className={styles.bigMetric}
											style={{ color: SEVERITY_COLORS[rowSeverity(manualAnalysis)] }}
										>
											{riskScore(manualAnalysis).toFixed(3)}
										</div>
										<div className={styles.inlineMetric}>
											{rowSeverity(manualAnalysis)} ·{" "}
											{String(manualAnalysis.action ?? "analyst_queue")}
										</div>
									</div>
								</div>

								{(manualExplainLoading ||
									manualExplain?.summary ||
									manualAnalysis.llm_reasoning) && (
									<div className={styles.panel}>
										<div className={styles.panelHeader}>Explain This Threat</div>
										{manualExplainLoading ? (
											<div className={styles.inlineMetric}>Generating summary...</div>
										) : (
											<>
												<div>{manualExplain?.summary || manualAnalysis.llm_reasoning}</div>
												{(manualExplain?.model ||
													manualExplain?.generated_at ||
													manualExplain?.error) && (
													<div className={styles.manualMeta}>
														model: {manualExplain?.model ?? "unknown"}
														{manualExplain?.generated_at
															? ` · generated: ${String(manualExplain.generated_at).replace("T", " ").slice(0, 19)} UTC`
															: ""}
														{manualExplain?.error
															? ` · note: ${String(manualExplain.error).slice(0, 140)}`
															: ""}
													</div>
												)}
											</>
										)}
									</div>
								)}
							</>
						)}
					</>
				)}
			</section>
		);
	};

	const renderManualAnalytics = () => {
		const guardBlockedCount = scanRows.filter((row) => isGuardBlocked(row)).length;
		const unresolvedCount = Math.max(0, scanRows.length - analysisRows.length);
		const blockedCount = guardBlockedCount > 0 ? guardBlockedCount : unresolvedCount;

		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Advanced Intelligence Workbench</div>
				<div className={styles.sectionSub}>Granular multi-stage pipeline telemetry</div>

				<div className={styles.manualTabRow}>
					{(
						[
							"Executive Summary",
							"Network Anomaly",
							"Recon Engine",
							"AI Intelligence Matrix",
						] as AnalyticsView[]
					).map((view) => (
						<button
							key={view}
							className={`${styles.manualTab} ${analyticsView === view ? styles.manualTabActive : ""}`}
							onClick={() => setAnalyticsView(view)}
							type="button"
						>
							{view}
						</button>
					))}
				</div>

				{analyticsView === "Executive Summary" && (
					<>
						<div className={styles.kpiGrid4}>
							<div className={styles.statCard}>
								<div className={styles.statValue}>{activeRows.length}</div>
								<div className={styles.statLabel}>Total</div>
							</div>
							<div className={`${styles.statCard} ${styles.statLow}`}>
								<div className={styles.statValue}>
									{Math.max(0, activeRows.length - highRows - criticalCount)}
								</div>
								<div className={styles.statLabel}>Safe to Process</div>
							</div>
							<div className={`${styles.statCard} ${styles.statHigh}`}>
								<div className={styles.statValue}>
									{activeRows.filter((r) => isObfuscated(r)).length}
								</div>
								<div className={styles.statLabel}>Obfuscation</div>
							</div>
							<div className={`${styles.statCard} ${styles.statCritical}`}>
								<div className={styles.statValue}>{blockedCount}</div>
								<div className={styles.statLabel}>Guard Blocked</div>
							</div>
						</div>

						<SeveritySpectrum />

						<div className={styles.chartGrid}>
							<div className={styles.chartCard}>
								<CategoryPie data={categoryPieData} />
							</div>
							<div className={styles.chartCard}>
								<RiskHistogram data={riskHistogramData} />
							</div>
							<div className={styles.chartCard}>
								<SeverityTrend data={severityTrendData} />
							</div>
						</div>

						{execData && (
							<div className={`${styles.alert} ${styles.alertOk}`}>
								PostgreSQL historical telemetry:{" "}
								{toNumber(execData.totalThreats, 0).toLocaleString()} rows · top category{" "}
								{execData.topCategory}
							</div>
						)}
					</>
				)}

				{analyticsView === "Network Anomaly" && (
					<>
						<div className={styles.sectionTitleSmall}>Live CIC-RF Network Profiler</div>
						{networkData ? (
							<>
								<div className={styles.kpiGrid4}>
									<div className={styles.statCard}>
										<div className={styles.statValue}>{networkData.totalAnomalies}</div>
										<div className={styles.statLabel}>Total Anomalies</div>
									</div>
									<div className={`${styles.statCard} ${styles.statCritical}`}>
										<div className={styles.statValue}>{networkData.criticalAnomalies}</div>
										<div className={styles.statLabel}>Critical</div>
									</div>
									<div className={styles.statCard}>
										<div className={styles.statValue}>{networkData.uniqueSources}</div>
										<div className={styles.statLabel}>Unique Sources</div>
									</div>
									<div className={`${styles.statCard} ${styles.statHigh}`}>
										<div className={styles.statValue}>{networkData.activeAlerts}</div>
										<div className={styles.statLabel}>Active Alerts</div>
									</div>
								</div>

								<div className={styles.tableWrap}>
									<table className={styles.table}>
										<thead>
											<tr>
												<th>Type</th>
												<th>Source</th>
												<th>Destination</th>
												<th>Severity</th>
												<th>Time</th>
											</tr>
										</thead>
										<tbody>
											{networkData.events.slice(0, 20).map((event) => (
												<tr key={event.id}>
													<td>{event.type}</td>
													<td>{event.source_ip}</td>
													<td>{event.destination}</td>
													<td>{String(event.severity).toUpperCase()}</td>
													<td>{event.timestamp.replace("T", " ").slice(0, 19)}</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							</>
						) : (
							<div className={`${styles.alert} ${styles.alertInfo}`}>
								Network telemetry unavailable.
							</div>
						)}
					</>
				)}

				{analyticsView === "Recon Engine" &&
					(reconData ? (
						<>
							<div className={styles.kpiGrid4}>
								<div className={styles.statCard}>
									<div className={styles.statValue}>{reconData.totalQueries}</div>
									<div className={styles.statLabel}>Total Queries</div>
								</div>
								<div className={styles.statCard}>
									<div className={styles.statValue}>
										{(reconData.avgMatchRate * 100).toFixed(1)}%
									</div>
									<div className={styles.statLabel}>Avg Match Rate</div>
								</div>
								<div className={styles.statCard}>
									<div className={styles.statValue}>{reconData.avgConfidence.toFixed(2)}</div>
									<div className={styles.statLabel}>Avg Confidence</div>
								</div>
								<div className={styles.statCard}>
									<div className={styles.statValue}>{reconData.totalResults}</div>
									<div className={styles.statLabel}>Total Results</div>
								</div>
							</div>

							<div className={styles.tableWrap}>
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
										{reconData.results.map((r) => (
											<tr key={r.query}>
												<td>{r.query}</td>
												<td>{r.totalResults}</td>
												<td>{(r.matchRate * 100).toFixed(1)}%</td>
												<td>
													<ConfidenceBar value={clamp01(r.avgConfidence)} />
												</td>
												<td>{r.topSources.slice(0, 3).join(", ")}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						</>
					) : (
						<div className={`${styles.alert} ${styles.alertInfo}`}>Recon data unavailable.</div>
					))}

				{analyticsView === "AI Intelligence Matrix" &&
					(intelData ? (
						<>
							<div className={styles.kpiGrid4}>
								<div className={styles.statCard}>
									<div className={styles.statValue}>{intelData.totalAnalyses}</div>
									<div className={styles.statLabel}>Total Analyses</div>
								</div>
								<div className={styles.statCard}>
									<div className={styles.statValue}>
										{(intelData.avgAccuracy * 100).toFixed(1)}%
									</div>
									<div className={styles.statLabel}>Avg Accuracy</div>
								</div>
								<div className={styles.statCard}>
									<div className={styles.statValue}>
										{(intelData.consensusRate * 100).toFixed(1)}%
									</div>
									<div className={styles.statLabel}>Consensus</div>
								</div>
								<div className={styles.statCard}>
									<div className={styles.statValue}>{intelData.engines.length}</div>
									<div className={styles.statLabel}>Active Engines</div>
								</div>
							</div>

							<div className={styles.chartGrid}>
								<div className={styles.chartCard}>
									<ConfidenceRadar data={analyticsRadar} />
								</div>
								<div className={styles.chartCard}>
									<div className={styles.tableWrap}>
										<table className={styles.table}>
											<thead>
												<tr>
													<th>Hash</th>
													<th>RoBERTa</th>
													<th>Blink</th>
													<th>Consensus</th>
												</tr>
											</thead>
											<tbody>
												{intelData.recentDisagreements.map((d) => (
													<tr key={d.hash}>
														<td>{d.hash.slice(0, 12)}...</td>
														<td>{d.roberta}</td>
														<td>{d.blink}</td>
														<td>{d.consensus}</td>
													</tr>
												))}
											</tbody>
										</table>
									</div>
								</div>
							</div>
						</>
					) : (
						<div className={`${styles.alert} ${styles.alertInfo}`}>
							Intelligence matrix unavailable.
						</div>
					))}
			</section>
		);
	};

	const renderManualThreatFeed = () => {
		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Live Threat Feed</div>

				<div className={styles.kpiGrid4}>
					<div className={`${styles.statCard} ${styles.statLow}`}>
						<div className={styles.statValue}>{severityCount(activeRows, "LOW")}</div>
						<div className={styles.statLabel}>LOW</div>
					</div>
					<div className={`${styles.statCard} ${styles.statMedium}`}>
						<div className={styles.statValue}>{severityCount(activeRows, "MEDIUM")}</div>
						<div className={styles.statLabel}>MEDIUM</div>
					</div>
					<div className={`${styles.statCard} ${styles.statHigh}`}>
						<div className={styles.statValue}>{severityCount(activeRows, "HIGH")}</div>
						<div className={styles.statLabel}>HIGH</div>
					</div>
					<div className={`${styles.statCard} ${styles.statCritical}`}>
						<div className={styles.statValue}>{severityCount(activeRows, "CRITICAL")}</div>
						<div className={styles.statLabel}>CRITICAL</div>
					</div>
				</div>

				<div className={styles.filterGrid}>
					<div>
						<label className={styles.fieldLabel} htmlFor="feed-category">
							Category
						</label>
						<select
							id="feed-category"
							className={styles.fieldInput}
							value={feedCategory}
							onChange={(e) => setFeedCategory(e.target.value)}
						>
							{threatFeedCategories.map((option) => (
								<option key={option} value={option}>
									{option}
								</option>
							))}
						</select>
					</div>
					<div>
						<label className={styles.fieldLabel} htmlFor="feed-severity">
							Severity
						</label>
						<select
							id="feed-severity"
							className={styles.fieldInput}
							value={feedSeverity}
							onChange={(e) => setFeedSeverity(e.target.value as "All" | Severity)}
						>
							<option value="All">All</option>
							{SEVERITY_ORDER.map((sev) => (
								<option key={sev} value={sev}>
									{sev}
								</option>
							))}
						</select>
					</div>
					<div>
						<label className={styles.fieldLabel} htmlFor="feed-engine">
							Engine
						</label>
						<select
							id="feed-engine"
							className={styles.fieldInput}
							value={feedEngine}
							onChange={(e) => setFeedEngine(e.target.value)}
						>
							{threatFeedEngines.map((option) => (
								<option key={option} value={option}>
									{option}
								</option>
							))}
						</select>
					</div>
					<label className={styles.checkboxRow}>
						<input
							type="checkbox"
							checked={feedThreatsOnly}
							onChange={(e) => setFeedThreatsOnly(e.target.checked)}
						/>
						Threats only
					</label>
					<label className={styles.checkboxRow}>
						<input
							type="checkbox"
							checked={feedObfuscatedOnly}
							onChange={(e) => setFeedObfuscatedOnly(e.target.checked)}
						/>
						Obfuscated only
					</label>
				</div>

				<div className={styles.inlineMetric}>
					Showing {filteredThreatFeed.length} / {activeRows.length} results
				</div>

				<div className={styles.tableWrap}>
					<table className={styles.table}>
						<thead>
							<tr>
								<th>Title</th>
								<th>Engine</th>
								<th>Query</th>
								<th>Category</th>
								<th>Guard</th>
								<th>Severity</th>
								<th>R-Score</th>
								<th>Timestamp</th>
							</tr>
						</thead>
						<tbody>
							{filteredThreatFeed.slice(0, 200).map((row, idx) => (
								<tr key={rowHash(row, `feed-${idx}`)}>
									<td>{rowTitle(row).slice(0, 80)}</td>
									<td>{rowEngine(row)}</td>
									<td>{String(row.query ?? "-")}</td>
									<td>{rowCategory(row)}</td>
									<td>{guardStatus(row)}</td>
									<td>{rowSeverity(row)}</td>
									<td>{riskScore(row).toFixed(3)}</td>
									<td>{rowTimestamp(row).replace("T", " ").slice(0, 19)}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>

				<div className={styles.actionRow}>
					<Button
						variant="secondary"
						onClick={() =>
							downloadTextFile(
								`threats_${Date.now()}.csv`,
								exportRowsCsv(filteredThreatFeed),
								"text/csv",
							)
						}
					>
						Download Filtered CSV
					</Button>
				</div>
			</section>
		);
	};

	const renderManualPii = () => {
		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>PII Protection (GLiNER)</div>

				<div className={styles.panel}>
					<label className={styles.fieldLabel} htmlFor="pii-example">
						Load example
					</label>
					<select
						id="pii-example"
						className={styles.fieldInput}
						value={piiExample}
						onChange={(e) => {
							const next = e.target.value;
							setPiiExample(next);
							if (next === "Custom input") return;
							setPiiInput(next);
						}}
					>
						<option>Custom input</option>
						<option>
							John Smith +91-9876543210 selling pills BTC:1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2
						</option>
						<option>hacker@proton.me ships from 192.168.1.1</option>
						<option>Aadhaar 1234-5678-9012 HDFC 50100123456789</option>
						<option>CLONED CARDS +44-7700-900-123 Telegram @vendor99</option>
					</select>

					<label className={styles.fieldLabel} htmlFor="pii-input">
						Text
					</label>
					<textarea
						id="pii-input"
						className={styles.textArea}
						rows={4}
						value={piiInput}
						onChange={(e) => setPiiInput(e.target.value)}
						placeholder="Paste content to scrub..."
					/>

					<Button variant="primary" onClick={() => void scrubPii()} loading={piiLoading}>
						Scrub PII
					</Button>
				</div>

				{piiResult && (
					<div className={styles.twoColGrid}>
						<div className={styles.panel}>
							<div className={styles.panelHeader}>Original</div>
							<pre className={styles.codeBlock}>{piiResult.original}</pre>
						</div>
						<div className={styles.panel}>
							<div className={styles.panelHeader}>Scrubbed</div>
							<pre className={styles.codeBlock}>{piiResult.scrubbed}</pre>
						</div>
					</div>
				)}

				{piiResult && (
					<div
						className={`${styles.alert} ${piiResult.redactions.length > 0 ? styles.alertOk : styles.alertInfo}`}
					>
						{piiResult.redactions.length > 0
							? `Removed ${piiResult.redactions.length} item(s): ${piiResult.redactions.map((r) => r.type).join(", ")}`
							: "No PII detected."}
					</div>
				)}

				{piiError && <div className={`${styles.alert} ${styles.alertCrit}`}>{piiError}</div>}
			</section>
		);
	};

	const renderManualNetworkAnomaly = () => {
		const modelLoaded = healthApi && healthTor;

		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>CIC-RF Network Anomaly</div>
				<div className={styles.sectionSub}>Innovation #1 - traffic-triggered scraping</div>

				<div className={`${styles.alert} ${modelLoaded ? styles.alertOk : styles.alertWarn}`}>
					{modelLoaded
						? `CIC-RF endpoint active · tor=${torState} · scheduler=${schedulerState}`
						: "CIC-RF endpoint unavailable. Check FastAPI/Tor runtime and watcher state."}
				</div>

				<div className={styles.twoColGrid}>
					<div className={styles.panel}>
						<Button
							variant="secondary"
							fullWidth
							onClick={() => void runNetworkSimulation("normal")}
							disabled={!healthApi || networkSimulationLoading}
						>
							{networkSimulationLoading ? "Running..." : "Run Normal Network Probe"}
						</Button>
					</div>
					<div className={styles.panel}>
						<Button
							variant="primary"
							fullWidth
							onClick={() => void runNetworkSimulation("tor")}
							disabled={!healthApi || networkSimulationLoading}
						>
							{networkSimulationLoading ? "Running..." : "Run Tor Network Probe"}
						</Button>
					</div>
				</div>

				{networkError && <div className={`${styles.alert} ${styles.alertWarn}`}>{networkError}</div>}

				{networkSimulation && (
					<div className={styles.twoColGrid}>
						<div className={styles.panel}>
							<RiskGauge score={networkSimulation.tScore} />
							<div
								className={`${styles.alert} ${networkSimulation.triggerScrape ? styles.alertCrit : styles.alertOk}`}
							>
								Prediction: <b>{networkSimulation.prediction}</b> · T-score:{" "}
								<b>{networkSimulation.tScore.toFixed(4)}</b> · Trigger:{" "}
								<b>{networkSimulation.triggerScrape ? "YES" : "NO"}</b>
							</div>
						</div>
						<div className={styles.panel}>
							<div className={styles.panelHeader}>Class probabilities</div>
							{networkSimulation.probabilities.length > 0 ? (
								<div className={styles.tableWrap}>
									<table className={styles.table}>
										<thead>
											<tr>
												<th>Class</th>
												<th>P</th>
											</tr>
										</thead>
										<tbody>
											{networkSimulation.probabilities.map((p) => (
												<tr key={p.label}>
													<td>{p.label}</td>
													<td>{p.p.toFixed(4)}</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							) : (
								<div className={`${styles.alert} ${styles.alertInfo}`}>
									No class probability payload returned by backend.
								</div>
							)}
							{networkSimulation.detectedAt && (
								<div className={styles.inlineMetric}>
									Detected at: {networkSimulation.detectedAt.replace("T", " ").slice(0, 19)} UTC
								</div>
							)}
							{networkSimulation.fallbackUsed && (
								<div className={`${styles.alert} ${styles.alertWarn}`}>
									Backend indicates fallback model output for this probe.
								</div>
							)}
							{(networkSimulation.dataSource || networkSimulation.error) && (
								<div className={styles.manualMeta}>
									source: {networkSimulation.dataSource ?? "unknown"}
									{networkSimulation.error ? ` · note: ${networkSimulation.error}` : ""}
								</div>
							)}
						</div>
					</div>
				)}

				{networkData && (
					<div className={styles.panel}>
						<div className={styles.panelHeader}>Last anomaly records</div>
						<div className={styles.tableWrap}>
							<table className={styles.table}>
								<thead>
									<tr>
										<th>Type</th>
										<th>Source</th>
										<th>Destination</th>
										<th>Severity</th>
									</tr>
								</thead>
								<tbody>
									{networkData.events.slice(0, 8).map((event) => (
										<tr key={event.id}>
											<td>{event.type}</td>
											<td>{event.source_ip}</td>
											<td>{event.destination}</td>
											<td>{String(event.severity).toUpperCase()}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</div>
				)}
			</section>
		);
	};

	const renderManualN8n = () => {
		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>n8n Alert Integration</div>

				<div className={styles.statusGrid3}>
					<StatusRow
						name="n8n Workflow"
						ok={healthN8n}
						message={healthN8n ? "localhost:5678" : "Run: n8n start"}
					/>
					<StatusRow
						name="FastAPI Bridge"
						ok={healthApi}
						message={healthApi ? API_BASE_LABEL : "Run: uvicorn alert_api:app --port 8000"}
					/>
					<StatusRow name="Session Alerts" ok={true} message={`${n8nAlertsSent} fired`} />
				</div>

				<div className={styles.panelHeader}>How severity is routed:</div>
				<SeveritySpectrum />

				<div className={styles.n8nPanel}>
					<div className={styles.n8nTitle}>Test Alert Pipeline</div>
					<Button variant="primary" onClick={() => void sendCriticalTestAlert()} disabled={!healthApi}>
						Send CRITICAL Test Alert
					</Button>
					<div className={styles.n8nMeta}>Check n8n execution log at http://localhost:5678</div>
				</div>

				{n8nActionNotice && <div className={`${styles.alert} ${styles.alertInfo}`}>{n8nActionNotice}</div>}

				{activeRows.length > 0 && (
					<div className={styles.panel}>
						<div className={styles.panelHeader}>
							{
								activeRows.filter((r) => rowSeverity(r) === "CRITICAL" || rowSeverity(r) === "HIGH")
									.length
							}{" "}
							CRITICAL/HIGH in current scan
						</div>
						<Button variant="secondary" onClick={() => void fireAlertsForCurrentRows()} disabled={!healthApi}>
							Fire Alerts For All
						</Button>
					</div>
				)}

				<details className={styles.details}>
					<summary>n8n Workflow Setup Guide</summary>
					<pre className={styles.codeBlock}>{`STEP 1 - Webhook Trigger node
HTTP Method: POST
Path: darksentinel-alert

STEP 2 - IF node by severity
Condition: $json.body.severity equals CRITICAL

STEP 3a - True branch (CRITICAL)
Gmail + Slack nodes

STEP 3b - False branch (HIGH/MEDIUM)
Slack node

TERMinals:
1) npm run dev
2) python -m uvicorn alert_api:app --port 8000
3) tor.exe
4) n8n start`}</pre>
				</details>
			</section>
		);
	};

	const renderManualModelTesting = () => {
		const passed = modelResults.filter((r) => r.match).length;
		const pct = modelResults.length > 0 ? passed / modelResults.length : 0;

		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>Model Testing</div>

				<div className={styles.twoColGrid}>
					<div className={`${styles.alert} ${healthRoberta ? styles.alertOk : styles.alertWarn}`}>
						RoBERTa {healthRoberta ? "loaded" : `status ${robertaState}`}
					</div>
					<div className={`${styles.alert} ${healthApi ? styles.alertOk : styles.alertWarn}`}>
						Blink.new gateway route {healthApi ? "available" : "unavailable"}
					</div>
				</div>

				<div className={styles.manualTabRow}>
					<button
						type="button"
						className={`${styles.manualTab} ${testMode === "RoBERTa only" ? styles.manualTabActive : ""}`}
						onClick={() => setTestMode("RoBERTa only")}
					>
						RoBERTa only (fast)
					</button>
					<button
						type="button"
						className={`${styles.manualTab} ${testMode === "RoBERTa + Blink.new AI Gateway" ? styles.manualTabActive : ""}`}
						onClick={() => setTestMode("RoBERTa + Blink.new AI Gateway")}
					>
						RoBERTa + Blink.new AI Gateway
					</button>
				</div>

				<div className={styles.inlineMetric}>Testing mode: {testMode}</div>
				{modelTestNotice && <div className={`${styles.alert} ${styles.alertInfo}`}>{modelTestNotice}</div>}

				<Button variant="primary" onClick={() => void runAllModelTests()} loading={modelTesting}>
					Run Live Dataset Tests
				</Button>

				{modelResults.length > 0 && (
					<>
						<div className={styles.tableWrap}>
							<table className={styles.table}>
								<thead>
									<tr>
										<th>Source Hash</th>
										<th>Input</th>
										<th>Expected</th>
										<th>Predicted</th>
										<th>Confidence</th>
										<th>Risk</th>
										<th>Severity</th>
										<th>Match</th>
									</tr>
								</thead>
								<tbody>
									{modelResults.map((r) => (
										<tr key={`${r.sourceHash}-${r.predicted}-${r.severity}`}>
											<td>{r.sourceHash.slice(0, 16)}</td>
											<td>{r.input.slice(0, 56)}</td>
											<td>{r.expected}</td>
											<td>{r.predicted}</td>
											<td>{(r.confidence * 100).toFixed(1)}%</td>
											<td>{r.risk.toFixed(3)}</td>
											<td>{r.severity}</td>
											<td>{r.match ? "PASS" : "FAIL"}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
						<div
							className={`${styles.alert} ${pct >= 0.8 ? styles.alertOk : pct >= 0.5 ? styles.alertWarn : styles.alertCrit}`}
						>
							Test Accuracy: {passed}/{modelResults.length} = {(pct * 100).toFixed(0)}%
						</div>
					</>
				)}

				<div className={styles.panel}>
					<div className={styles.panelHeader}>Custom Input Test</div>
					<textarea
						className={styles.textArea}
						rows={3}
						value={customTestInput}
						onChange={(e) => setCustomTestInput(e.target.value)}
						placeholder="Paste custom threat text"
					/>
					<Button variant="secondary" onClick={() => void classifyCustomModelInput()}>
						Classify
					</Button>

					{customTestResult && (
						<div className={styles.panel} style={{ marginTop: 12 }}>
							<div className={styles.bigMetric}>
								{String(customTestResult.category ?? "UNKNOWN").toUpperCase()}
							</div>
							<div className={styles.inlineMetric}>
								Combined confidence: {(confidenceValue(customTestResult) * 100).toFixed(1)}%
							</div>
							<ConfidenceBar value={confidenceValue(customTestResult)} />
							<div
								className={`${styles.alert} ${rowSeverity(customTestResult) === "CRITICAL" ? styles.alertCrit : styles.alertInfo}`}
							>
								Verdict: {String(customTestResult.verdict ?? "HUMAN_REVIEW")} · Risk:{" "}
								{riskScore(customTestResult).toFixed(3)} {rowSeverity(customTestResult)}
							</div>
						</div>
					)}
				</div>
			</section>
		);
	};

	const renderManualSystemStatus = () => {
		const components = [
			{ name: "Tor Network", ok: healthTor, detail: `status ${torState}` },
			{ name: "GLiNER-PII", ok: healthApi, detail: healthApi ? "Route active" : "Unavailable" },
			{ name: "RoBERTa", ok: healthRoberta, detail: healthRoberta ? "Model loaded" : `status ${robertaState}` },
			{
				name: "Blink.new AI Gateway",
				ok: healthApi,
				detail: healthApi ? "Gateway route active" : "Unavailable",
			},
			{
				name: "CIC-RF Darknet",
				ok: Boolean(cicLastModified) || healthTor,
				detail: cicLastModified
					? `${cicPrediction} @ ${cicLastModified.replace("T", " ").slice(0, 19)} UTC`
					: "No live CIC feed status",
			},
			{ name: "ObfusLex Route", ok: healthApi, detail: healthApi ? "Scrub endpoint active" : "Unavailable" },
			{ name: "Scheduler", ok: healthScheduler, detail: `status ${schedulerState}` },
			{ name: "PostgreSQL", ok: healthPg, detail: healthPg ? "Connected" : "Not connected" },
			{
				name: "n8n Workflow",
				ok: healthN8n,
				detail: healthN8n ? "localhost:5678 active" : "Run: n8n start",
			},
			{
				name: "FastAPI Bridge",
				ok: healthApi,
				detail: healthApi ? `${API_BASE_LABEL} active` : "Bridge offline",
			},
		];

		return (
			<section className={styles.sectionBlock}>
				<div className={styles.sectionTitle}>System Status</div>

				<div className={`${styles.alert} ${healthApi ? styles.alertInfo : styles.alertWarn}`}>
					Live panel auto-refresh: {healthApi ? "active" : "degraded"}
				</div>

				<div className={styles.panelHeader}>Component Status:</div>
				<div className={styles.componentList}>
					{components.map((c) => (
						<div key={c.name} className={styles.componentRow}>
							<span className={`${styles.statusDot} ${c.ok ? styles.dotOk : styles.dotWarn}`} />
							<span className={styles.componentName}>{c.name}</span>
							<span className={styles.componentDetail}>{c.detail}</span>
						</div>
					))}
				</div>

				<div className={styles.panelHeader}>Backend Health Metadata:</div>
				<div className={styles.envGrid}>
					<div className={styles.envRow}>
						<b>Auth Required</b>: {health?.auth_required ? "yes" : "no"}
					</div>
					<div className={styles.envRow}>
						<b>Notify Medium</b>: {health?.notify_medium ? "enabled" : "disabled"}
					</div>
					<div className={styles.envRow}>
						<b>Webhook</b>: {health?.n8n_webhook ? String(health.n8n_webhook) : "not reported"}
					</div>
					<div className={styles.envRow}>
						<b>Outbox</b>: q={toNumber(health?.n8n_outbox?.queue_depth, 0)} · delivered=
						{toNumber(health?.n8n_outbox?.delivered, 0)} · failed=
						{toNumber(health?.n8n_outbox?.failed, 0)}
					</div>
					<div className={styles.envRow}>
						<b>Session</b>: processed={toNumber(health?.session_stats?.total_processed, 0)} · n8n_sent=
						{toNumber(health?.session_stats?.n8n_sent, 0)} · pg_stored=
						{toNumber(health?.session_stats?.pg_stored, 0)}
					</div>
				</div>
			</section>
		);
	};

	const renderManualMode = () => {
		return (
			<>
				{renderNavbar()}
				{renderManualRail()}

				{manualPage === "Dashboard" && renderManualDashboard()}
				{manualPage === "Search & Scrape" && renderManualSearch()}
				{manualPage === "AI Analysis" && renderManualAi()}
				{manualPage === "Analytics" && renderManualAnalytics()}
				{manualPage === "Threat Feed" && renderManualThreatFeed()}
				{manualPage === "PII Protection" && renderManualPii()}
				{manualPage === "Network Anomaly" && renderManualNetworkAnomaly()}
				{manualPage === "n8n Alerts" && renderManualN8n()}
				{manualPage === "Model Testing" && renderManualModelTesting()}
				{manualPage === "System Status" && renderManualSystemStatus()}

				{renderFooter()}
			</>
		);
	};

	return (
		<div className={styles.page}>
			{mode === "home" && renderHome()}
			{mode === "auto" && renderAutoMode()}
			{mode === "manual" && renderManualMode()}
		</div>
	);
}
