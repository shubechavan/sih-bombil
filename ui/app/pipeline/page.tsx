"use client";

import { useRouter } from "next/navigation";
import { TacticalPanel, PipelineStage, AlertBanner } from "@/components/tactical";
import { Button } from "@/components/ui";
import { usePipelineState } from "@/hooks";
import styles from "./pipeline.module.css";

export default function PipelinePage() {
  const router = useRouter();
  const { stages, isRunning, error, scanId, reset, cancelScan, sseStatus } = usePipelineState();

  return (
    <div className={styles.wrapper}>
      <TacticalPanel
        title="Pipeline Monitor"
        subtitle={scanId ? `Scan ID: ${scanId}` : "No active scan"}
        severity={isRunning ? "info" : undefined}
        headerRight={
          <div className={styles.actions}>
            <Button
              variant="primary"
              size="sm"
              onClick={() => router.push("/pipeline/configure")}
              disabled={isRunning}
            >
              New Scan
            </Button>
            {scanId && !isRunning && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => router.push("/pipeline/analysis")}
              >
                View Results
              </Button>
            )}
          </div>
        }
      >
        {error && (
          <AlertBanner severity="critical" message={error} onDismiss={reset} />
        )}

        {isRunning && (
          <div className={styles.sseStatus} style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <span>Stream: {sseStatus}</span>
            {sseStatus === "closed" && (
              <Button variant="danger" size="sm" onClick={cancelScan}>
                Force Reset
              </Button>
            )}
          </div>
        )}

        <div className={styles.stages}>
          {stages.map((stage, i) => (
            <PipelineStage
              key={stage.name}
              name={stage.name}
              index={i}
              status={stage.status}
              progress={stage.progress}
              metrics={stage.metrics}
            />
          ))}
        </div>
      </TacticalPanel>
    </div>
  );
}
