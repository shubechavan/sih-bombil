"use client";

import {
	BandPill,
	ComponentBreakdown,
	EvidenceList,
	RefusalNotice,
} from "@/components/attribution";
import { TacticalPanel } from "@/components/tactical";
import { Button, TextArea } from "@/components/ui";
import type { AnalyseMatch, AnalyseResponse } from "@/lib/attribution";
import { ApiError, MIN_STYLOMETRY_CHARS, formatScore, postJson } from "@/lib/attribution";
import { useState } from "react";
import styles from "./analyze.module.css";

/**
 * Paste text, score it against every stored writeprint.
 *
 * The other four pages render what the pipeline already decided. This one takes
 * input from the room, which is the only way to show that the numbers elsewhere
 * are computed rather than recalled. Everything here is deliberately reused from
 * the actor profile — the same ComponentBreakdown, the same EvidenceList — so a
 * viewer can see that a pasted-text score is presented exactly like a stored
 * one, with the same refusals in the same places.
 */

/** ISO timestamps, one per line. Blank lines and stray whitespace are ignored. */
function parseTimestamps(raw: string): { values: string[]; bad: string[] } {
	const values: string[] = [];
	const bad: string[] = [];
	for (const line of raw.split("\n")) {
		const trimmed = line.trim();
		if (!trimmed) continue;
		if (Number.isNaN(Date.parse(trimmed))) bad.push(trimmed);
		else values.push(trimmed);
	}
	return { values, bad };
}

function MatchCard({ match }: { match: AnalyseMatch }) {
	return (
		<article className={styles.match}>
			<header className={styles.matchHead}>
				<div className={styles.matchWho}>
					<span className={styles.handle}>{match.handle}</span>
					{match.source_name && <span className={styles.source}>{match.source_name}</span>}
					{match.actor_label && <span className={styles.actor}>actor: {match.actor_label}</span>}
				</div>
				<div className={styles.matchScore}>
					<span className={styles.score} data-band={match.band}>
						{formatScore(match.score)}
					</span>
					<BandPill band={match.band} />
				</div>
			</header>
			<ComponentBreakdown components={match.components} />
			<EvidenceList evidence={match.evidence} />
		</article>
	);
}

export default function AnalyzePage() {
	const [text, setText] = useState("");
	const [stamps, setStamps] = useState("");
	const [categories, setCategories] = useState("");
	const [result, setResult] = useState<AnalyseResponse | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);

	const { values: parsedStamps, bad } = parseTimestamps(stamps);

	async function run(event: React.FormEvent) {
		event.preventDefault();
		if (!text.trim()) {
			setError("Paste some text first.");
			return;
		}
		setLoading(true);
		setError(null);
		try {
			const payload = await postJson<AnalyseResponse>("/analyze", {
				text,
				posted_at: parsedStamps,
				categories: categories
					.split(",")
					.map((c) => c.trim())
					.filter(Boolean),
				limit: 20,
			});
			setResult(payload);
		} catch (err) {
			// A 422 here is not a bug: it is the engine refusing a paste with
			// neither enough prose nor any timestamp. Show its sentence in full.
			setError(err instanceof ApiError ? err.message : "Analysis failed");
			setResult(null);
		} finally {
			setLoading(false);
		}
	}

	return (
		<div className={styles.page}>
			<header className={styles.header}>
				<h1 className={styles.title}>Analyze</h1>
				<p className={styles.lede}>
					Paste text from anywhere and score it against every persona in the corpus. The text is
					projected into the stored vocabulary by transform alone — it is never added to the corpus,
					and the fit is never rerun on it.
				</p>
			</header>

			<form className={styles.form} onSubmit={run}>
				<TacticalPanel title="Text to attribute">
					<TextArea
						label="Prose"
						value={text}
						minUseful={MIN_STYLOMETRY_CHARS}
						onChange={(e) => setText(e.target.value)}
						className={styles.paste}
						placeholder="Paste a vendor bio, a forum post, a listing…"
					/>
					<div className={styles.grid}>
						<TextArea
							label="Posting times (ISO 8601, one per line)"
							value={stamps}
							onChange={(e) => setStamps(e.target.value)}
							className={styles.stamps}
							placeholder={"2024-03-02T22:14:00\n2024-03-03T23:41:00"}
						/>
						<TextArea
							label="Categories (comma separated, optional)"
							value={categories}
							onChange={(e) => setCategories(e.target.value)}
							className={styles.stamps}
							placeholder="drugs, docs"
						/>
					</div>
					{bad.length > 0 && (
						<p className={styles.warn} role="alert">
							Ignoring {bad.length} unparseable timestamp{bad.length === 1 ? "" : "s"}:{" "}
							{bad.slice(0, 3).join(", ")}
						</p>
					)}
					<div className={styles.actions}>
						<span className={styles.hint}>
							{parsedStamps.length === 0
								? "No timestamps — behaviour will be reported as not assessed."
								: `${parsedStamps.length} timestamp${parsedStamps.length === 1 ? "" : "s"} will drive the behavioural term.`}
						</span>
						<Button type="submit" loading={loading}>
							Analyze
						</Button>
					</div>
				</TacticalPanel>
			</form>

			{error && (
				<p className={styles.error} role="alert">
					{error}
				</p>
			)}

			{result && (
				<>
					<TacticalPanel title="What was measurable about this text">
						<dl className={styles.facts}>
							<div>
								<dt>Prose after masking</dt>
								<dd>
									{result.char_count} characters
									{result.masked_chars > 0 && (
										<span className={styles.sub}>
											{" "}
											({result.masked_chars} masked as identifiers)
										</span>
									)}
								</dd>
							</div>
							<div>
								<dt>Extractor version</dt>
								<dd className={styles.mono}>{result.feature_version}</dd>
							</div>
						</dl>

						{!result.stylometry.measured && (
							<RefusalNotice
								title="Stylometry refused"
								reason={result.stylometry.reason}
								detail="Matches below are ranked on behaviour alone. This is the same rule that refuses persona 7."
							/>
						)}
						{!result.behaviour.measured && (
							<RefusalNotice title="Behaviour not assessed" reason={result.behaviour.reason} />
						)}
						<p className={styles.note}>{result.note}</p>
					</TacticalPanel>

					<TacticalPanel title={`Ranked matches (${result.matches.length})`}>
						{result.matches.length === 0 ? (
							<p className={styles.empty}>Nothing was comparable to this text.</p>
						) : (
							<div className={styles.matches}>
								{result.matches.map((match) => (
									<MatchCard key={match.persona_id} match={match} />
								))}
							</div>
						)}
					</TacticalPanel>

					{result.not_scored.length > 0 && (
						<TacticalPanel title={`Not scored (${result.not_scored.length})`}>
							<ul className={styles.notScored}>
								{result.not_scored.map((row) => (
									<li key={row.persona_id}>
										<span className={styles.handle}>{row.handle}</span>
										<span className={styles.reason}>{row.reason}</span>
									</li>
								))}
							</ul>
						</TacticalPanel>
					)}
				</>
			)}
		</div>
	);
}
