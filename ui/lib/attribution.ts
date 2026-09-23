/**
 * attribution.ts — types and fetch helpers for the Phase 4 attribution API.
 *
 * The important type here is `Component`. Every score the backend serves is
 * either a measured value or the reason it could not be measured, and the
 * discriminated union below means TypeScript will not let a component be
 * rendered as a number without checking which one it is. That is deliberate:
 * showing `0.00` for an unmeasured term asserts something the engine spent a
 * whole phase proving false.
 */

export type Band = "CONFIRMED" | "PROBABLE" | "POSSIBLE" | "WEAK";
export type ComponentKey = "H" | "S" | "B" | "I";

export type Component =
	| { measured: true; value: number; reason: null; weight: number | null }
	| { measured: false; value: null; reason: string; weight: number | null };

export type Components = Record<ComponentKey, Component>;

export interface EvidenceEntry {
	type: string;
	detail: string;
	weight: number | null;
	identifier_type: string | null;
	value: string | null;
	component: string | null;
	label: string | null;
	/** null for non-identifier evidence; false when the corpus declares a value no extractor found. */
	derived: boolean | null;
	extra: Record<string, unknown>;
}

export interface Identifier {
	type: string;
	value: string;
	value_norm: string | null;
	validated: boolean;
	derived: boolean;
	first_seen: string | null;
	last_seen: string | null;
}

export interface PostSample {
	title: string | null;
	body: string | null;
	category: string | null;
	posted_at: string | null;
}

export interface PersonaSummary {
	id: number;
	handle: string;
	handle_normalized: string | null;
	source_id: number | null;
	source_name: string | null;
	category: string | null;
	post_count: number;
	stylometry_refused: boolean;
	stylometry_refused_reason: string | null;
	char_count: number | null;
	/**
	 * How much the source this persona was collected from is trusted, 0..1.
	 * Displayed and used to order the scheduler's visits. It does NOT weight
	 * any score — measured, and it made the separation margin worse. See
	 * scripts/measure_reliability.py.
	 */
	source_reliability: number | null;
}

export interface PersonaDetail extends PersonaSummary {
	bio: string | null;
	identifiers: Identifier[];
	posts: PostSample[];
	first_seen: string | null;
	last_seen: string | null;
}

export interface LinkSummary {
	persona_a: number;
	persona_b: number;
	handle_a: string | null;
	handle_b: string | null;
	score: number;
	band: Band;
	method: string | null;
	components: Components;
	evidence: EvidenceEntry[];
	computed_at: string | null;
}

export interface ActorSummary {
	id: number;
	label: string | null;
	category: string | null;
	persona_count: number;
	source_count: number;
	/** null for a single-persona actor: it was never merged, so there is no band. */
	band: Band | null;
	confidence: number | null;
	identifier_count: number;
	first_seen: string | null;
	last_seen: string | null;
	handles: string[];
}

export interface TimelineBucket {
	bucket: string;
	posts: number;
	personas: number;
}

export interface ActorDetail extends ActorSummary {
	notes: string | null;
	personas: PersonaDetail[];
	links: LinkSummary[];
	trust_edges: TrustEdge[];
	trust_note: string;
	timeline: TimelineBucket[];
	/** Pointers outside Tor. Investigative context, never part of the score. */
	leads: LeadEntry[];
	leads_note: string;
	profile: ActorProfileEntry | null;
}

/**
 * One pointer at the world outside Tor. Bands are qualitative on purpose:
 * there is no ground truth linking a persona to a real-world identity, so a
 * decimal here would have the shape of a measurement and none of the
 * substance. See link/leads.py.
 */
export interface LeadEntry {
	kind: string;
	value: string;
	band: "STRONG" | "MODERATE" | "WEAK";
	why: string;
	caveat: string;
	/** "actor" — they published it. "source" — infrastructure they merely sit on. */
	scope: "actor" | "source";
	personas: number[];
	url: string | null;
	detail: Record<string, unknown>;
}

/**
 * A behavioural profile and, crucially, which kind it is. `kind` and `label`
 * both arrive from the server rather than being reconstructed here: a
 * renderer that decides for itself whether prose came from a model will
 * eventually decide wrong, and a template shown as AI output is invisible to
 * the person reading it.
 */
export interface ActorProfileEntry {
	text: string;
	kind: "ai" | "rule-based";
	label: string;
	model: string | null;
	provider: string | null;
	affects_score: false;
}

/**
 * Two vendors rated by the same buyers. Deliberately NOT a GraphEdge: no
 * score, no band, no components, so nothing can render it as attribution or
 * add it to one. Measured against ground truth, buyer overlap separates true
 * pairs from false ones worse than chance (ROC-AUC 0.389) — `affects_score`
 * is always false and `note` says so on every row.
 */
export interface TrustEdge {
	persona_a: number;
	persona_b: number;
	handle_a: string;
	handle_b: string;
	shared_buyers: string[];
	shared_count: number;
	buyers_a: number;
	buyers_b: number;
	overlap: number;
	detail: string;
	affects_score: boolean;
	note: string;
}

export interface GraphNode {
	id: number;
	handle: string;
	source_id: number | null;
	source_name: string | null;
	category: string | null;
	actor_id: number | null;
	stylometry_refused: boolean;
}

/**
 * A node in the entity view: a persona, or something a persona published.
 * `id` is a string (`persona:3`, `pgp:9A1B…`) because these nodes are not all
 * rows in one table — which is why this is a separate type from GraphNode
 * rather than a widening of it.
 */
export interface EntityNode {
	id: string;
	kind: string;
	label: string;
	value: string;
	personas: number[];
	/** Touched by more than one persona. The hubs are the point of the view. */
	shared: boolean;
	detail: Record<string, unknown>;
}

