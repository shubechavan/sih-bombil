"use client";

import { useState } from "react";
import { BarChart3, Brain, Globe, Shield } from "lucide-react";
import { Button } from "@/components/ui";
import styles from "./analytics.module.css";

const TABS = [
	{ id: "executive", label: "Executive Summary", icon: BarChart3 },
	{ id: "network", label: "Network Anomaly", icon: Globe },
	{ id: "recon", label: "Recon Engine", icon: Shield },
	{ id: "intelligence", label: "AI Intelligence", icon: Brain },
] as const;

type TabId = (typeof TABS)[number]["id"];

// Lazy-loaded sub-views
import ExecutiveSummary from "./executive/ExecutiveSummary";
import NetworkAnomaly from "./network/NetworkAnomaly";
import ReconEngine from "./recon/ReconEngine";
import IntelligenceMatrix from "./intelligence/IntelligenceMatrix";

const VIEW_MAP: Record<TabId, React.ComponentType> = {
	executive: ExecutiveSummary,
	network: NetworkAnomaly,
	recon: ReconEngine,
	intelligence: IntelligenceMatrix,
};

export default function AnalyticsPage() {
	const [activeTab, setActiveTab] = useState<TabId>("executive");
	const ActiveView = VIEW_MAP[activeTab];

	return (
		<div className={styles.wrapper}>
			<nav className={styles.tabs}>
				{TABS.map((tab) => {
					const Icon = tab.icon;
					return (
						<Button
							key={tab.id}
							variant={activeTab === tab.id ? "primary" : "ghost"}
							size="sm"
							onClick={() => setActiveTab(tab.id)}
						>
							<Icon size={14} />
							{tab.label}
						</Button>
					);
				})}
			</nav>
			<div className={styles.content}>
				<ActiveView />
			</div>
		</div>
	);
}
