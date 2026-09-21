"use client";

import { Tooltip } from "@/components/ui";
import type { Identity } from "@/lib/attribution";
import type { ServiceStatus } from "@/types/health";
import { LogOut, Menu } from "lucide-react";
import styles from "./CommandBar.module.css";

interface CommandBarProps {
	threatLevel?: "critical" | "high" | "medium" | "low";
	services?: Record<string, ServiceStatus>;
	onMenuToggle?: () => void;
	identity?: Identity | null;
	onSignOut?: () => void;
}

export function CommandBar({
	threatLevel = "low",
	services,
	onMenuToggle,
	identity,
	onSignOut,
}: CommandBarProps) {
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
								{/* The dot is colour-only, so carry the state in text for
								    anyone who cannot see or hover it. */}
								<span className={styles.serviceDot} data-status={svc.status}>
									<span className="sr-only">{`${name}: ${svc.status}`}</span>
								</span>
							</Tooltip>
						))}
					</div>
				)}
				{identity && (
					<div className={styles.operator}>
						{/* Whose name is on every audit row this session writes. */}
						<span className={styles.operatorName}>{identity.username}</span>
						<span className={styles.operatorRole}>{identity.role}</span>
						<button
							type="button"
							className={styles.signOut}
							onClick={onSignOut}
							aria-label={`Sign out ${identity.username}`}
						>
							<LogOut size={16} aria-hidden="true" />
						</button>
					</div>
				)}
			</div>
		</header>
	);
}
