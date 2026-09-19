"use client";

import type { ReactNode } from "react";
import styles from "./Tooltip.module.css";

interface TooltipProps {
	content: string;
	children: ReactNode;
}

export function Tooltip({ content, children }: TooltipProps) {
	return (
		<span className={styles.wrapper}>
			{children}
			<span className={styles.tip} role="tooltip">
				{content}
			</span>
		</span>
	);
}
