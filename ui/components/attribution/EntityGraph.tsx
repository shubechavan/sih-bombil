"use client";

import type { EntityEdge, EntityNode, TrustEdge } from "@/lib/attribution";
import {
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
 * The identifier view of the same corpus.
 *
 * `ForceGraph` draws personas joined by attribution and answers "are these two
 * the same person?". This draws what was published: the identifier becomes a
 * node and personas hang off it, so a shared PGP key is a hub you can see
 * rather than a sentence inside an edge payload.
 *
 * A separate component rather than a mode on ForceGraph, for the same reason
 * `/graph/entity` is a separate endpoint: the two disagree about what a node
 * is. There, a node is a persona with a numeric id and an edge carries a
 * score. Here, a node has a string id and a kind, and an edge is an
 * observation with no score at all — so there is no thickness to vary and no
 * band to colour by. Sharing a renderer would mean branching on node shape in
 * every mark.
 */

type PositionedNode = EntityNode & SimulationNodeDatum;
type PositionedEdge = Omit<EntityEdge, "source" | "target"> & SimulationLinkDatum<PositionedNode>;

interface Props {
	nodes: EntityNode[];
	edges: EntityEdge[];
	/** Drawn underneath, grey and dashed, and excluded from the layout. */
	trustEdges?: TrustEdge[];
	selectedNode: EntityNode | null;
	onSelectNode: (node: EntityNode | null) => void;
	width?: number;
	height?: number;
}

/**
 * One colour per kind. Deliberately not the band palette: nothing here is
 * scored, and borrowing the attribution colours would make an observation look
 * like a confidence.
 */
const KIND_COLOR: Record<string, string> = {
	persona: "#7dd3fc",
	handle: "#c084fc",
	pgp: "#4ade80",
	wallet: "#fcd34d",
	email: "#fb923c",
	jabber: "#f472b6",
	session: "#a78bfa",
	telegram: "#38bdf8",
	onion_mirror: "#94a3b8",
};

const FALLBACK = "#94a3b8";

export function EntityGraph({
	nodes,
	edges,
	trustEdges = [],
	selectedNode,
	onSelectNode,
	width = 720,
	height = 460,
}: Props) {
	const simRef = useRef<ReturnType<typeof forceSimulation<PositionedNode>> | null>(null);
	const [, setTick] = useState(0);

	const positioned = useMemo<PositionedNode[]>(() => nodes.map((node) => ({ ...node })), [nodes]);

	const links = useMemo<PositionedEdge[]>(
		() => edges.map((edge) => ({ ...edge })) as unknown as PositionedEdge[],
		[edges],
	);

	// Restart only when the shape of the data changes, not on every render —
	// otherwise the layout resets mid-settle and the graph never stops moving.
	const signature = useMemo(
		() => `${nodes.map((n) => n.id).join(",")}|${edges.length}`,
		[nodes, edges],
	);

	// The signature is the dependency on purpose: listing `positioned`/`links`
	// would restart the layout on every render and the graph would never settle.
	// biome-ignore lint/correctness/useExhaustiveDependencies: explained above
	useEffect(() => {
		const simulation = forceSimulation<PositionedNode>(positioned)
			.force(
				"link",
				forceLink<PositionedNode, PositionedEdge>(links)
					.id((node) => node.id)
					// Hubs sit further from their personas so a node with four
					// edges reads as a hub rather than a tangle.
					.distance((link) => {
						const target = link.target as PositionedNode;
						return target?.shared ? 90 : 55;
					})
					.strength(0.5),
			)
			.force("charge", forceManyBody<PositionedNode>().strength(-190))
			.force("center", forceCenter(width / 2, height / 2))
			.force(
				"collide",
				forceCollide<PositionedNode>((node) => (node.kind === "persona" ? 26 : 20)),
			);

		simRef.current = simulation;
		simulation.on("tick", () => setTick((value) => value + 1));
		return () => {
			simulation.stop();
			simulation.on("tick", null);
		};
	}, [signature, width, height]);

	const byId = useMemo(() => {
		const map = new Map<string, PositionedNode>();
		for (const node of positioned) map.set(node.id, node);
		return map;
	}, [positioned]);

	return (
		<svg
			className={styles.svg}
			viewBox={`0 0 ${width} ${height}`}
			role="img"
			aria-label={`Entity graph: ${nodes.length} nodes, ${edges.length} edges`}
		>
			<title>
				Personas and the identifiers they published. Nodes touched by more than one persona are
				hubs.
			</title>

			{/* Trust edges first, so they sit beneath everything else. */}
			{trustEdges.map((edge) => {
				const a = byId.get(`persona:${edge.persona_a}`);
				const b = byId.get(`persona:${edge.persona_b}`);
				if (!a?.x || !b?.x) return null;
				return (
					<line
						key={`trust-${edge.persona_a}-${edge.persona_b}`}
						x1={a.x}
						y1={a.y}
						x2={b.x}
						y2={b.y}
						stroke={FALLBACK}
						strokeWidth={1}
						strokeDasharray="4 4"
						strokeOpacity={0.45}
					/>
				);
			})}

			{links.map((link, index) => {
				const source = link.source as PositionedNode;
				const target = link.target as PositionedNode;
				if (!source?.x || !target?.x) return null;
				const dimmed =
					selectedNode !== null && selectedNode.id !== source.id && selectedNode.id !== target.id;
				return (
					<line
						key={`edge-${index}-${link.relation}`}
						x1={source.x}
						y1={source.y}
						x2={target.x}
						y2={target.y}
						stroke={KIND_COLOR[target.kind] ?? FALLBACK}
						strokeWidth={target.shared ? 1.8 : 1}
						strokeOpacity={dimmed ? 0.12 : 0.5}
						// A derived handle is our inference, not something the
						// actor published; dashing it keeps the two apart.
						strokeDasharray={link.relation === "normalises to" ? "3 3" : undefined}
					/>
				);
			})}

			{positioned.map((node) => {
				if (node.x === undefined || node.y === undefined) return null;
				const colour = KIND_COLOR[node.kind] ?? FALLBACK;
				const selected = selectedNode?.id === node.id;
				const dimmed = selectedNode !== null && !selected;
				const radius = node.kind === "persona" ? 11 : node.shared ? 10 : 6;
				return (
					<g
						key={node.id}
						transform={`translate(${node.x}, ${node.y})`}
						opacity={dimmed ? 0.35 : 1}
						onClick={() => onSelectNode(selected ? null : node)}
						onKeyDown={(event) => {
							if (event.key === "Enter" || event.key === " ") {
								event.preventDefault();
								onSelectNode(selected ? null : node);
							}
						}}
						tabIndex={0}
						// biome-ignore lint/a11y/useSemanticElements: SVG has no <button>;
						role="button"
						aria-label={`${node.kind} ${node.label}${
							node.shared ? `, shared by ${node.personas.length} personas` : ""
						}`}
						className={styles.node}
					>
						<circle
							r={radius}
							fill={colour}
							fillOpacity={node.kind === "persona" ? 0.3 : 0.22}
							stroke={colour}
							strokeWidth={selected ? 2.5 : node.shared ? 2 : 1}
						/>
						{/* A hub earns a second ring. It is the one thing this
						    view exists to make visible at a glance. */}
						{node.shared && node.kind !== "persona" && (
							<circle
								r={radius + 4}
								fill="none"
								stroke={colour}
								strokeWidth={1}
								strokeOpacity={0.55}
							/>
						)}
						<text className={styles.label} y={radius + 12} textAnchor="middle" fill="currentColor">
							{node.label}
						</text>
					</g>
				);
			})}
		</svg>
	);
}
