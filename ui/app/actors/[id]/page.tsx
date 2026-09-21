"use client";

import {
	BandPill,
	ComponentBreakdown,
	EvidenceList,
	RefusalNotice,
	RefusedPill,
} from "@/components/attribution";
import { TacticalPanel } from "@/components/tactical";
import {
	type ActorDetail,
	type LinkSummary,
	type PersonaDetail,
	api,
	formatDate,
} from "@/lib/attribution";
import { ArrowLeft, Fingerprint } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import styles from "./actor.module.css";

function IdentifierChip({
	type,
	value,
	derived,
	validated,
}: {
	type: string;
	value: string;
	derived: boolean;
	validated: boolean;
}) {
	return (
		<span
			className={styles.chip}
			data-derived={derived ? "true" : "false"}
			title={
				derived
					? `Extracted from this persona's prose${validated ? " (checksum validated)" : ""}`
					: "Declared by the corpus but present in no bio and no post — no extractor could have produced this value. See docs/BUILD_PLAN.md, 'Known corpus gaps'."
			}
		>
			<span className={styles.chipType}>{type}</span>
			<span className={styles.chipValue}>{value}</span>
			{!derived && <span className={styles.chipFlag}>declared only</span>}
		</span>
	);
}

function PersonaCard({ persona }: { persona: PersonaDetail }) {
	return (
		<article className={styles.persona}>
			<header className={styles.personaHead}>
				<h3 className={styles.personaHandle}>{persona.handle}</h3>
				<span className={styles.personaSource}>
					{persona.source_name ?? "unknown source"} · {persona.post_count} posts
				</span>
				{persona.stylometry_refused && <RefusedPill reason={persona.stylometry_refused_reason} />}
			</header>

			{/* The floor refusal, shown as a result rather than an omission. */}
			{persona.stylometry_refused && (
				<RefusalNotice
					title="Stylometry declined to score this persona"
					reason={persona.stylometry_refused_reason}
					detail={`Every pair this persona appears in is scored with S unmeasured, and its weight redistributed, rather than with a number derived from ${persona.char_count ?? "too few"} characters.`}
				/>
			)}

			{persona.bio && <p className={styles.bio}>{persona.bio}</p>}

			{persona.identifiers.length > 0 && (
				<div className={styles.chips}>
					{persona.identifiers.map((identifier) => (
						<IdentifierChip
							key={`${identifier.type}-${identifier.value}`}
							type={identifier.type}
							value={identifier.value}
							derived={identifier.derived}
							validated={identifier.validated}
						/>
					))}
				</div>
			)}

			{persona.posts.length > 0 && (
				<details className={styles.posts}>
					<summary className={styles.postsSummary}>{persona.posts.length} post sample(s)</summary>
					{persona.posts.map((post, index) => (
						<blockquote key={`${persona.id}-${index}`} className={styles.post}>
							{post.title && <strong>{post.title}</strong>}
							<span className={styles.postMeta}>
								{formatDate(post.posted_at)} · {post.category ?? "uncategorised"}
							</span>
							<p>{post.body}</p>
						</blockquote>
					))}
				</details>
			)}
		</article>
	);
}

function LinkCard({ link }: { link: LinkSummary }) {
	return (
		<article className={styles.link}>
			<header className={styles.linkHead}>
				<span className={styles.linkPair}>
					{link.handle_a} <span className={styles.tilde}>~</span> {link.handle_b}
				</span>
				<BandPill band={link.band} score={link.score} />
			</header>
			<ComponentBreakdown components={link.components} />
			<h4 className={styles.evidenceTitle}>Why</h4>
			<EvidenceList
				evidence={link.evidence}
				omit={["component_breakdown", "component_not_assessed"]}
			/>
		</article>
	);
}

