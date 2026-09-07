"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { TacticalPanel } from "@/components/tactical";
import { Button, TextArea, Select, Toggle } from "@/components/ui";
import { usePipelineStore } from "@/stores/pipeline";
import { DEFAULT_QUERIES, FETCH_PROFILES, MAX_RESULTS_OPTIONS } from "@/lib/constants";
import type { ScanConfig, FetchProfile } from "@/types/pipeline";
import styles from "./configure.module.css";

export default function ConfigurePage() {
  const router = useRouter();
  const startScan = usePipelineStore((s) => s.startScan);

  const [queries, setQueries] = useState(DEFAULT_QUERIES.join("\n"));
  const [fetchProfile, setFetchProfile] = useState<FetchProfile>("stealth");
  const [maxResults, setMaxResults] = useState(100);
  const [enableObfuscation, setEnableObfuscation] = useState(true);
  const [enablePiiScrub, setEnablePiiScrub] = useState(true);

  const handleStart = async () => {
    const config: ScanConfig = {
      queries: queries.split("\n").map((q) => q.trim()).filter(Boolean),
      fetchProfile,
      maxResults,
      enableObfuscation,
      enablePiiScrub,
    };
    await startScan(config);
    router.push("/pipeline");
  };

  return (
    <div className={styles.wrapper}>
      <TacticalPanel title="Configure Scan" subtitle="Set up dark web intelligence collection parameters">
        <div className={styles.form}>
          <TextArea
            label="Search Queries"
            value={queries}
            onChange={(e) => setQueries(e.target.value)}
            rows={6}
            placeholder="Enter queries, one per line..."
          />

          <div className={styles.row}>
            <Select
              label="Fetch Profile"
              value={fetchProfile}
              onChange={(e) => setFetchProfile(e.target.value as FetchProfile)}
              options={Object.entries(FETCH_PROFILES).map(([value, config]) => ({
                value,
                label: config.label,
              }))}
            />

            <Select
              label="Max Results"
              value={String(maxResults)}
              onChange={(e) => setMaxResults(Number(e.target.value))}
              options={MAX_RESULTS_OPTIONS.map((n) => ({
                value: String(n),
                label: String(n),
              }))}
            />
          </div>

          <div className={styles.toggles}>
            <Toggle
              label="Obfuscation Detection"
              checked={enableObfuscation}
              onChange={setEnableObfuscation}
            />
            <Toggle
              label="PII Auto-Scrub"
              checked={enablePiiScrub}
              onChange={setEnablePiiScrub}
            />
          </div>

          <div className={styles.actions}>
            <Button variant="secondary" onClick={() => router.back()}>
              Cancel
            </Button>
            <Button variant="primary" onClick={handleStart}>
              Start Scan
            </Button>
          </div>
        </div>
      </TacticalPanel>
    </div>
  );
}
