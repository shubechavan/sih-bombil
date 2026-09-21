"use client";

import { CommandBar, NavRail } from "@/components/tactical";
import { useServiceHealth } from "@/hooks";
import { NAV_ITEMS } from "@/lib/constants";
import { getSeverityFromScore } from "@/lib/severity";
import { useAuthStore } from "@/stores/auth";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export function AppShell({ children }: { children: React.ReactNode }) {
	const [mobileNavOpen, setMobileNavOpen] = useState(false);
	const pathname = usePathname();
	const router = useRouter();
	const { services } = useServiceHealth();
	const { identity, checked, check } = useAuthStore();

	useEffect(() => {
		if (!checked) check();
	}, [checked, check]);

	// Send a signed-out operator to the login page rather than letting every
	// panel render its own 401.
	useEffect(() => {
		if (checked && !identity && pathname !== "/login") router.replace("/login");
	}, [checked, identity, pathname, router]);

	// Derive threat level from service health
	const offlineCount = Object.values(services).filter((s) => s.status === "offline").length;
	const threatLevel =
		offlineCount >= 3
			? "critical"
			: offlineCount >= 2
				? "high"
				: offlineCount >= 1
					? "medium"
					: "low";

	// The login page owns the whole viewport and has no nav to show.
	if (pathname === "/login") {
		return <>{children}</>;
	}

	if (pathname === "/") {
		return <main style={{ padding: "var(--ds-space-5)", overflow: "auto" }}>{children}</main>;
	}

	// Until the session has been checked, render nothing rather than flashing
	// the console at someone who is about to be redirected to /login.
	if (!checked || !identity) {
		return null;
	}

	// The audit view is admin-only on the API; hiding it from an analyst's rail
	// means they do not have to discover that by being refused.
	const navItems = identity.can_read_audit
		? NAV_ITEMS
		: NAV_ITEMS.filter((item) => item.href !== "/audit");

	return (
		<>
			{/* First focusable thing on every page: a keyboard user should not
			    have to tab through the whole nav rail to reach the content. */}
			<a className="skip-link" href="#main">
				Skip to content
			</a>
			<CommandBar
				threatLevel={threatLevel as "critical" | "high" | "medium" | "low"}
				services={services}
				onMenuToggle={() => setMobileNavOpen(!mobileNavOpen)}
				identity={identity}
				onSignOut={() => {
					useAuthStore.getState().signOut();
					router.replace("/login");
				}}
			/>
			<div className="app-layout">
				<NavRail
					items={navItems}
					mobileOpen={mobileNavOpen}
					onMobileClose={() => setMobileNavOpen(false)}
				/>
				<main id="main" className="app-layout__main">
					{children}
				</main>
			</div>
		</>
	);
}
