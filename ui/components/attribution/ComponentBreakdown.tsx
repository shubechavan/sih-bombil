"use client";

import type { Component, ComponentKey, Components } from "@/lib/attribution";
import { COMPONENT_LABELS } from "@/lib/attribution";
import styles from "./ComponentBreakdown.module.css";

/**
 * The H / S / B / I breakdown for one link.
 *
 * This component exists to make one mistake impossible: rendering an unmeasured
 * term as a number. An unmeasured component gets no bar and no figure — it gets
 * the sentence the pipeline wrote about why it could not be assessed, in full.
 *
 * On this corpus that means every link shows I as prose rather than 0.00, which
 * is the Phase 3 result and the most defensible thing on the screen. It is
 * styled as a first-class row, not greyed-out filler.
 */

const ORDER: ComponentKey[] = ["H", "S", "B", "I"];

function Row({ term, component }: { term: ComponentKey; component: Component }) {
	const weight = component.weight === null ? "" : `×${component.weight.toFixed(2)}`;

	if (!component.measured) {
		return (
			<div className={styles.row} data-unmeasured="true">
				<div className={styles.head}>
					<span className={styles.term}>{term}</span>
					<span className={styles.label}>{COMPONENT_LABELS[term]}</span>
					<span className={styles.weight}>{weight}</span>
					<span className={styles.notAssessed}>NOT ASSESSED</span>
				</div>
				<p className={styles.reason}>{component.reason}</p>
			</div>
		);
	}

	const pct = Math.max(0, Math.min(100, component.value * 100));
	return (
		<div className={styles.row}>
			<div className={styles.head}>
				<span className={styles.term}>{term}</span>
				<span className={styles.label}>{COMPONENT_LABELS[term]}</span>
				<span className={styles.weight}>{weight}</span>
				<span className={styles.value}>{component.value.toFixed(3)}</span>
			</div>
			<div className={styles.track}>
				<div className={styles.fill} style={{ width: `${pct}%` }} />
			</div>
		</div>
	);
}

export function ComponentBreakdown({ components }: { components: Components }) {
	return (
		<div className={styles.wrap}>
			{ORDER.map((term) => (
				<Row key={term} term={term} component={components[term]} />
			))}
		</div>
	);
}
