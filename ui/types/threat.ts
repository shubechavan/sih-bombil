export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export type Consensus = "THREAT_CONFIRMED" | "HUMAN_REVIEW" | "CLEAN";

export interface Threat {
	rowHash: string;
	riskScore: number;
	textSnippet: string;
	searchEngine: string;
	timestamp: string;
	category: string;
	severity: Severity;

	sourceIp?: string;
	destinationIp?: string;
	sourcePort?: number;
	destinationPort?: number;
	protocol?: string;
	flowDuration?: number;
	bytesPerSecond?: number;
	packetsPerSecond?: number;
	label?: string;
	consensus?: Consensus;
	robertaCategory?: string;
	robertaConfidence?: number;
	blinkCategory?: string;
	blinkSeverity?: string;
	mitreTags?: string[];
	llm_mitre?: string | null;
	llm_mitre_tactic?: string | null;
	llm_mitre_tactic_id?: string | null;
	llm_mitre_technique_name?: string | null;
	obfuscationDetected?: boolean;
	obfusFlaggedTokens?: string[];
	piiRedactions?: PiiRedaction[];
	raw_text?: string;
	guard_status?: "safe" | "blocked";
	obfuscation_detected?: boolean;
	obfus_flagged_tokens?: string[];

	// Backward-compatible aliases for legacy backend payloads
	source_hash?: string;
	title?: string;
	engine?: string;
	query?: string;
	clean_text?: string;
	r_score?: number;
	created_at?: string;
}

export interface PiiRedaction {
	type: string;
	count: number;
}

export type ScanResult = Threat;

export interface AnalysisResult {
	roberta: {
		category: string;
		confidence: number;
		scores: Record<string, number>;
	};
	blink: {
		category: string;
		severity: string;
		mitreTags: string[];
		reasoning: string;
	};
	consensus: Consensus;
	riskScore: number;
	piiRedactions: PiiRedaction[];
	explain?: string;
}

export interface ThreatFilters {
	severity: Severity[];
	category: string | null;
	engine: string | null;
	search: string | null;
	threatsOnly: boolean;
	obfuscatedOnly: boolean;
	limit?: number;
}

export interface ThreatPage {
	items: Threat[];
	total: number;
	page: number;
	pages: number;
}

export interface ThreatPageResponse extends ThreatPage {
	source: "backend" | "local";
	fallbackActive: boolean;
	fallbackReason?: string;
}
