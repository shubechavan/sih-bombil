"use client";

import type { Severity } from "@/types/threat";
import type { LucideIcon } from "lucide-react";
import styles from "./StatCard.module.css";

interface StatCardProps {
	label: string;
	value: string | number;
	severity?: Lowercase<Severity>;
	delta?: { value: string; trend: "up" | "down" | "flat" };
	icon?: LucideIcon;
	onClick?: () => void;
}

export function StatCard({ label, value, severity, delta, icon: Icon, onClick }: StatCardProps) {
	return (
		<div
			className={styles.card}
			data-severity={severity}
			data-clickable={!!onClick}
			onClick={onClick}
			role={onClick ? "button" : undefined}
			tabIndex={onClick ? 0 : undefined}
			// A native button activates on both Enter and Space. Handling only
			// Enter makes this look operable and behave like half a control;
			// Space also needs preventDefault or it scrolls the page instead.
			onKeyDown={
				onClick
					? (e) => {
							if (e.key === "Enter" || e.key === " ") {
								e.preventDefault();
								onClick();
							}
						}
					: undefined
			}
		>
			<div className={styles.row}>
				<span className={styles.label}>{label}</span>
				{Icon && (
					<span className={styles.icon}>
						<Icon size={16} />
					</span>
				)}
			</div>
			<span className={styles.value} data-severity={severity}>
				{value}
			</span>
			{delta && (
				<span className={styles.delta} data-trend={delta.trend}>
					{delta.trend === "up" ? "▲" : delta.trend === "down" ? "▼" : "—"} {delta.value}
				</span>
			)}
		</div>
	);
}
