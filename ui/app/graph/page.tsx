"use client";

import {
	BandPill,
	ComponentBreakdown,
	EvidenceList,
	ForceGraph,
	RefusalNotice,
} from "@/components/attribution";
import { TacticalPanel } from "@/components/tactical";
import { type GraphEdge, type GraphPayload, api } from "@/lib/attribution";
import { Share2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import styles from "./graph.module.css";

export default function GraphPage() {
	const [payload, setPayload] = useState<GraphPayload | null>(null);
	const [minScore, setMinScore] = useState(0.45);
	const [selected, setSelected] = useState<GraphEdge | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

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
						<p className={styles.error}>{error}</p>
					) : (
						payload && (
							<ForceGraph
								nodes={payload.nodes}
								edges={payload.edges}
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
							thickness = score · dashed ring = stylometry refused
						</span>
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
