"use client";

import {
	BandPill,
	ComponentBreakdown,
	EvidenceList,
	ForceGraph,
	RefusalNotice,
} from "@/components/attribution";
import { TacticalPanel } from "@/components/tactical";
import {
	type ActorSummary,
	type GraphEdge,
	type GraphNode,
	type GraphPayload,
	api,
} from "@/lib/attribution";
import { Share2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import styles from "./graph.module.css";

export default function GraphPage() {
	const [payload, setPayload] = useState<GraphPayload | null>(null);
	const [minScore, setMinScore] = useState(0.45);
	const [selected, setSelected] = useState<GraphEdge | null>(null);
	const [showTrust, setShowTrust] = useState(true);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [actors, setActors] = useState<ActorSummary[]>([]);

	useEffect(() => {
		let cancelled = false;
		setLoading(true);
		api<GraphPayload>("/graph", { min_score: minScore })
			.then((data) => {
				if (!cancelled) {
					setPayload(data);
					setError(null);
					setSelected(null);
				}
			})
			.catch((err: Error) => !cancelled && setError(err.message))
			.finally(() => !cancelled && setLoading(false));
		return () => {
			cancelled = true;
		};
	}, [minScore]);

	// Every node's probability score is its actor's cluster confidence, not a
	// number invented for the graph — the same figure the Actors page already
	// shows, so a persona reads the same wherever it appears. Fetched once,
	// independent of min_score: the band filter only hides weak edges, it
	// does not change what any actor was resolved to.
	useEffect(() => {
		let cancelled = false;
		api<ActorSummary[]>("/actors", { min_personas: 1, limit: 500 })
			.then((rows) => !cancelled && setActors(rows))
			.catch(() => {
				// Best-effort enrichment — the graph is still fully usable without it.
			});
		return () => {
			cancelled = true;
		};
	}, []);

	const nodesWithConfidence: GraphNode[] = useMemo(() => {
		if (!payload) return [];
		const byActorId = new Map(actors.map((actor) => [actor.id, actor]));
		return payload.nodes.map((node) => {
			const actor = node.actor_id !== null ? byActorId.get(node.actor_id) : undefined;
			return {
				...node,
				actor_confidence: actor?.confidence ?? null,
				actor_band: actor?.band ?? null,
			};
		});
	}, [payload, actors]);

	const refused = payload?.nodes.filter((n) => n.stylometry_refused) ?? [];

	return (
		<div className={styles.page}>
			<header className={styles.header}>
				<h1 className={styles.title}>
					<Share2 size={20} /> Link graph
				</h1>
				<p className={styles.subtitle}>{payload?.note}</p>
			</header>

			<div className={styles.layout}>
				<TacticalPanel
					title="Personas"
					subtitle={
						payload
							? `${payload.nodes.length} nodes, ${payload.edges.length} edges at ≥ ${minScore.toFixed(2)}`
							: "loading"
					}
					loading={loading}
					headerRight={
						<label className={styles.slider}>
							<span>min score {minScore.toFixed(2)}</span>
							<input
								type="range"
								min={0}
								max={1}
								step={0.05}
								value={minScore}
								onChange={(e) => setMinScore(Number(e.target.value))}
							/>
						</label>
					}
				>
					{error ? (
						<p className={styles.error} role="alert">
							{error}
						</p>
					) : (
						payload && (
							<ForceGraph
								nodes={nodesWithConfidence}
								edges={payload.edges}
								trustEdges={showTrust ? payload.trust_edges : []}
								selectedEdge={selected}
								onSelectEdge={setSelected}
							/>
						)
					)}
					<div className={styles.legend}>
						<span>
							<i className={styles.swCritical} /> CONFIRMED
						</span>
						<span>
							<i className={styles.swHigh} /> PROBABLE
						</span>
						<span>
							<i className={styles.swMedium} /> POSSIBLE
						</span>
						<span>
							<i className={styles.swLow} /> WEAK
						</span>
						<span className={styles.legendNote}>
							thickness = score · number in node = actor confidence (– = not merged) · dashed ring =
							stylometry refused
						</span>
						{payload && payload.trust_edges.length > 0 && (
							<label className={styles.trustToggle}>
								<input
									type="checkbox"
									checked={showTrust}
									onChange={(e) => setShowTrust(e.target.checked)}
								/>
								<i className={styles.swTrust} /> shared buyers ({payload.trust_edges.length}) —
								context, not a score
							</label>
						)}
					</div>
				</TacticalPanel>

				<aside className={styles.side}>
					<TacticalPanel
						title="Evidence"
						subtitle={selected ? `${selected.score.toFixed(3)} ${selected.band}` : "click an edge"}
					>
						{selected ? (
							<div className={styles.detail}>
								<BandPill band={selected.band} score={selected.score} />
								<ComponentBreakdown components={selected.components} />
								<EvidenceList
									evidence={selected.evidence}
									omit={["component_breakdown", "component_not_assessed"]}
								/>
							</div>
						) : (
							<p className={styles.hint}>
								Each edge carries the reasons that produced it — shared identifiers, writeprint
								cosine, posting-hour overlap, and which components could not be assessed at all.
								Click one.
							</p>
						)}
					</TacticalPanel>

					{/* Shown whenever the edges are drawn. A dashed line on a link
					    graph reads as a weak link unless something says otherwise,
					    and here it means the opposite: measured and rejected. */}
					{showTrust && payload && payload.trust_edges.length > 0 && (
						<TacticalPanel
							title="Shared buyers"
							subtitle={`${payload.trust_edges.length} vendor pair(s) — no effect on any score`}
						>
							<p className={styles.trustNote}>{payload.trust_note}</p>
							<ul className={styles.trustList}>
								{payload.trust_edges.slice(0, 8).map((edge) => (
									<li key={`${edge.persona_a}-${edge.persona_b}`}>
										<strong>
											{edge.handle_a} ~ {edge.handle_b}
										</strong>
										<span>{edge.detail}</span>
									</li>
								))}
							</ul>
						</TacticalPanel>
					)}

					{refused.length > 0 && (
						<RefusalNotice
							title={`${refused.length} persona(s) refused by stylometry`}
							reason={`${refused.map((n) => n.handle).join(", ")} fell below the 300-character floor, so no writeprint was built and S is unmeasured on every pair they appear in.`}
							detail="Marked with a dashed ring in the graph. Their edges are thin because the engine declined to guess, not because it found them unalike."
						/>
					)}

					<p className={styles.footnote}>
						Nodes are personas. To see them grouped into actors, go to{" "}
						<Link href="/actors" className={styles.link}>
							Actors
						</Link>
						.
					</p>
				</aside>
			</div>
		</div>
	);
}
