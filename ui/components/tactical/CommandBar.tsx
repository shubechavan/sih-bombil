"use client";

import { Tooltip } from "@/components/ui";
import type { ServiceStatus } from "@/types/health";
import { Menu } from "lucide-react";
import styles from "./CommandBar.module.css";

interface CommandBarProps {
	threatLevel?: "critical" | "high" | "medium" | "low";
	services?: Record<string, ServiceStatus>;
	onMenuToggle?: () => void;
}

export function CommandBar({ threatLevel = "low", services, onMenuToggle }: CommandBarProps) {
	return (
		<header className={styles.bar}>
			<div className={styles.left}>
				<button
					type="button"
					className={styles.menuBtn}
					onClick={onMenuToggle}
					aria-label="Toggle navigation"
				>
					<Menu size={20} />
				</button>
				<span className={styles.logo}>DarkSentinel</span>
				<span className={styles.threatLevel} data-level={threatLevel}>
					<span
						style={{
							width: 6,
							height: 6,
							borderRadius: "50%",
							background: "currentColor",
						}}
					/>
					{threatLevel} threat
				</span>
			</div>
			<div className={styles.right}>
				{services && (
					<div className={styles.services}>
						{Object.entries(services).map(([name, svc]) => (
							<Tooltip key={name} content={`${name}: ${svc.status}`}>
								<span className={styles.serviceDot} data-status={svc.status} />
							</Tooltip>
						))}
					</div>
				)}
			</div>
		</header>
	);
}
