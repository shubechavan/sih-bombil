"use client";

import { useState } from "react";
import { Bell, Send, TestTube, Trash2 } from "lucide-react";
import { useAlertStore } from "@/stores/alerts";
import { TacticalPanel, SeverityPill } from "@/components/tactical";
import { Button, Select, TextArea, Badge } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import styles from "./alerts.module.css";

import type { Severity } from "@/types/threat";

const SEVERITY_OPTIONS = [
	{ value: "CRITICAL", label: "Critical" },
	{ value: "HIGH", label: "High" },
	{ value: "MEDIUM", label: "Medium" },
	{ value: "LOW", label: "Low" },
];

export default function AlertsPage() {
	const { history, sendAlert, testWebhook, clearHistory } = useAlertStore();
	const [severity, setSeverity] = useState<Severity>("HIGH");
	const [message, setMessage] = useState("");
	const [source, setSource] = useState("");
	const [sending, setSending] = useState(false);
	const [testing, setTesting] = useState(false);
	const [actionError, setActionError] = useState<string | null>(null);

	async function handleSend() {
		if (!message.trim()) return;
		setActionError(null);
		setSending(true);
		try {
			await sendAlert({
				severity,
				message: message.trim(),
				source: source.trim() || "manual",
				timestamp: new Date().toISOString(),
			});
			setMessage("");
			setSource("");
		} catch (err) {
			setActionError(err instanceof Error ? err.message : "Failed to send alert");
		} finally {
			setSending(false);
		}
	}

	async function handleTest() {
		setActionError(null);
		setTesting(true);
		try {
			await testWebhook();
		} catch (err) {
			setActionError(err instanceof Error ? err.message : "Webhook test failed");
		} finally {
			setTesting(false);
		}
	}

	return (
		<div className={styles.wrapper}>
			<TacticalPanel title="Send Alert" icon={<Bell size={16} />}>
				<div className={styles.form}>
					<Select
						label="Severity"
						options={SEVERITY_OPTIONS}
						value={severity}
						onChange={(e) => setSeverity(e.target.value as Severity)}
					/>
					<TextArea
						label="Message"
						value={message}
						onChange={(e) => setMessage(e.target.value)}
						maxLength={2000}
						rows={4}
						placeholder="Describe the alert..."
					/>
					<TextArea
						label="Source (optional)"
						value={source}
						onChange={(e) => setSource(e.target.value)}
						rows={1}
						placeholder="e.g. manual, pipeline, system"
					/>
					<div className={styles.actions}>
						<Button onClick={handleSend} loading={sending} disabled={!message.trim()}>
							<Send size={14} />
							Send Alert
						</Button>
						<Button variant="secondary" onClick={handleTest} loading={testing}>
							<TestTube size={14} />
							Test Webhook
						</Button>
					</div>
					{actionError && (
						<p style={{ color: "var(--ds-critical)", fontFamily: "var(--ds-font-mono)", fontSize: "0.8rem", marginTop: "0.5rem" }}>
							{actionError}
						</p>
					)}
				</div>
			</TacticalPanel>

			<TacticalPanel
				title="Alert History"
				headerRight={
					history.length > 0 ? (
						<Button variant="ghost" size="sm" onClick={clearHistory}>
							<Trash2 size={14} />
							Clear
						</Button>
					) : undefined
				}
			>
				{history.length === 0 ? (
					<p className={styles.empty}>No alerts sent yet.</p>
				) : (
					<div className={styles.historyList}>
						{history.map((entry) => (
							<div key={entry.id} className={styles.historyItem}>
								<div className={styles.historyHeader}>
									<SeverityPill severity={entry.payload.severity} />
									<Badge
										variant={
											entry.status === "sent"
												? "accent"
												: entry.status === "failed"
													? "critical"
													: "neutral"
										}
									>
										{entry.status}
									</Badge>
									<span className={styles.historyTime}>{relativeTime(entry.payload.timestamp)}</span>
								</div>
								<p className={styles.historyMessage}>{entry.payload.message}</p>
								{entry.error && <p className={styles.historyError}>{entry.error}</p>}
							</div>
						))}
					</div>
				)}
			</TacticalPanel>
		</div>
	);
}
