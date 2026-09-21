"use client";

import {
	CalendarRange,
	ChevronLeft,
	ChevronRight,
	Download,
	LayoutDashboard,
	ScanSearch,
	ScrollText,
	Share2,
	Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import styles from "./NavRail.module.css";

// Only what NAV_ITEMS names, plus LayoutDashboard as the fallback below.
const ICONS: Record<string, React.ElementType> = {
	Users,
	Share2,
	CalendarRange,
	Download,
	ScanSearch,
	ScrollText,
};

interface NavItem {
	href: string;
	label: string;
	icon: string;
}

interface NavRailProps {
	items: readonly NavItem[];
	mobileOpen?: boolean;
	onMobileClose?: () => void;
}

export function NavRail({ items, mobileOpen, onMobileClose }: NavRailProps) {
	const [expanded, setExpanded] = useState(false);
	const pathname = usePathname();

	// Escape closes the mobile drawer. Clicking the backdrop already does, and a
	// drawer a keyboard user cannot dismiss is a trap.
	useEffect(() => {
		if (!mobileOpen || !onMobileClose) return;
		const onKey = (event: KeyboardEvent) => {
			if (event.key === "Escape") onMobileClose();
		};
		document.addEventListener("keydown", onKey);
		return () => document.removeEventListener("keydown", onKey);
	}, [mobileOpen, onMobileClose]);

	return (
		<>
			{mobileOpen && (
				<button
					type="button"
					className={styles.backdrop}
					onClick={onMobileClose}
					aria-label="Close navigation"
				/>
			)}
			{/* `app-layout__nav` is the grid placement and the 768px drawer
			    behaviour, both of which live in globals.css. Without it this
			    auto-places into grid row 1 and pushes the main column down the
			    page, and the mobile drawer rules never apply. */}
			<nav
				className={`app-layout__nav ${styles.rail}`}
				aria-label="Sections"
				data-expanded={expanded}
				data-open={mobileOpen}
			>
				{items.map((item) => {
					const Icon = ICONS[item.icon] ?? LayoutDashboard;
					const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);

					return (
						<Link
							key={item.href}
							href={item.href}
							className={styles.navItem}
							data-active={active}
							onClick={onMobileClose}
						>
							<span className={styles.iconWrap}>
								<Icon size={18} />
							</span>
							<span className={styles.label}>{item.label}</span>
						</Link>
					);
				})}

				<button
					type="button"
					className={styles.expandBtn}
					onClick={() => setExpanded(!expanded)}
					aria-label={expanded ? "Collapse navigation" : "Expand navigation"}
				>
					{expanded ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
				</button>
			</nav>
		</>
	);
}
