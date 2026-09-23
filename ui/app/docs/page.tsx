"use client";

import { TacticalPanel } from "@/components/tactical";
import {
	BookOpen,
	CheckCircle2,
	Clock,
	ExternalLink,
	Eye,
	Fingerprint,
	Key,
	Layers,
	Network,
	Scale,
	Search,
	Shield,
	Terminal,
} from "lucide-react";
import styles from "./docs.module.css";

export default function DocsPage() {
	return (
		<div className={styles.container}>
			<header className={styles.hero}>
				<div className={styles.heroTitle}>
					<Shield size={28} />
					<span>Dark Sentinel v2 — System Documentation</span>
				</div>
				<p className={styles.heroSubtitle}>
					Dark web threat actor deanonymization and cross-site attribution platform. Unit of
					analysis is the <strong>threat actor</strong> (a real human), linking multiple
					pseudonymous personas across onion markets and forums using multi-modal forensic evidence.
				</p>
				<div className={styles.badgeRow}>
					<span className={styles.tag}>SIH 2026</span>
					<span className={styles.tag}>7 Phases Complete</span>
					<span className={styles.tag}>414 Tests Passing</span>
					<span className={styles.tag}>Precision 1.000</span>
					<span className={styles.tag}>Separation Margin +0.508</span>
				</div>
			</header>

			{/* Evaluation Numbers */}
			<TacticalPanel
				title="System Performance & Verification"
				subtitle="Empirically measured against synthetic answer key (20 personas, 3 markets, 14 actors)"
				icon={<Scale size={20} />}
			>
				<div className={styles.grid4}>
					<div className={styles.card}>
						<span className={styles.statLabel}>Precision</span>
						<span className={styles.statValue}>1.000</span>
						<p className={styles.cardText}>Zero false accusations across all confidence bands</p>
					</div>
					<div className={styles.card}>
						<span className={styles.statLabel}>Recall (Clustered)</span>
						<span className={styles.statValue}>8 / 8</span>
						<p className={styles.cardText}>100% of rebranded migration pairs successfully linked</p>
					</div>
					<div className={styles.card}>
						<span className={styles.statLabel}>Separation Margin</span>
						<span className={styles.statValue}>+0.508</span>
						<p className={styles.cardText}>
							Gap between weakest true match (0.853) & strongest false match (0.345)
						</p>
					</div>
					<div className={styles.card}>
						<span className={styles.statLabel}>Automated Tests</span>
						<span className={styles.statValue}>414 + 49</span>
						<p className={styles.cardText}>
							414 pytest unit/integration tests + 49 browser checks passing
						</p>
					</div>
				</div>
			</TacticalPanel>

			{/* Attribution Formula */}
			<TacticalPanel
				title="Explainable Attribution Confidence"
				subtitle="Transparent mathematical formulation ensuring court-grade auditability"
				icon={<Terminal size={20} />}
			>
				<div className={styles.formulaBox}>A = 0.40 · H + 0.25 · S + 0.20 · B + 0.15 · I</div>

				<div className={styles.grid4}>
					<div className={styles.card}>
						<div className={styles.cardTitle}>
							<Key size={16} color="#22d3ee" />
							<span>H · Hard Identifiers (40%)</span>
						</div>
						<p className={styles.cardText}>
							PGP key fingerprints (1.00), Base58/EIP-55 crypto wallets (0.90), Jabber/emails
							(0.85), exact/normalized handles (0.60/0.45).
						</p>
					</div>
					<div className={styles.card}>
						<div className={styles.cardTitle}>
							<Fingerprint size={16} color="#a78bfa" />
							<span>S · Stylometry (25%)</span>
						</div>
						<p className={styles.cardText}>
							5000-dimension character 3-5 gram TF-IDF writeprint vector cosine similarity. Minimum
							300 characters required.
						</p>
					</div>
					<div className={styles.card}>
						<div className={styles.cardTitle}>
							<Clock size={16} color="#fbbf24" />
							<span>B · Behaviour (20%)</span>
						</div>
						<p className={styles.cardText}>
							24-hour UTC posting rhythm histogram, product category mix, and dark web trade
							vocabulary Jaccard similarity.
						</p>
					</div>
					<div className={styles.card}>
						<div className={styles.cardTitle}>
							<Network size={16} color="#fb923c" />
							<span>I · Infrastructure (15%)</span>
						</div>
						<p className={styles.cardText}>
							Passive server fingerprinting: TLS certificates, Favicon mmh3 hash, ETag header,
							server banner, /server-status leaks.
						</p>
					</div>
				</div>

				<div className={styles.bandPills}>
					<span className={styles.pillConfirmed}>CONFIRMED ≥ 0.85</span>
					<span className={styles.pillProbable}>PROBABLE 0.65 – 0.84</span>
					<span className={styles.pillPossible}>POSSIBLE 0.45 – 0.64</span>
					<span className={styles.pillWeak}>WEAK &lt; 0.45</span>
				</div>
			</TacticalPanel>

			{/* 5 Layer Architecture */}
			<TacticalPanel
				title="Five-Layer Forensic Pipeline"
				subtitle="From passive onion crawling to high-confidence entity resolution"
				icon={<Layers size={20} />}
			>
				<div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
					<div className={styles.layerStep}>
						<span className={styles.layerNum}>LAYER 1</span>
						<div>
							<div className={styles.cardTitle}>Collection (Tor SOCKS5 + Lab Target)</div>
							<p className={styles.cardText}>
								Passive, rate-limited crawling (1 req/2s) over Tor. Lab hidden service testbed
								verifies complete fidelity (33 requests, 74s, 424 text fields recovered
								byte-identically).
							</p>
						</div>
					</div>

					<div className={styles.layerStep}>
						<span className={styles.layerNum}>LAYER 2</span>
						<div>
							<div className={styles.cardTitle}>Extraction & Normalization</div>
							<p className={styles.cardText}>
								Base58Check (BTC) & EIP-55 (ETH) checksum validation drops corrupt data before
								linking. ObfusLex leet-speak normalizer decodes aliases like{" "}
								<code>Dr3adPirat3</code> → <code>dreadpirate</code>.
							</p>
						</div>
					</div>

					<div className={styles.layerStep}>
						<span className={styles.layerNum}>LAYER 3</span>
						<div>
							<div className={styles.cardTitle}>Passive Reconnaissance & Clearnet Correlation</div>
							<p className={styles.cardText}>
								Probes exposed server headers, TLS SANs, and Favicons to correlate dark web hidden
								services to clearnet IPs via passive Shodan/Censys pivots. No active exploitation.
							</p>
						</div>
					</div>

					<div className={styles.layerStep}>
						<span className={styles.layerNum}>LAYER 4</span>
						<div>
							<div className={styles.cardTitle}>Linking & Graph Clustering</div>
							<p className={styles.cardText}>
								NetworkX graph resolution finds connected components at the PROBABLE threshold
								(0.65). Transitively clusters multi-hop migrations (e.g., 1~9 + 1~16 connects 9~16).
							</p>
						</div>
					</div>

					<div className={styles.layerStep}>
						<span className={styles.layerNum}>LAYER 5</span>
						<div>
							<div className={styles.cardTitle}>Scoring, Dashboard & Forensic Export</div>
							<p className={styles.cardText}>
								Computes attribution confidence, feeds interactive D3 force graphs, live text
								analysis (/analyze), and generates court-ready PDF case dossiers via ReportLab.
							</p>
						</div>
					</div>
				</div>
			</TacticalPanel>

			{/* Real Cases & Hard Negatives */}
			<div className={styles.grid2}>
				<TacticalPanel
					title="Real-World Case Analogies"
					subtitle="Historical OPSEC failures automated by Dark Sentinel"
					icon={<Search size={20} />}
				>
					<div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
						<div className={styles.card}>
							<div className={styles.cardTitle}>Silk Road (Ross Ulbricht)</div>
							<p className={styles.cardText}>
								Caught via clearnet email promotion (<code>rossulbricht@gmail.com</code>), PGP key
								username <code>frosty</code>, and misconfigured server status leak.
							</p>
						</div>
						<div className={styles.card}>
							<div className={styles.cardTitle}>AlphaBay (Alexandre Cazes)</div>
							<p className={styles.cardText}>
								Caught via welcome email headers with personal email (
								<code>pimp_alex_91@hotmail.com</code>) and handle reuse across clearnet forums.
							</p>
						</div>
					</div>
				</TacticalPanel>

				<TacticalPanel
					title="Engine Refusal Gates & Integrity"
					subtitle="Refusing to guess when data is insufficient"
					icon={<Eye size={20} />}
				>
					<div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
						<div className={styles.card}>
							<div className={styles.cardTitle}>300-Character Stylometry Floor</div>
							<p className={styles.cardText}>
								Personas under 300 characters (e.g. <code>paperghost</code> with 152 chars) are
								marked <strong>NOT ASSESSED</strong>, redistributing weight rather than generating
								false confidence.
							</p>
						</div>
						<div className={styles.card}>
							<div className={styles.cardTitle}>Hard Negatives Correctly Held at WEAK</div>
							<p className={styles.cardText}>
								Pairs <code>2↔20</code> and <code>4↔15</code> share product categories but diverge
								on sleep schedule (0.08 & 0.18 B overlap). Both held below 0.25 (WEAK).
							</p>
						</div>
					</div>
				</TacticalPanel>
			</div>
		</div>
	);
}
