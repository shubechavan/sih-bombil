"use client";

import type { Band } from "@/lib/attribution";
import styles from "./BandPill.module.css";

/**
 * A confidence band, or the explicit absence of one.
 *
 * `band === null` means the actor was never merged with anything — a single
 * persona standing alone. That is not WEAK: WEAK would say we compared it to
 * something and were unconvinced. The pill renders "NOT MERGED" instead, which
 * is what actually happened.
 */
export function BandPill({ band, score }: { band: Band | null; score?: number | null }) {
	if (band === null) {
		return (
			<span
				className={styles.pill}
				data-band="none"
				title="Single persona — no link reached the clustering threshold, so there was nothing to score."
			>
				NOT MERGED
			</span>
		);
	}
	return (
		<span className={styles.pill} data-band={band}>
			<span className={styles.dot} />
			{band}
			{score !== undefined && score !== null && (
				<span className={styles.score}>{score.toFixed(3)}</span>
			)}
		</span>
	);
}

export function RefusedPill({ reason }: { reason: string | null }) {
	return (
		<span className={styles.pill} data-band="refused" title={reason ?? undefined}>
			STYLOMETRY REFUSED
		</span>
	);
}
