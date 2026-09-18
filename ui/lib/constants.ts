import { PIPELINE_STAGES } from "@/types/pipeline";

export { PIPELINE_STAGES };

export const DEFAULT_QUERIES = [
  "drug marketplace dark web",
  "stolen credit card data",
  "hacking tools exploit kits",
  "ransomware as a service",
  "leaked database credentials",
] as const;

export const FETCH_PROFILES = {
  balanced: { label: "Balanced", description: "Standard crawl speed, moderate stealth" },
  stealth: { label: "Stealth", description: "Slower crawl, maximum anonymity" },
  aggressive: { label: "Aggressive", description: "Fast crawl, higher detection risk" },
} as const;

export const MAX_RESULTS_OPTIONS = [50, 100, 200] as const;

export const CHART_COLORS = {
  critical: "#FF2D55",
  high: "#FF9500",
  medium: "#FFCC00",
  low: "#30D158",
  accent: "#00E5A0",
  info: "#00B4D8",
  surface: "#1A332B",
  text: "#7A9B8C",
  grid: "rgba(122, 155, 140, 0.10)",
} as const;

export const THREAT_CATEGORIES = [
  "drugs",
  "weapons",
  "fraud",
  "hacking",
  "counterfeit",
  "stolen_data",
  "malware",
  "ransomware",
  "extremism",
  "other",
] as const;

// Attribution (Phase 4) sits above the v1 pages, which are still the Dark
// Sentinel v1 content-threat views and are not wired to the attribution API.
export const NAV_ITEMS = [
  { href: "/", label: "Mission Control", icon: "LayoutDashboard" },
  { href: "/actors", label: "Actors", icon: "Users" },
  { href: "/graph", label: "Link Graph", icon: "Share2" },
  { href: "/timeline", label: "Timeline", icon: "CalendarRange" },
  { href: "/export", label: "Export", icon: "Download" },
  { href: "/pipeline", label: "Pipeline", icon: "Workflow" },
  { href: "/threats", label: "Threat Feed", icon: "Shield" },
  { href: "/analytics", label: "Analytics", icon: "BarChart3" },
  { href: "/pii", label: "PII Protection", icon: "Fingerprint" },
  { href: "/alerts", label: "Alerts", icon: "Bell" },
  { href: "/system", label: "System", icon: "Activity" },
] as const;
