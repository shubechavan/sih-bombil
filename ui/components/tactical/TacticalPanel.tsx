import { Skeleton } from "@/components/ui";
import styles from "./TacticalPanel.module.css";

interface TacticalPanelProps {
	title: string;
	subtitle?: string;
	icon?: React.ReactNode;
	headerRight?: React.ReactNode;
	severity?: "critical" | "high" | "medium" | "low" | "info";
	loading?: boolean;
	children?: React.ReactNode;
}

function PanelSkeleton() {
	return (
		<div className={styles.skeleton}>
			<Skeleton variant="text" width="80%" />
			<Skeleton variant="text" width="60%" />
			<Skeleton variant="text" width="70%" />
		</div>
	);
}

export function TacticalPanel({
	title,
	subtitle,
	icon,
	headerRight,
	severity,
	loading,
	children,
}: TacticalPanelProps) {
	return (
		// aria-busy tells a screen reader the region is mid-update. Skeleton
		// itself is aria-hidden, so without this the panel simply goes quiet
		// and a user has no way to know something is coming.
		<section className={styles.panel} data-severity={severity} aria-busy={loading}>
			<header className={styles.header}>
				<div className={styles.heading}>
					{icon && <span className={styles.icon}>{icon}</span>}
					<div>
						<h2 className={styles.title}>{title}</h2>
						{subtitle && <p className={styles.subtitle}>{subtitle}</p>}
					</div>
				</div>
				{headerRight}
			</header>
			<div className={styles.body}>{loading ? <PanelSkeleton /> : children}</div>
		</section>
	);
}
