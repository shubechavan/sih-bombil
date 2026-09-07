"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Send } from "lucide-react";
import { TacticalPanel, RiskGauge, SeverityPill, ConfidenceMeter, AlertBanner } from "@/components/tactical";
import { ConfidenceRadar } from "@/components/charts";
import { Button, Badge } from "@/components/ui";
import { bff } from "@/lib/api";
import { formatScore, truncHash } from "@/lib/format";
import { getSeverityFromScore } from "@/lib/severity";
import type { Threat, AnalysisResult, Severity, Consensus } from "@/types/threat";
import styles from "./detail.module.css";

/**
 * The backend /analyze endpoint returns the full AnalysisResult shape (nested roberta/blink)
 * PLUS flat llm_mitre* fields for full ATT&CK detail. We extend AnalysisResult to carry them.
 */
interface AnalyzeApiResponse extends AnalysisResult {
  llm_mitre?: string | null;
  llm_mitre_tactic?: string | null;
  llm_mitre_tactic_id?: string | null;
  llm_mitre_technique_name?: string | null;
}

function normalizeAnalysis(raw: AnalyzeApiResponse): AnalyzeApiResponse {
  // Backend already returns the full nested shape; just ensure required fields have fallbacks.
  return {
    ...raw,
    roberta: raw.roberta ?? { category: "Unknown", confidence: 0, scores: {} },
    blink: raw.blink ?? { category: "Unknown", severity: "LOW", mitreTags: [], reasoning: "" },
    consensus: raw.consensus ?? "HUMAN_REVIEW",
    riskScore: Math.min(1, Math.max(0, Number(raw.riskScore ?? 0) || 0)),
    piiRedactions: raw.piiRedactions ?? [],
  };
}

interface Props {
  hash: string;
}

export function ThreatDetailClient({ hash }: Props) {
  const router = useRouter();
  const [threat, setThreat] = useState<Threat | null>(null);
  const [analysis, setAnalysis] = useState<AnalyzeApiResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await bff.get(`threats?hash=${hash}`).json<{ items: Threat[] }>();
        if (data.items.length > 0) setThreat(data.items[0]);
      } catch {
        setError("Failed to load threat");
      } finally {
        setLoading(false);
      }
    })();
  }, [hash]);

  const runAnalysis = async () => {
    if (!threat) return;
    setAnalyzing(true);
    try {
      const raw = await bff
        .post("analyze", {
          json: { rowHash: threat.rowHash, text: threat.textSnippet, includeExplain: true },
        })
        .json<AnalyzeApiResponse>();
      setAnalysis(normalizeAnalysis(raw));
    } catch {
      setError("Analysis failed");
    } finally {
      setAnalyzing(false);
    }
  };

  if (loading) {
    return <TacticalPanel title="Loading…" loading />;
  }

  if (!threat) {
    return (
      <TacticalPanel title="Not Found" subtitle={`No threat with hash ${truncHash(hash)}`}>
        <Button variant="secondary" icon={ArrowLeft} onClick={() => router.back()}>
          Back
        </Button>
      </TacticalPanel>
    );
  }

  const severity = getSeverityFromScore(threat.riskScore);

  const radarData = analysis
    ? [
        { axis: "RoBERTa", value: analysis.roberta.confidence },
        { axis: "BLINK", value: analysis.riskScore },
        { axis: "Consensus", value: analysis.consensus === "THREAT_CONFIRMED" ? 0.95 : 0.5 },
        { axis: "PII Risk", value: Math.min(1, (analysis.piiRedactions?.length ?? 0) / 5) },
      ]
    : [];

  return (
    <div className={styles.wrapper}>
      <Button variant="ghost" icon={ArrowLeft} onClick={() => router.back()}>
        Back to Feed
      </Button>

      {error && <AlertBanner severity="critical" message={error} onDismiss={() => setError(null)} />}

      <TacticalPanel
        title={`Threat ${truncHash(threat.rowHash)}`}
        subtitle={threat.searchEngine}
        severity={severity.toLowerCase() as Lowercase<Severity>}
        headerRight={
          <div className={styles.headerActions}>
            <SeverityPill severity={severity} />
            <Button
              variant="primary"
              size="sm"
              icon={Send}
              onClick={runAnalysis}
              loading={analyzing}
              disabled={analyzing}
            >
              Analyze
            </Button>
          </div>
        }
      >
        <div className={styles.grid}>
          <div className={styles.meta}>
            <div className={styles.field}>
              <span className={styles.fieldLabel}>Hash</span>
              <code className={styles.fieldValue}>{threat.rowHash}</code>
            </div>
            <div className={styles.field}>
              <span className={styles.fieldLabel}>Risk Score</span>
              <span className={styles.fieldValue}>{formatScore(threat.riskScore)}</span>
            </div>
            <div className={styles.field}>
              <span className={styles.fieldLabel}>Category</span>
              <Badge variant="accent">{threat.category || "Uncategorized"}</Badge>
            </div>
            <div className={styles.field}>
              <span className={styles.fieldLabel}>Content</span>
              <p className={styles.content}>{threat.textSnippet}</p>
            </div>
          </div>

          <div className={styles.sidebar}>
            <RiskGauge score={threat.riskScore} />
          </div>
        </div>

        {analysis && (
          <div className={styles.analysisSection}>
            <h3 className={styles.sectionTitle}>AI Analysis</h3>
            <div className={styles.analysisGrid}>
              <div className={styles.engineResult}>
                <span className={styles.engineName}>RoBERTa</span>
                <Badge variant="accent">{analysis.roberta.category}</Badge>
                <ConfidenceMeter label="Confidence" value={analysis.roberta.confidence} />
              </div>
              <div className={styles.engineResult}>
                <span className={styles.engineName}>BLINK</span>
                <Badge variant="accent">{analysis.blink.category}</Badge>
                <SeverityPill severity={analysis.blink.severity as Severity} />
              </div>
              <div className={styles.engineResult}>
                <span className={styles.engineName}>Consensus</span>
                <Badge
                  variant={
                    analysis.consensus === "THREAT_CONFIRMED"
                      ? "critical"
                      : analysis.consensus === "HUMAN_REVIEW"
                        ? "medium"
                        : "low"
                  }
                >
                  {analysis.consensus}
                </Badge>
              </div>
            </div>

            <ConfidenceRadar data={radarData} />

            {/* MITRE ATT&CK mapping */}
            {(analysis.llm_mitre || analysis.blink.mitreTags?.length > 0) && (
              <div className={styles.explain}>
                <span className={styles.fieldLabel}>MITRE ATT&amp;CK</span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginTop: "0.4rem" }}>
                  {analysis.llm_mitre && (
                    <Badge variant="accent">{analysis.llm_mitre}</Badge>
                  )}
                  {analysis.llm_mitre_technique_name && (
                    <Badge variant="accent">{analysis.llm_mitre_technique_name}</Badge>
                  )}
                  {analysis.llm_mitre_tactic && (
                    <Badge variant="medium">{analysis.llm_mitre_tactic}</Badge>
                  )}
                  {analysis.llm_mitre_tactic_id && (
                    <Badge variant="neutral">{analysis.llm_mitre_tactic_id}</Badge>
                  )}
                </div>
              </div>
            )}

            {analysis.explain && (
              <div className={styles.explain}>
                <span className={styles.fieldLabel}>Explanation</span>
                <p className={styles.content}>{analysis.explain}</p>
              </div>
            )}
          </div>
        )}
      </TacticalPanel>
    </div>
  );
}
