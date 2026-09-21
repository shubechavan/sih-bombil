"use client";

import type { GraphEdge, GraphNode, TrustEdge } from "@/lib/attribution";
import { BAND_TOKEN } from "@/lib/attribution";
import {
	type Simulation,
	type SimulationLinkDatum,
	type SimulationNodeDatum,
	forceCenter,
	forceCollide,
	forceLink,
	forceManyBody,
	forceSimulation,
} from "d3-force";
import { useEffect, useMemo, useRef, useState } from "react";
import styles from "./ForceGraph.module.css";

/**
 * Persona link graph. d3-force computes the layout; the SVG is ours.
 *
 * d3-force is a physics solver with no DOM of its own, so it does not bring a
 * component library or any styling with it — every mark below is drawn here and
 * themed from styles/tokens.css.
 *
 * Node = persona, never actor: the graph exists to show *why* personas were
 * merged, so collapsing them first would hide the evidence. Edge thickness is
 * the attribution score; colour is the band. Clicking an edge raises its
 * evidence rather than a tooltip with a number in it.
 */

type PositionedNode = GraphNode & SimulationNodeDatum;

/**
 * d3 rewrites `source`/`target` from ids to node objects while it runs, so the
 * wire shape (numbers) and the simulation shape (nodes) cannot be the same
 * type. Omitting them from GraphEdge and taking d3's definitions keeps the rest
 * of the edge — score, band, components, evidence — intact.
 */
type PositionedEdge = Omit<GraphEdge, "source" | "target"> & SimulationLinkDatum<PositionedNode>;

interface Props {
	nodes: GraphNode[];
	edges: GraphEdge[];
	/**
	 * Buyer-mediated context. A separate prop, not merged into `edges`, so
	 * they cannot be drawn with a band colour or a score-derived thickness —
	 * they have neither. Rendered underneath, grey, dashed and uniformly thin,
	 * and they take no part in the force layout: letting them pull nodes
	 * together would make the picture argue for a relationship the engine
	 * explicitly refused to score.
	 */
	trustEdges?: TrustEdge[];
	selectedEdge: GraphEdge | null;
	onSelectEdge: (edge: GraphEdge | null) => void;
	onSelectNode?: (node: GraphNode) => void;
	width?: number;
	height?: number;
}

const BAND_COLOR: Record<string, string> = {
	CONFIRMED: "var(--ds-critical, #f87171)",
	PROBABLE: "var(--ds-high, #fb923c)",
	POSSIBLE: "var(--ds-medium, #f0b429)",
	WEAK: "var(--ds-low, #4ade80)",
};

/** Distinct hues per source, so a cross-market link is visible as one. */
const SOURCE_COLOR = [
	"var(--ds-accent, #4da3ff)",
	"var(--ds-info, #a78bfa)",
	"var(--ds-low, #4ade80)",
	"var(--ds-medium, #f0b429)",
];