export interface EntityEdge {
	source: string;
	target: string;
	relation: "published" | "normalises to";
}

export interface EntityGraphPayload {
	nodes: EntityNode[];
	edges: EntityEdge[];
	trust_edges: TrustEdge[];
	trust_note: string;
	hub_count: number;
	note: string;
}

export interface GraphEdge {
	source: number;
	target: number;
	score: number;
	band: Band;
	method: string | null;
	components: Components;
	evidence: EvidenceEntry[];
}

export interface GraphPayload {
	nodes: GraphNode[];
	edges: GraphEdge[];
	trust_edges: TrustEdge[];
	trust_note: string;
	min_score: number;
	note: string;
}

export interface Correlation {
	clearnet_host: string;
	clearnet_ip: string | null;
	clearnet_port: number | null;
	match_type: string;
	score: number;
	provider: string | null;
	evidence: EvidenceEntry[];
	observed_at: string | null;
}

export interface ReconReport {
	onion_url: string;
	source_id: number | null;
	source_name: string | null;
	server_banner: string | null;
	powered_by: string | null;
	etag: string | null;
	favicon_hash: string | null;
	status_exposed: boolean;
	default_page: boolean;
	dir_listing: boolean;
	tls_subject: string | null;
	tls_issuer: string | null;
	tls_serial: string | null;
	tls_sans: string[];
	robots_txt: string | null;
	sitemap_xml: string | null;
	html_comments: string[];
	generator_meta: string | null;
	clearnet_refs: string[];
	headers: Record<string, unknown>;
	header_order: string[];
	misconfig_score: number | null;
	scanned_at: string | null;
	correlations: Correlation[];
	attribution_note: string;
}

/** One persona scored against pasted text. Same Components union as a link. */
export interface AnalyseMatch {
	persona_id: number;
	handle: string;
	source_name: string | null;
	actor_id: number | null;
	actor_label: string | null;
	score: number;
	band: Band;
	components: Components;
	evidence: EvidenceEntry[];
}

export interface AnalyseResponse {
	feature_version: string;
	/** Characters of prose after identifier masking — what the floor tested. */
	char_count: number;
	masked_chars: number;
	stylometry: Component;
	behaviour: Component;
	matches: AnalyseMatch[];
	not_scored: { persona_id: number; handle: string; reason: string }[];
	note: string;
}

export interface AnalyseRequest {
	text: string;
	posted_at?: string[];
	categories?: string[];
	limit?: number;
}

/** Who is signed in, and what this role may do. From GET /auth/me. */
export interface Identity {
	username: string;
	role: "analyst" | "admin";
	can_scan: boolean;
	can_read_audit: boolean;
}

export interface AuditRow {
	id: number;
	operator_id: string;
	role: string | null;
	method: string;
	path: string;
	query: string | null;
	status: number | null;
	action_hash: string | null;
	at: string | null;
}

export interface AuditScanRow {
	id: number;
	operator_id: string | null;
	mode: string | null;
	data_source: string | null;
	query: string | null;
	status: string | null;
	personas_new: number | null;
	links_new: number | null;
	action_hash: string | null;
	job_id: string | null;
	started_at: string | null;
	finished_at: string | null;
	error: string | null;
}

export interface AuditPage {
	requests: AuditRow[];
	scans: AuditScanRow[];
	total_requests: number;
	total_scans: number;
	operators: string[];
	note: string;
}

export const BANDS: Band[] = ["CONFIRMED", "PROBABLE", "POSSIBLE", "WEAK"];

/** The engine refuses stylometry below this many characters of masked prose. */
export const MIN_STYLOMETRY_CHARS = 300;

export const COMPONENT_LABELS: Record<ComponentKey, string> = {
	H: "hard identifiers",
	S: "stylometry",
	B: "behaviour",
	I: "infrastructure",
};

/** Maps an attribution band onto the existing severity tokens in styles/tokens.css. */
export const BAND_TOKEN: Record<Band, string> = {
	CONFIRMED: "critical",
	PROBABLE: "high",
	POSSIBLE: "medium",
	WEAK: "low",
};

export class ApiError extends Error {
	constructor(
		message: string,
		readonly status: number,
	) {
		super(message);
		this.name = "ApiError";
	}
}

/** Fetch through the Next route handlers, which proxy to the FastAPI backend. */
export async function api<T>(path: string, params?: Record<string, unknown>): Promise<T> {
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(params ?? {})) {
		if (value !== undefined && value !== null && value !== "") {
			query.set(key, String(value));
		}
	}
	const suffix = query.toString() ? `?${query}` : "";
	const res = await fetch(`/api/attribution${path}${suffix}`, { cache: "no-store" });
	if (!res.ok) {
		let detail = `Request failed (${res.status})`;
		try {
			const body = await res.json();
			detail = body?.detail ?? body?.error ?? detail;
		} catch {
			/* response was not JSON; keep the status message */
		}
		throw new ApiError(detail, res.status);
	}
	return (await res.json()) as T;
}

/** POST through the same proxy `api()` reads through. */
export async function postJson<T>(path: string, body: unknown): Promise<T> {
	const res = await fetch(`/api/attribution${path}`, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify(body),
		cache: "no-store",
	});
	if (!res.ok) {
		let detail = `Request failed (${res.status})`;
		try {
			const payload = await res.json();
			detail = payload?.detail ?? payload?.error ?? detail;
		} catch {
			/* response was not JSON; keep the status message */
		}
		throw new ApiError(detail, res.status);
	}
	return (await res.json()) as T;
}

export function formatScore(value: number | null | undefined): string {
	return value === null || value === undefined ? "—" : value.toFixed(3);
}

export function formatDate(value: string | null | undefined): string {
	if (!value) return "—";
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? "—" : date.toISOString().slice(0, 10);
}
