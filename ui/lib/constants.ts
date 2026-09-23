/**
 * Shared constants for the attribution console.
 *
 * v1's search queries, fetch profiles, threat categories and pipeline stages
 * went with the pages that used them. What is left is the navigation, which is
 * the five attribution views — `/actors/[id]` is reached from the table rather
 * than from the rail.
 */

export const NAV_ITEMS = [
	{ href: "/actors", label: "Actors", icon: "Users" },
	{ href: "/analyze", label: "Analyze", icon: "ScanSearch" },
	{ href: "/graph", label: "Link Graph", icon: "Share2" },
	{ href: "/timeline", label: "Timeline", icon: "CalendarRange" },
	{ href: "/export", label: "Export", icon: "Download" },
	// Admin only. AppShell filters it out for an analyst, so they do not have to
	// discover the restriction by being refused.
	{ href: "/audit", label: "Audit", icon: "ScrollText" },
	{ href: "/docs", label: "Docs", icon: "BookOpen" },
] as const;
