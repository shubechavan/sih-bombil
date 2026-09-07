"use client";

import { useRef, useEffect } from "react";
import styles from "./TerminalLog.module.css";

export interface LogEntry {
  id: string;
  timestamp: string;
  message: string;
  type?: "info" | "success" | "warn" | "error";
}

interface TerminalLogProps {
  title?: string;
  entries: LogEntry[];
  autoScroll?: boolean;
}

export function TerminalLog({ title = "terminal", entries, autoScroll = true }: TerminalLogProps) {
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [entries, autoScroll]);

  return (
    <div className={styles.wrapper}>
      <div className={styles.header}>
        <span className={styles.dot} />
        <span className={styles.dot} />
        <span className={styles.dot} />
        <span className={styles.title}>{title}</span>
      </div>
      <div className={styles.body} ref={bodyRef}>
        {entries.map((entry) => (
          <div key={entry.id} className={styles.line} data-type={entry.type}>
            <span className={styles.timestamp}>{entry.timestamp}</span>
            <span className={styles.prefix}>▸</span>
            <span>{entry.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
