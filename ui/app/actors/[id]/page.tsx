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
	type ActorProfileEntry,
	type LeadEntry,
	type LinkSummary,
	type PersonaDetail,
	api,
	formatDate,
} from "@/lib/attribution";
import { ArrowLeft, Bot, ExternalLink, Fingerprint, Globe, NotebookPen } from "lucide-react";
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
					{persona.source_reliability !== null && (
						<>
							{" · "}
							<span
								className={styles.reliability}
								title="How much this source is trusted, 0–1. It orders which sources the scheduler visits first. It does not weight any score: weighting was measured and made the separation margin worse (scripts/measure_reliability.py)."
							>
								source reliability {persona.source_reliability.toFixed(2)}
							</span>
						</>
					)}
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

/**
 * The behavioural profile, with its provenance above it rather than beneath.
 * A reader who skims the prose and skips a footnote has been misled about
 * where it came from, so the label is the first thing in the panel and `kind`
 * comes from the server — this component never decides for itself.
 */
function ProfilePanel({ profile }: { profile: ActorProfileEntry }) {
	const isAi = profile.kind === "ai";
	return (
		<TacticalPanel
			title="Behavioural profile"
			subtitle="a description of stored features — not evidence, and not part of any score"
			icon={isAi ? <Bot size={20} /> : <NotebookPen size={20} />}
		>
			<p className={styles.profileLabel} data-kind={profile.kind}>
				{profile.label}
				{isAi && profile.model ? ` · ${profile.model}` : ""}
			</p>
			<p className={styles.profileText}>{profile.text}</p>
		</TacticalPanel>
	);
}

function LeadRow({ lead }: { lead: LeadEntry }) {
	return (
		<li className={styles.lead}>
			<div className={styles.leadHead}>
				<span className={styles.leadBand} data-band={lead.band}>
					{lead.band}
				</span>
				<span className={styles.leadKind}>{lead.kind.replace(/_/g, " ")}</span>
				{lead.url ? (
					<a className={styles.leadValue} href={lead.url} target="_blank" rel="noreferrer noopener">
						{lead.value}
						<ExternalLink size={11} aria-hidden="true" />
					</a>
				) : (
					<span className={styles.leadValue}>{lead.value}</span>
				)}
			</div>
			<p className={styles.leadWhy}>{lead.why}</p>
			{/* The caveat is not a tooltip. A lead read without what it does not
			    prove is the failure mode this whole panel exists to avoid. */}
			<p className={styles.leadCaveat}>{lead.caveat}</p>
		</li>
	);
}

function LeadsPanel({ leads, note }: { leads: LeadEntry[]; note: string }) {
	const groups: { scope: "actor" | "source"; heading: string }[] = [
		{ scope: "actor", heading: "Published by this actor" },
		{ scope: "source", heading: "Infrastructure behind the sites they post on" },
	];
	return (
		<TacticalPanel
			title="Clearnet leads"
			subtitle={`${leads.length} pointer(s) outside Tor — investigative leads, never conclusions`}
			icon={<Globe size={20} />}
		>
			<p className={styles.leadsNote}>{note}</p>
			{groups.map(({ scope, heading }) => {
				const group = leads.filter((lead) => lead.scope === scope);
				if (group.length === 0) return null;
				return (
					<section key={scope} className={styles.leadGroup}>
						<h4 className={styles.leadGroupTitle}>{heading}</h4>
						<ul className={styles.leadList}>
							{group.map((lead) => (
								<LeadRow key={`${lead.kind}-${lead.value}`} lead={lead} />
							))}
						</ul>
					</section>
				);
			})}
		</TacticalPanel>
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

			{actor?.profile && <ProfilePanel profile={actor.profile} />}

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

			{actor && actor.leads.length > 0 && (
				<LeadsPanel leads={actor.leads} note={actor.leads_note} />
			)}

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
