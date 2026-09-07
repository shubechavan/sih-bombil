"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { CommandBar, NavRail } from "@/components/tactical";
import { useServiceHealth } from "@/hooks";
import { NAV_ITEMS } from "@/lib/constants";
import { getSeverityFromScore } from "@/lib/severity";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const pathname = usePathname();
  const { services } = useServiceHealth();

  // Derive threat level from service health
  const offlineCount = Object.values(services).filter(
    (s) => s.status === "offline"
  ).length;
  const threatLevel =
    offlineCount >= 3
      ? "critical"
      : offlineCount >= 2
        ? "high"
        : offlineCount >= 1
          ? "medium"
          : "low";

  if (pathname === "/") {
    return <main style={{ padding: "var(--ds-space-5)", overflow: "auto" }}>{children}</main>;
  }

  return (
    <>
      <CommandBar
        threatLevel={threatLevel as "critical" | "high" | "medium" | "low"}
        services={services}
        onMenuToggle={() => setMobileNavOpen(!mobileNavOpen)}
      />
      <div className="app-layout">
        <NavRail
          items={NAV_ITEMS}
          mobileOpen={mobileNavOpen}
          onMobileClose={() => setMobileNavOpen(false)}
        />
        <main style={{ overflow: "auto", padding: "var(--ds-space-5)" }}>
          {children}
        </main>
      </div>
    </>
  );
}
