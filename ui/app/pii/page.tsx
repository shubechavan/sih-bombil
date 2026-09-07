"use client";

import { useState } from "react";
import { ShieldAlert, Copy, Check } from "lucide-react";
import { bff } from "@/lib/api";
import { TacticalPanel } from "@/components/tactical";
import { Button, TextArea, Badge } from "@/components/ui";
import styles from "./pii.module.css";

interface ScrubResult {
	original: string;
	scrubbed: string;
	redactions: Array<{ type: string; original: string; replacement: string }>;
}

export default function PiiPage() {
	const [input, setInput] = useState("");
	const [result, setResult] = useState<ScrubResult | null>(null);
	const [loading, setLoading] = useState(false);
	const [copied, setCopied] = useState(false);
	const [scrubError, setScrubError] = useState<string | null>(null);

	async function handleScrub() {
		if (!input.trim()) return;
		setScrubError(null);
		setLoading(true);
		try {
			const res = await bff.post("pii/scrub", { json: { text: input } }).json<ScrubResult>();
			setResult(res);
		} catch (err) {
			setResult(null);
			setScrubError(err instanceof Error ? err.message : "PII scrub failed — is the backend online?");
		} finally {
			setLoading(false);
		}
	}

	function handleCopy() {
		if (!result) return;
		navigator.clipboard.writeText(result.scrubbed);
		setCopied(true);
		setTimeout(() => setCopied(false), 2000);
	}

	return (
		<div className={styles.wrapper}>
			<TacticalPanel title="PII Protection" icon={<ShieldAlert size={16} />}>
				<p className={styles.description}>
					Paste text containing potential PII (names, emails, IPs, phone numbers) to scrub it before
					analysis. The engine uses pattern matching and NER to detect and redact sensitive information.
				</p>

				<div className={styles.inputSection}>
					<TextArea
						label="Input Text"
						value={input}
						onChange={(e) => setInput(e.target.value)}
						maxLength={10000}
						rows={8}
						placeholder="Paste text containing PII here..."
					/>
					<Button onClick={handleScrub} loading={loading} disabled={!input.trim()}>
						Scrub PII
					</Button>
					{scrubError && (
						<p style={{ color: "var(--ds-critical)", fontFamily: "var(--ds-font-mono)", fontSize: "0.8rem", marginTop: "0.5rem" }}>
							{scrubError}
						</p>
					)}
				</div>
			</TacticalPanel>

			{result && (
				<>
					<TacticalPanel title="Scrubbed Output">
						<div className={styles.outputHeader}>
							<span className={styles.redactionCount}>
								{result.redactions.length} redaction{result.redactions.length !== 1 ? "s" : ""} applied
							</span>
							<Button variant="ghost" size="sm" onClick={handleCopy}>
								{copied ? <Check size={14} /> : <Copy size={14} />}
								{copied ? "Copied" : "Copy"}
							</Button>
						</div>
						<pre className={styles.outputText}>{result.scrubbed}</pre>
					</TacticalPanel>

					{result.redactions.length > 0 && (
						<TacticalPanel title="Redaction Details">
							<div className={styles.redactionList}>
								{result.redactions.map((r, i) => (
									<div key={i} className={styles.redactionItem}>
										<Badge variant="accent">{r.type}</Badge>
										<span className={styles.redactionOriginal}>{r.original}</span>
										<span className={styles.redactionArrow}>→</span>
										<span className={styles.redactionReplacement}>{r.replacement}</span>
									</div>
								))}
							</div>
						</TacticalPanel>
					)}

					<TacticalPanel title="Diff View">
						<div className={styles.diffGrid}>
							<div className={styles.diffPane}>
								<h4 className={styles.diffTitle}>Original</h4>
								<pre className={styles.diffText}>{result.original}</pre>
							</div>
							<div className={styles.diffPane}>
								<h4 className={styles.diffTitle}>Scrubbed</h4>
								<pre className={styles.diffText}>{result.scrubbed}</pre>
							</div>
						</div>
					</TacticalPanel>
				</>
			)}
		</div>
	);
}