export default function ActorDetailPage({ params }: { params: { id: string } }) {
	const [actor, setActor] = useState<ActorDetail | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);

	useEffect(() => {
		let cancelled = false;
		setLoading(true);
		api<ActorDetail>(`/actors/${params.id}`)
			.then((data) => !cancelled && setActor(data))
			.catch((err: Error) => !cancelled && setError(err.message))
			.finally(() => !cancelled && setLoading(false));
		return () => {
			cancelled = true;
		};
	}, [params.id]);

	if (error) {
		return (
			<div className={styles.page}>
				<Link href="/actors" className={styles.back}>
					<ArrowLeft size={14} /> All actors
				</Link>
				<p className={styles.error} role="alert">
					{error}
				</p>
			</div>
		);
	}

	return (
		<div className={styles.page}>
			<Link href="/actors" className={styles.back}>
				<ArrowLeft size={14} /> All actors
			</Link>

			<header className={styles.header}>
				<h1 className={styles.title}>
					<Fingerprint size={20} />
					{actor?.label ?? (loading ? "Loading…" : `actor ${params.id}`)}
				</h1>
				{actor && (
					<div className={styles.meta}>
						<BandPill band={actor.band} score={actor.confidence} />
						<span>{actor.persona_count} personas</span>
						<span>{actor.source_count} sources</span>
						<span>{actor.category ?? "uncategorised"}</span>
						<span>
							{formatDate(actor.first_seen)} → {formatDate(actor.last_seen)}
						</span>
					</div>
				)}
				{actor?.notes && <p className={styles.notes}>{actor.notes}</p>}
			</header>

			<TacticalPanel
				title="Linked personas"
				subtitle="every persona this actor resolves to"
				loading={loading}
			>
				<div className={styles.personas}>
					{actor?.personas.map((persona) => (
						<PersonaCard key={persona.id} persona={persona} />
					))}
				</div>
			</TacticalPanel>

			<TacticalPanel
				title="Evidence"
				subtitle={
					actor && actor.links.length > 0
						? `${actor.links.length} link(s) merged these personas`
						: "no links inside this actor"
				}
				loading={loading}
			>
				{actor && actor.links.length === 0 ? (
					<RefusalNotice
						title="Never merged"
						reason="This actor is a single persona. No link reached the clustering threshold, so there is no merging evidence to show — and no confidence band, because nothing was compared."
					/>
				) : (
					<div className={styles.links}>
						{actor?.links.map((link) => (
							<LinkCard key={`${link.persona_a}-${link.persona_b}`} link={link} />
						))}
					</div>
				)}
			</TacticalPanel>

			{actor && actor.trust_edges.length > 0 && (
				<TacticalPanel
					title="Shared buyers"
					subtitle={`${actor.trust_edges.length} vendor pair(s) — relationship context, not evidence`}
				>
					{/* The caption sits above the rows on purpose. An overlap
					    figure on an actor profile reads as corroboration unless
					    the page says, before the numbers, that it is not. */}
					<p className={styles.trustNote}>{actor.trust_note}</p>
					<ul className={styles.trustList}>
						{actor.trust_edges.map((edge) => (
							<li key={`${edge.persona_a}-${edge.persona_b}`}>
								<div className={styles.trustHead}>
									<strong>
										{edge.handle_a} ~ {edge.handle_b}
									</strong>
									<span className={styles.trustCount}>{edge.shared_count} shared</span>
								</div>
								<span className={styles.trustDetail}>{edge.detail}</span>
							</li>
						))}
					</ul>
				</TacticalPanel>
			)}

			{actor && actor.timeline.length > 0 && (
				<TacticalPanel title="Activity" subtitle="posts per month">
					<div className={styles.timeline}>
						{actor.timeline.map((bucket) => {
							const peak = Math.max(...actor.timeline.map((b) => b.posts));
							return (
								<div key={bucket.bucket} className={styles.bar}>
									<div
										className={styles.barFill}
										style={{ height: `${(bucket.posts / peak) * 100}%` }}
										title={`${bucket.bucket}: ${bucket.posts} posts, ${bucket.personas} persona(s)`}
									/>
									<span className={styles.barLabel}>{bucket.bucket.slice(2)}</span>
								</div>
							);
						})}
					</div>
				</TacticalPanel>
			)}
		</div>
	);
}
