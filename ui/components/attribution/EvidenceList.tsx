"use client";

import type { EvidenceEntry } from "@/lib/attribution";
import styles from "./EvidenceList.module.css";

/**
 * A link's evidence, in the words the pipeline wrote it in.
 *
 * `detail` is rendered verbatim — never truncated, summarised or reformatted.
 * Those sentences were composed for an analyst to read ("same PGP fingerprint
 * A4F57A…", "posting-hour overlap 84% (peaks 00/01/03 vs 01/22/23 UTC)") and
 * rewriting them in the UI would quietly change what the system claims.
 *
 * Two entry kinds get extra treatment:
 *   - `component_not_assessed` is the refusal, so it is highlighted rather
 *     than buried at the bottom of the list where it naturally falls.
 *   - identifier evidence carries provenance: some fixture identifiers are
 *     declared by the corpus but appear in no bio or post, so a link citing one
 *     says so instead of implying the extractor found it.
 */

const KIND_LABEL: Record<string, string> = {
	shared_identifier: "identifier",
	handle_reuse: "handle",
	stylometry: "stylometry",
	behaviour: "behaviour",
	behaviour_score: "behaviour",
	infrastructure: "infrastructure",
	infra_score: "infrastructure",
	component_breakdown: "breakdown",
	component_not_assessed: "not assessed",
	transitive_path: "inferred path",
	not_directly_observed: "inferred",
	provenance: "provenance",
};

export function EvidenceList({
	evidence,
	omit = [],
}: {
	evidence: EvidenceEntry[];
	/**
	 * Entry types to leave out. Used where <ComponentBreakdown> is rendered
	 * directly above: it already shows every component's value or its reason, so
	 * repeating `component_breakdown` and `component_not_assessed` here is pure
	 * duplication on screen. Nothing is lost — the breakdown carries the same
	 * text — and the trail stays readable.
	 */
	omit?: string[];
}) {
	evidence = omit.length ? evidence.filter((e) => !omit.includes(e.type)) : evidence;
	if (evidence.length === 0) {
		return <p className={styles.empty}>No evidence recorded for this link.</p>;
	}

	return (
		<ul className={styles.list}>
			{evidence.map((entry, index) => {
				const unmeasured = entry.type === "component_not_assessed";
				return (
					<li
						// evidence has no stable id; order is fixed by the pipeline
						key={`${entry.type}-${index}`}
						className={styles.item}
						data-unmeasured={unmeasured ? "true" : undefined}
					>
						<span className={styles.kind}>{KIND_LABEL[entry.type] ?? entry.type}</span>
						<div className={styles.body}>
							<p className={styles.detail}>{entry.detail}</p>
							<div className={styles.tags}>
								{entry.weight !== null && !unmeasured && (
									<span className={styles.weight}>weight {entry.weight.toFixed(2)}</span>
								)}
								{entry.derived === false && (
									<span
										className={styles.declared}
										title="This value is declared by the corpus but appears in no bio and no post, so no extractor could have produced it. See docs/BUILD_PLAN.md."
									>
										declared only — not found in any text
									</span>
								)}
								{entry.derived === true && (
									<span className={styles.derived}>extracted from prose</span>
								)}
							</div>
						</div>
					</li>
				);
			})}
		</ul>
	);
}
