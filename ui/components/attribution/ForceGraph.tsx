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
import { Maximize2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
	CONFIRMED: "var(--ds-success, #4ade80)",
	PROBABLE: "var(--ds-high, #fb923c)",
	POSSIBLE: "var(--ds-medium, #f0b429)",
	WEAK: "var(--ds-low, #38bdf8)",
};

/** Distinct hues per source, so a cross-market link is visible as one. */
const SOURCE_COLOR = [
	"var(--ds-accent, #4da3ff)",
	"var(--ds-info, #a78bfa)",
	"var(--ds-low, #4ade80)",
	"var(--ds-medium, #f0b429)",
];

/** Pan/zoom clamp: near enough to 1:1 that the graph never shrinks to a dot
 *  or blows up past the panel, while still giving room to spread dense areas. */
const MIN_SCALE = 0.4;
const MAX_SCALE = 4;

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
	const svgRef = useRef<SVGSVGElement | null>(null);

	// Pan/zoom of the whole drawing, independent of the force layout itself.
	const [view, setView] = useState({ x: 0, y: 0, k: 1 });
	const panRef = useRef<{
		pointerId: number;
		startX: number;
		startY: number;
		origX: number;
		origY: number;
	} | null>(null);
	const dragRef = useRef<{ pointerId: number; node: PositionedNode } | null>(null);

	/** CSS pixels -> viewBox units. The SVG scales to its container's width
	 *  with height auto, so the ratio is uniform in both axes. */
	const pxToViewBox = () => {
		const rect = svgRef.current?.getBoundingClientRect();
		return rect && rect.width > 0 ? width / rect.width : 1;
	};

	/** Client coordinates -> the force simulation's own coordinate space,
	 *  undoing the pan/zoom transform so a dragged node lands under the pointer. */
	const clientToGraph = (clientX: number, clientY: number) => {
		const rect = svgRef.current?.getBoundingClientRect();
		if (!rect) return { x: 0, y: 0 };
		const scale = pxToViewBox();
		const vbX = (clientX - rect.left) * scale;
		const vbY = (clientY - rect.top) * scale;
		return { x: (vbX - view.x) / view.k, y: (vbY - view.y) / view.k };
	};

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
		setView({ x: 0, y: 0, k: 1 });

		simulation.on("tick", () => setTick((value) => value + 1));
		return () => {
			simulation.stop();
			simulation.on("tick", null);
		};
	}, [signature, width, height]);

	// React registers onWheel as a passive listener, so a handler passed as a
	// JSX prop cannot preventDefault the page scroll — it fires but does
	// nothing, and scrolling to zoom the graph scrolls the panel underneath it
	// too. A native listener with passive:false is the only way to stop that.
	//
	// A callback ref, not useEffect(() => svgRef.current..., [width]): the SVG
	// does not exist on the first render (nodesRef starts empty, so the "no
	// personas" placeholder renders instead), so an effect keyed on `width`
	// finds svgRef.current null, attaches nothing, and never runs again once
	// `width` stops changing — the listener silently never gets attached.
	// A callback ref fires exactly when the node itself is created or swapped.
	const wheelCleanupRef = useRef<(() => void) | null>(null);
	const attachSvgRef = useCallback(
		(node: SVGSVGElement | null) => {
			wheelCleanupRef.current?.();
			wheelCleanupRef.current = null;
			svgRef.current = node;
			if (!node) return;

			const onWheel = (event: WheelEvent) => {
				event.preventDefault();
				const rect = node.getBoundingClientRect();
				if (rect.width === 0) return;
				const scale = width / rect.width;
				const px = (event.clientX - rect.left) * scale;
				const py = (event.clientY - rect.top) * scale;
				const factor = Math.exp(-event.deltaY * 0.001);
				setView((prev) => {
					const k = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.k * factor));
					// Keep the point under the cursor fixed on screen.
					const x = px - ((px - prev.x) / prev.k) * k;
					const y = py - ((py - prev.y) / prev.k) * k;
					return { x, y, k };
				});
			};

			node.addEventListener("wheel", onWheel, { passive: false });
			wheelCleanupRef.current = () => node.removeEventListener("wheel", onWheel);
		},
		[width],
	);

	const handleBackgroundPointerDown = (event: React.PointerEvent<SVGRectElement>) => {
		event.currentTarget.setPointerCapture(event.pointerId);
		panRef.current = {
			pointerId: event.pointerId,
			startX: event.clientX,
			startY: event.clientY,
			origX: view.x,
			origY: view.y,
		};
	};

	const handleBackgroundPointerMove = (event: React.PointerEvent<SVGRectElement>) => {
		const pan = panRef.current;
		if (!pan || pan.pointerId !== event.pointerId) return;
		const scale = pxToViewBox();
		setView((prev) => ({
			...prev,
			x: pan.origX + (event.clientX - pan.startX) * scale,
			y: pan.origY + (event.clientY - pan.startY) * scale,
		}));
	};

	const endPan = (event: React.PointerEvent<SVGRectElement>) => {
		if (panRef.current?.pointerId !== event.pointerId) return;
		panRef.current = null;
		try {
			event.currentTarget.releasePointerCapture(event.pointerId);
		} catch {
			// Capture may already be gone (e.g. pointer cancelled); nothing to clean up.
		}
	};

	const beginNodeDrag = (node: PositionedNode) => (event: React.PointerEvent<SVGGElement>) => {
		event.stopPropagation();
		event.currentTarget.setPointerCapture(event.pointerId);
		dragRef.current = { pointerId: event.pointerId, node };
		node.fx = node.x;
		node.fy = node.y;
		simRef.current?.alphaTarget(0.3).restart();
	};

	const handleNodeDrag = (event: React.PointerEvent<SVGGElement>) => {
		const drag = dragRef.current;
		if (!drag || drag.pointerId !== event.pointerId) return;
		const { x, y } = clientToGraph(event.clientX, event.clientY);
		drag.node.fx = x;
		drag.node.fy = y;
		// The simulation is between ticks (or already at rest), so the pinned
		// node needs its own redraw rather than waiting for the next tick.
		drag.node.x = x;
		drag.node.y = y;
		setTick((value) => value + 1);
	};

	const endNodeDrag = (event: React.PointerEvent<SVGGElement>) => {
		const drag = dragRef.current;
		if (!drag || drag.pointerId !== event.pointerId) return;
		drag.node.fx = null;
		drag.node.fy = null;
		simRef.current?.alphaTarget(0);
		dragRef.current = null;
		try {
			event.currentTarget.releasePointerCapture(event.pointerId);
		} catch {
			// Capture may already be gone; nothing to clean up.
		}
	};

	const positionedNodes = nodesRef.current;
	const positionedEdges = edgesRef.current;
	const nodeById = new Map(positionedNodes.map((node) => [node.id, node]));
	// positionedNodes is a copy taken when the simulation last (re)built, keyed
	// off `signature` — which tracks node ids and edge scores, not confidence.
	// Actor confidence loads on its own, slightly later, over a separate
	// fetch; without this fresh lookup a node drawn before that fetch resolves
	// would be stuck showing "–" forever, because nothing after the initial
	// copy would ever refresh it.
	const freshNodeById = new Map(nodes.map((node) => [node.id, node]));
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

	const zoomPct = Math.round(view.k * 100);
	const isDefaultView = view.x === 0 && view.y === 0 && view.k === 1;

	return (
		<div className={styles.canvas}>
			<div className={styles.toolbar}>
				<span className={styles.zoomReadout}>{zoomPct}%</span>
				<button
					type="button"
					className={styles.resetView}
					onClick={() => setView({ x: 0, y: 0, k: 1 })}
					disabled={isDefaultView}
					aria-label="Reset pan and zoom"
					title="Reset pan and zoom"
				>
					<Maximize2 size={13} aria-hidden="true" />
				</button>
			</div>
			<svg
				ref={attachSvgRef}
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
				}. Drag the background to pan, scroll to zoom, and drag a node to reposition it.`}
				data-tick={tick}
			>
				{/* Catches pan gestures on empty space. Sized to the viewBox, not the
			    laid-out nodes, so panning works from any point in the panel. */}
				<rect
					x={0}
					y={0}
					width={width}
					height={height}
					fill="transparent"
					className={styles.panSurface}
					onPointerDown={handleBackgroundPointerDown}
					onPointerMove={handleBackgroundPointerMove}
					onPointerUp={endPan}
					onPointerCancel={endPan}
				/>
				<g transform={`translate(${view.x}, ${view.y}) scale(${view.k})`}>
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
							const confidence = freshNodeById.get(node.id)?.actor_confidence ?? null;
							const actorBand = freshNodeById.get(node.id)?.actor_band ?? null;
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
									onPointerDown={beginNodeDrag(node)}
									onPointerMove={handleNodeDrag}
									onPointerUp={endNodeDrag}
									onPointerCancel={endNodeDrag}
								>
									<circle
										className={styles.nodeCircle}
										r={13}
										fill={colour}
										fillOpacity={0.22}
										stroke={colour}
										strokeWidth={1.5}
										style={{ color: colour }}
									/>
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
									{/* Every node carries a probability, even when that probability is
							    "none yet" — a single persona was never compared to anything,
							    which is a fact worth showing, not a blank. The figure is the same
							    actor confidence the Actors page reports for this persona's
							    cluster, never a number invented for the graph. */}
									<text
										className={styles.nodeScore}
										y={4}
										textAnchor="middle"
										fill={actorBand ? BAND_COLOR[actorBand] : "var(--ds-text-dim, #6b7280)"}
									>
										{confidence != null ? Math.round(confidence * 100) : "–"}
									</text>
									<text className={styles.label} y={30} textAnchor="middle">
										{node.handle}
									</text>
									<title>
										{[
											node.stylometry_refused
												? `${node.handle} — stylometry refused (too little text to build a writeprint)`
												: `${node.handle} — ${node.source_name ?? "unknown source"}`,
											confidence != null
												? `actor confidence ${(confidence * 100).toFixed(1)}% (${actorBand})`
												: "not merged with anything — no confidence to show",
										].join(" · ")}
									</title>
								</g>
							);
						})}
					</g>
				</g>
			</svg>
		</div>
	);
}
