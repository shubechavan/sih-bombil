import type { ReactNode } from "react";
import styles from "./ChartShell.module.css";

interface ChartShellProps {
  title: string;
  headerRight?: ReactNode;
  empty?: boolean;
  emptyText?: string;
  children: ReactNode;
}

export function ChartShell({
  title,
  headerRight,
  empty,
  emptyText = "No data available",
  children,
}: ChartShellProps) {
  return (
    <div className={styles.shell}>
      <div className={styles.header}>
        <span className={styles.title}>{title}</span>
        {headerRight}
      </div>
      <div className={styles.body}>
        {empty ? <div className={styles.empty}>{emptyText}</div> : children}
      </div>
    </div>
  );
}
