"use client";

import styles from "./RefusalNotice.module.css";

/**
 * A place where the engine declined to produce a number, shown as prominently
 * as a result would be.
 *
 * Used for the stylometry floor (a persona with too little text) and for the
 * recon page's note that its findings are site-level and therefore do not feed
 * the I term. Both are refusals, and refusals are the strongest thing this
 * system does — a tool that only ever says yes is a tool that is guessing. They
 * do not belong in small grey type at the bottom of a panel.
 */
export function RefusalNotice({
	title,
	reason,
	detail,
}: {
	title: string;
	reason: string | null;
	detail?: string;
}) {
	return (
		<div className={styles.notice} role="note">
			<div className={styles.head}>
				<span className={styles.marker}>◆</span>
				<span className={styles.title}>{title}</span>
			</div>
			{reason && <p className={styles.reason}>{reason}</p>}
			{detail && <p className={styles.detail}>{detail}</p>}
		</div>
	);
}
