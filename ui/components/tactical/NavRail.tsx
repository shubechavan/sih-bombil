"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Play,
  Shield,
  BarChart3,
  Eye,
  Bell,
  Settings,
  ChevronLeft,
  ChevronRight,
  Workflow,
  Fingerprint,
  Activity,
  Users,
  Share2,
  CalendarRange,
  Download,
} from "lucide-react";
import styles from "./NavRail.module.css";

const ICONS: Record<string, React.ElementType> = {
  LayoutDashboard,
  Play,
  Shield,
  BarChart3,
  Eye,
  Bell,
  Settings,
  Workflow,
  Fingerprint,
  Activity,
  Users,
  Share2,
  CalendarRange,
  Download,
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

  return (
    <>
      {mobileOpen && (
        <div className={styles.backdrop} onClick={onMobileClose} />
      )}
      <nav
        className={styles.rail}
        data-expanded={expanded}
        data-open={mobileOpen}
      >
        {items.map((item) => {
          const Icon = ICONS[item.icon] ?? LayoutDashboard;
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);

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
