"use client";

import {
	BookOpen,
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
import { useEffect, useRef, useState } from "react";
import styles from "./NavRail.module.css";

// Only what NAV_ITEMS names, plus LayoutDashboard as the fallback below.
const ICONS: Record<string, React.ElementType> = {
	Users,
	Share2,
	CalendarRange,
	Download,
	ScanSearch,
	ScrollText,
	BookOpen,
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

	const railRef = useRef<HTMLElement>(null);

	// Escape closes the mobile drawer. Clicking the backdrop already does, and a
	// drawer a keyboard user cannot dismiss is a trap.
	//
	// Tab is handled here too. Escape alone left the other half of the trap
	// open: the drawer is an overlay, so tabbing out of it moves focus to
	// controls sitting underneath it that the user cannot see. Keeping focus
	// inside while it is open, and putting it back on the toggle when it
	// closes, is the part that makes the drawer usable rather than merely
	// dismissible.
	useEffect(() => {
		if (!mobileOpen || !onMobileClose) return;

		const opener = document.activeElement as HTMLElement | null;
		// getClientRects() rather than the selector alone: the expand button is
		// display:none at this breakpoint, so it still matches but can never
		// hold focus. Left in the list it becomes "last", the wrap condition
		// never fires, and the trap silently does nothing.
		const focusables = () =>
			Array.from(
				railRef.current?.querySelectorAll<HTMLElement>(
					'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
				) ?? [],
			).filter((el) => el.getClientRects().length > 0);

		focusables()[0]?.focus();

		const onKey = (event: KeyboardEvent) => {
			if (event.key === "Escape") {
				onMobileClose();
				return;
			}
			if (event.key !== "Tab") return;

			const items = focusables();
			if (items.length === 0) return;
			const first = items[0];
			const last = items[items.length - 1];

			if (event.shiftKey && document.activeElement === first) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && document.activeElement === last) {
				event.preventDefault();
				first.focus();
			}
		};

		document.addEventListener("keydown", onKey);
		return () => {
			document.removeEventListener("keydown", onKey);
			// Back where they were, not wherever the DOM happens to land.
			opener?.focus?.();
		};
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
				ref={railRef}
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
							// The label span is display:none while the rail is
							// collapsed, which left every one of these as an icon
							// with no accessible name — axe flagged link-name on
							// all six, on every page. Naming the link here works
							// in both states.
							aria-label={item.label}
							data-active={active}
							// data-active is the styling hook; aria-current is the
							// only part a screen reader hears. They have to agree.
							aria-current={active ? "page" : undefined}
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
