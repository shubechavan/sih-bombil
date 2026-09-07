"use client";

import { AlertTriangle, Info, X } from "lucide-react";
import styles from "./AlertBanner.module.css";

interface AlertBannerProps {
  severity: "critical" | "high" | "medium" | "low" | "info";
  message: string;
  onDismiss?: () => void;
}

export function AlertBanner({ severity, message, onDismiss }: AlertBannerProps) {
  const Icon = severity === "info" ? Info : AlertTriangle;

  return (
    <div className={styles.banner} data-severity={severity} role="alert">
      <span className={styles.icon}>
        <Icon size={16} />
      </span>
      <span className={styles.message}>{message}</span>
      {onDismiss && (
        <button className={styles.dismiss} onClick={onDismiss} aria-label="Dismiss">
          <X size={14} />
        </button>
      )}
    </div>
  );
}
