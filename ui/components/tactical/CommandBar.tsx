"use client";

import { Tooltip } from "@/components/ui";
import type { Identity } from "@/lib/attribution";
import type { ServiceStatus } from "@/types/health";
import { LogOut, Menu, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import styles from "./CommandBar.module.css";

const THEME_KEY = "ds-theme";

/**
 * Reads the live DOM attribute rather than localStorage: the inline script in
 * app/layout.tsx already resolved the saved choice onto <html> before this
 * component mounts, so the attribute is the single source of truth.
 */
function ThemeToggle() {
	const [theme, setTheme] = useState<"dark" | "light">("dark");

	useEffect(() => {
		setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark");
	}, []);

	const toggle = () => {
		const next = theme === "light" ? "dark" : "light";
		document.documentElement.setAttribute("data-theme", next);
		try {
			localStorage.setItem(THEME_KEY, next);
		} catch {
			// Private browsing or a blocked store — theme still applies this session.
		}
		setTheme(next);
	};

	return (
		<button
			type="button"
			className={styles.themeToggle}
			onClick={toggle}
			aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
			title={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
		>
			{theme === "light" ? (
				<Moon size={16} aria-hidden="true" />
			) : (
				<Sun size={16} aria-hidden="true" />
			)}
		</button>
	);
}

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
				<ThemeToggle />
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
