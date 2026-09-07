"use client";

import { motion } from "framer-motion";
import { CheckCircle2, XCircle, Loader2 } from "lucide-react";
import type { StageStatus } from "@/types/pipeline";
import styles from "./PipelineStage.module.css";

interface PipelineStageProps {
  name: string;
  index: number;
  status: StageStatus;
  progress?: number;
  metrics?: Record<string, number>;
  onClick?: () => void;
}

function StatusIcon({ status }: { status: StageStatus }) {
  if (status === "complete")
    return <CheckCircle2 size={16} />;
  if (status === "error")
    return <XCircle size={16} />;
  if (status === "processing")
    return <Loader2 size={16} className="animate-spin" />;
  return null;
}

export function PipelineStage({ name, index, status, progress, metrics, onClick }: PipelineStageProps) {
  return (
    <motion.button
      className={styles.stage}
      data-status={status}
      onClick={onClick}
      whileHover={{ y: -2 }}
      whileTap={{ scale: 0.98 }}
    >
      <span className={styles.index}>{String(index + 1).padStart(2, "0")}</span>
      <span className={styles.name}>{name}</span>

      {status === "processing" && (
        <div className={styles.progressBar}>
          <motion.div
            className={styles.progressFill}
            initial={{ width: 0 }}
            animate={{ width: `${progress ?? 0}%` }}
            transition={{ duration: 0.3 }}
          />
        </div>
      )}

      {status === "complete" && metrics && (
        <span className={styles.metric}>
          {Object.values(metrics)[0]}
        </span>
      )}

      <span className={styles.statusIcon} data-status={status}>
        <StatusIcon status={status} />
      </span>
    </motion.button>
  );
}