export function ForceGraph({
	nodes,
	edges,
	trustEdges = [],
	selectedEdge,
	onSelectEdge,
	onSelectNode,
	width = 900,
	height = 560,
}: Props) {
	const [tick, setTick] = useState(0);
	const simRef = useRef<Simulation<PositionedNode, PositionedEdge> | null>(null);
	const nodesRef = useRef<PositionedNode[]>([]);
	const edgesRef = useRef<PositionedEdge[]>([]);

	// Rebuild only when the graph's shape changes, not on every selection.
	const signature = useMemo(
		() =>
			`${nodes.map((n) => n.id).join(",")}|${edges
				.map((e) => `${e.source}-${e.target}-${e.score.toFixed(3)}`)
				.join(",")}`,
		[nodes, edges],
	);

	// `signature` already captures every change that should rebuild the
	// simulation; listing `nodes`/`edges` would restart the layout on each
	// parent re-render, and `tick` is this effect's output, not an input.
	// biome-ignore lint/correctness/useExhaustiveDependencies: explained above
	useEffect(() => {
		const positioned: PositionedNode[] = nodes.map((node) => ({ ...node }));
		const byId = new Map(positioned.map((node) => [node.id, node]));
		const positionedEdges: PositionedEdge[] = edges.flatMap((edge) => {
			const source = byId.get(edge.source as number);
			const target = byId.get(edge.target as number);
			// An edge naming a node we were not given is dropped rather than
			// drawn against undefined coordinates.
			return source && target ? [{ ...edge, source, target }] : [];
		});

		const simulation = forceSimulation<PositionedNode>(positioned)
			.force(
				"link",
				forceLink<PositionedNode, PositionedEdge>(positionedEdges)
					.id((node) => node.id)
					// A stronger link sits closer: distance falls as score rises.
					.distance((edge) => 200 - 120 * edge.score)
					.strength((edge) => 0.15 + 0.7 * edge.score),
			)
			.force("charge", forceManyBody<PositionedNode>().strength(-340))
			.force("center", forceCenter(width / 2, height / 2))
			.force("collide", forceCollide<PositionedNode>(34))
			.alphaDecay(0.035);

		nodesRef.current = positioned;
		edgesRef.current = positionedEdges;
		simRef.current = simulation;

		simulation.on("tick", () => setTick((value) => value + 1));
		return () => {
			simulation.stop();
			simulation.on("tick", null);
		};
	}, [signature, width, height]);

	const positionedNodes = nodesRef.current;
	const positionedEdges = edgesRef.current;
	const nodeById = new Map(positionedNodes.map((node) => [node.id, node]));
	// Resolved against the laid-out nodes at render time rather than fed to the
	// simulation, which is what keeps them out of the layout.
	const drawnTrust = trustEdges.flatMap((edge) => {
		const a = nodeById.get(edge.persona_a);
		const b = nodeById.get(edge.persona_b);
		return a && b ? [{ edge, a, b }] : [];
	});
	const selectedKey = selectedEdge ? `${selectedEdge.source}-${selectedEdge.target}` : null;

	if (positionedNodes.length === 0) {
		return <p className={styles.empty}>No personas to draw.</p>;
	}

	return (
		<svg
			className={styles.svg}
			viewBox={`0 0 ${width} ${height}`}
			// role="group", not role="img". An image is a single opaque thing,
			// so declaring one and then putting focusable edges inside it is a
			// contradiction — axe reports it as nested-interactive. This graph is
			// genuinely both a picture and a set of controls, and group is the
			// role that admits that.
			//
			// biome-ignore lint/a11y/useSemanticElements: the semantic element the
			// rule wants is <fieldset>, which cannot contain SVG shapes. Same
			// reason as the role="button" edges below.
			role="group"
			aria-label={`Persona link graph: ${positionedNodes.length} personas, ${positionedEdges.length} attribution links${
				drawnTrust.length > 0
					? `, and ${drawnTrust.length} dashed shared-buyer relationships which carry no score`
					: ""
			}`}
			data-tick={tick}
		>
			{/* First, so attribution edges and nodes paint over them. */}
			<g>
				{drawnTrust.map(({ edge, a, b }) => (
					<line
						key={`trust-${edge.persona_a}-${edge.persona_b}`}
						x1={a.x ?? 0}
						y1={a.y ?? 0}
						x2={b.x ?? 0}
						y2={b.y ?? 0}
						stroke="var(--ds-muted, #8b93a5)"
						strokeWidth={1}
						strokeOpacity={0.35}
						strokeDasharray="2 5"
					>
						<title>
							{`${edge.handle_a} ~ ${edge.handle_b} — ${edge.shared_count} shared buyer(s). Context only: shared buyers are not evidence of identity and do not affect the score.`}
						</title>
					</line>
				))}
			</g>
			<g>
				{positionedEdges.map((edge) => {
					const source = edge.source as PositionedNode;
					const target = edge.target as PositionedNode;
					const key = `${source.id}-${target.id}`;
					const active = key === selectedKey;
					const select = () =>
						onSelectEdge(active ? null : { ...edge, source: source.id, target: target.id });
					return (
						<line
							key={key}
							x1={source.x ?? 0}
							y1={source.y ?? 0}
							x2={target.x ?? 0}
							y2={target.y ?? 0}
							stroke={BAND_COLOR[edge.band] ?? "var(--ds-border, #3a3f4b)"}
							// Thickness is the score, which is the whole point of the view.
							strokeWidth={1 + edge.score * 6}
							strokeOpacity={active ? 1 : 0.55}
							className={styles.edge}
							data-active={active ? "true" : undefined}
							// biome-ignore lint/a11y/useSemanticElements: SVG has no <button>;
							// a role is the only way to make a drawn edge operable.
							role="button"
							tabIndex={0}
							// Hand back the wire shape, with ids, not d3's mutated node refs.
							onClick={() => select()}
							onKeyDown={(event) => {
								if (event.key === "Enter" || event.key === " ") {
									event.preventDefault();
									select();
								}
							}}
						>
							<title>
								{`${source.handle} ~ ${target.handle} — ${edge.band} ${edge.score.toFixed(3)} (click for evidence)`}
							</title>
						</line>
					);
				})}
			</g>
			<g>
				{positionedNodes.map((node) => {
					const colour =
						SOURCE_COLOR[(node.source_id ?? 0) % SOURCE_COLOR.length] ?? SOURCE_COLOR[0];
					return (
						<g
							key={node.id}
							transform={`translate(${node.x ?? 0}, ${node.y ?? 0})`}
							className={styles.node}
							role={onSelectNode ? "button" : undefined}
							tabIndex={onSelectNode ? 0 : undefined}
							onClick={() => onSelectNode?.(node)}
							onKeyDown={(event) => {
								if (onSelectNode && (event.key === "Enter" || event.key === " ")) {
									event.preventDefault();
									onSelectNode(node);
								}
							}}
						>
							<circle r={13} fill={colour} fillOpacity={0.22} stroke={colour} strokeWidth={1.5} />
							{/* A persona stylometry refused is marked on the node itself, so a
							    reader sees the refusal rather than a node with thin edges. */}
							{node.stylometry_refused && (
								<circle
									r={18}
									fill="none"
									stroke="var(--ds-medium, #f0b429)"
									strokeWidth={1.5}
									strokeDasharray="3 3"
								/>
							)}
							<text className={styles.label} y={30} textAnchor="middle">
								{node.handle}
							</text>
							<title>
								{node.stylometry_refused
									? `${node.handle} — stylometry refused (too little text to build a writeprint)`
									: `${node.handle} — ${node.source_name ?? "unknown source"}`}
							</title>
						</g>
					);
				})}
			</g>
		</svg>
	);
}
