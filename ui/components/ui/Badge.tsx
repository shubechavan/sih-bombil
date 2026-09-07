import styles from "./Badge.module.css";

type BadgeVariant = "critical" | "high" | "medium" | "low" | "accent" | "neutral";

interface BadgeProps {
  variant?: BadgeVariant;
  dot?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function Badge({ variant = "neutral", dot, children, className }: BadgeProps) {
  return (
    <span className={`${styles.badge} ${styles[variant]} ${className ?? ""}`}>
      {dot && <span className={styles.dot} />}
      {children}
    </span>
  );
}
