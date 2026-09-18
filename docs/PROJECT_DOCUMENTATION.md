# Dark Sentinel v2 — Technical Architecture & Forensic Attribution Manual

**Author**: Senior Darknet OSINT / Threat Intelligence Engineer  
**Classification**: Authorized Investigative Use Only  
**Target Platform**: Dark Sentinel v2 (Successor to Dark Sentinel v1)  
**Corpus / Problem Statement**: SIH Threat Actor Attribution & Deanonymization across Onion Networks  

---

## 1. Executive Summary & Forensic Heritage

In the history of cybercrime investigations, darknet operators do not fall because the Tor protocol's onion routing cryptography was broken. They fall because of **operational security (OPSEC) failures across time, platforms, and psychological habits**.

When investigating and apprehending kingpins behind networks like **Silk Road**, **AlphaBay**, and **Hansa Market**, investigators exploit four persistent human failure modes:

1. **Cryptographic and Identifier Leakage ($H$)**: Re-using an OpenPGP key pair across aliases, posting un-mixed cryptocurrency addresses (Bitcoin, Ethereum, Monero), or carrying forward Jabber/XMPP, Session, or Telegram handles.
   * *The Silk Road Case*: Ross Ulbricht published an early inquiry on Bitcointalk under the handle `altoid`, citing `rossulbricht@gmail.com`. Years later, he re-used the key tag `frosty@frosty` matching his local machine username.
   * *The AlphaBay Case*: Alexandre Cazes included his personal email `pimp_alex_91@hotmail.com` in the header configuration of welcome emails dispatched by the AlphaBay server daemon, while re-using his `Alpha02` alias from French-Canadian web development forums.
2. **Stylometric & Linguistic Fingerprinting ($S$)**: Human writing carries involuntary micro-habits: sentence length variance, function word distribution, punctuation cadence, capitalization idiosyncrasies, greeting/sign-off tropes, and distinctive trade vocabulary.
   * *The Ulbricht Stylometry*: DPR’s libertarian philosophy essays, semicolon usage, and specific capitalization rules correlated strongly with Ulbricht’s published writings.
3. **Temporal & Behavioural Footprints ($B$)**: An actor's biological sleep cycle, active hours, and transaction types create a recognizable distribution across 24-hour UTC time buckets, regardless of the VPN or Tor circuit used.
4. **Infrastructure & Daemon Misconfigurations ($I$)**: Web servers hidden behind Tor services frequently leak metadata: unstripped Apache `/server-status` pages, ETag hashes generated from physical filesystem inodes, TLS certificate Subject Alternative Names (SANs) containing clearnet domains, and favicon MurmurHash3 signatures matching Shodan records.

**Dark Sentinel v2** is engineered to automate, formalize, and mathematically score these exact investigative techniques into a unified, court-defensible threat intelligence platform.

---

## 2. System Architecture & Directory Map

```
dark-sentinel-v2/
├── .env.example              # Environment variables template (PG_*, TOR_SOCKS, OPERATOR_ID)
├── CLAUDE.md                 # Project directives, attribution rubric, and coding rules
├── README.md                 # Project overview, quickstart, and operational disclaimer
├── db.py                     # SQLAlchemy 2.x engine, connection pool, session factory, UTC utilities
├── docker-compose.yml        # Multi-container topology (PostgreSQL 16, Tor Proxy, API, UI)
├── requirements.txt          # Python dependencies (PyTorch/GLiNER, scikit-learn, networkx, mmh3)
├── schema_v2.sql             # Idempotent DDL: 10 tables, indexes, audit trail
│
├── docs/
│   ├── BUILD_PLAN.md         # 5-phase historical build plan and engineering rationale
│   └── PROJECT_DOCUMENTATION.md # This comprehensive technical reference manual
│
├── extract/                  # Layer 2: Forensic Extraction & Normalization
│   ├── identifiers.py        # Checksum-validated crypto, PGP, email, jabber, session, telegram
│   ├── normalize.py          # ObfusLex leet-speak decode, Unicode NFKD fold, separator stripping
│   ├── pgp.py                # OpenPGP armored key parser, fingerprint calculator, UID extractor
│   └── gliner_extract.py     # Zero-shot NER extraction and PII redaction pipeline
│
├── recon/                    # Layer 3: Passive Reconnaissance & Clearnet Correlation
│   ├── fingerprint.py        # Passive onion surface probe (ETag, mmh3 favicon, banners, status)
│   ├── correlate.py          # Correlation scoring against clearnet Shodan/Censys observations
│   └── tor.py                # Safe Tor SOCKS5 session manager with rate limiting and timeout guards
│
├── link/                     # Layer 4: Multi-Signal Linking & Resolution
│   ├── stylometry.py         # 5000-dim TF-IDF char 3-5 gram writeprint vectorizer & cosine similarity
│   ├── behaviour.py          # 24h UTC posting histogram, day-of-week profile, category Jaccard
│   ├── infra.py              # Persona-level vs. site-level infrastructure attribution guards
│   ├── resolve.py            # Pairwise attribution resolution and transitive cluster closure
│   └── graph.py              # NetworkX graph representation, community detection, shortest evidence path
│
├── score/                    # Layer 5: Confidence Scoring Engine
│   └── attribution.py        # Attribution formula implementation, weight presets, explainable evidence
│
├── fixtures/                 # Synthetic Ground-Truth Forensic Corpus
│   ├── ground_truth.json     # True actor cluster answer key for precision/recall verification
│   ├── sources.json          # Market Alpha, Forum Beta, Market Gamma configurations
│   ├── pgp_blocks.json       # Armored OpenPGP public key test blocks
│   ├── infra_findings.json   # Simulated passive recon findings per source
│   ├── clearnet_obs/         # Simulated Shodan observations for correlation tests
│   ├── market_alpha/         # 12 vendors, profiles, listings, feedback
│   ├── forum_beta/           # 30 forum discussion threads and actor posts
│   └── market_gamma/         # 4 rebranded vendor migrations from Market Alpha
│
├── legacy/                   # Reusable legacy components from Dark Sentinel v1
│   ├── darksearch.py         # Battle-tested Tor crawler, circuit rotator, engine ranking
│   ├── llm.py                # GLiNER loading logic and consensus classification
│   ├── obfuslex_engine.py    # Leet speak permutation matrices
│   └── alert_api.py          # FastAPI v1 alert server (to be upgraded)
│
├── scripts/                  # Operational Utility & Evaluation Scripts
│   ├── load_fixtures.py      # Seed database with the synthetic demo corpus
│   ├── ingest.py             # Run identifier extraction and ingest posts into database
│   ├── evaluate.py           # Benchmark linking engine against ground_truth.json (Precision/Recall)
│   ├── gen_fixtures.py       # Corpus generator
│   ├── gen_pgp_blocks.py     # OpenPGP block generator
│   └── wallet_codec.py       # Base58Check, Bech32, EIP-55, and Monero validation math
│
├── tests/                    # Pytest Test Suite (229 passed)
│   ├── test_attribution.py   # Attribution formula logic, bounds, and weight renormalizations
│   ├── test_identifiers.py   # Wallet checksums, PGP parsing, and false-positive rejection
│   ├── test_fixtures.py      # Stylometric separation margins and ablation evaluations
│   ├── test_recon.py         # HTTP header capture, favicon hash, and misconfig detection
│   ├── test_correlate.py     # Clearnet correlation scoring rules
│   ├── test_linking.py       # Pairwise resolution, transitive closures, and negative case refusals
│   └── test_infra.py         # Infrastructure attribution boundary isolation
│
└── ui/                       # Layer 5: Mission Control Frontend (Next.js 14)
    ├── app/                  # Next.js App Router (Alerts, Analytics, Threats from v1)
    ├── components/           # Tactical UI components (NavRail, ThreatRow, ConfidenceMeter)
    └── styles/               # CSS modules and design tokens
```

---

## 3. The Attribution Engine: Mathematical Derivation

Dark Sentinel v2 rejects opaque black-box "AI scores". In court proceedings or high-stakes intelligence briefings, an analyst must provide transparent, explainable evidence.

The core attribution score $A \in [0.0, 1.0]$ is computed as:

$$A = w_H \cdot H + w_S \cdot S + w_B \cdot B + w_I \cdot I$$

```
Target Weights (Measured Preset):
  w_H = 0.40  (Hard Identifier Overlap)
  w_S = 0.20  (Stylometric Cosine Similarity)
  w_B = 0.25  (Behavioural / Temporal Overlap)
  w_I = 0.15  (Infrastructure Overlap)
```

### 3.1 Hard Identifier Overlap ($H$)
$H$ combines multiple independent identifier matches using a probabilistic **Noisy-OR** formulation to prevent saturation while rewarding independent corroboration:

$$H = 1 - \prod_{k \in \text{matches}} (1 - m_k)$$

| Identifier Match Type | Base Match Weight ($m_k$) | Forensic Justification |
|---|---|---|
| OpenPGP Fingerprint | `1.00` | Cryptographically unique asymmetric key pair; near-zero accidental collision. |
| Checksummed Crypto Wallet | `0.90` | Financial destination address (BTC, ETH, XMR, LTC); high intentionality. |
| Email / Jabber / Session ID | `0.85` | Direct asynchronous contact channels. |
| Mirror Onion URL | `0.80` | Shared vanity hidden service address published across platforms. |
| Exact Handle Match | `0.60` | Identical alphanumeric string; possible impersonation, requires corroboration. |
| Normalized Handle Collision | `0.45` | Match resolved after ObfusLex leet-speak decode (`Dr3ad_P1rat3` $\to$ `dreadpirate`). |

### 3.2 Stylometric Similarity ($S$)
Human writing exhibits unique stylometric invariants. The engine processes post text through:
1. **Extraction Floor**: Requires $\ge 300$ characters of scrubbed prose. Below this threshold, $S = \text{None}$.
2. **Feature Representation (Writeprint Vector)**:
   * Character 3-gram, 4-gram, and 5-gram TF-IDF ($\text{max\_features} = 5000$).
   * Function word frequencies (prepositions, conjunctions, auxiliary verbs).
   * Punctuation distributions (semicolons, em-dashes, consecutive ellipses).
   * Capitalization frequency ratio.
   * Average word and sentence length distributions.
   * Emoji and ASCII emoticon profile.
3. **Similarity**: Cosine similarity between normalized $L_2$ writeprint vectors.

### 3.3 Behavioural & Temporal Similarity ($B$)
Actors operate within circadian and operational constraints:
* **Posting-Hour Histogram**: 24 normalized bins representing activity in UTC. Compared using cosine similarity, providing timezone footprinting independent of user IP.
* **Category Affinity**: Jaccard index of illicit marketplace categories (e.g. `cannabis`, `stimulants`, `fraud_guides`, `malware`).
* **Trade Vocabulary**: Overlap ratio of specialized slang, escrow terminology, and shipping carrier jargon.

### 3.4 Infrastructure Overlap ($I$)
* Evaluates shared hosting signatures when personas control dedicated infrastructure (such as vendor shop mirrors).
* Measures TLS certificate serial match (`1.0`), clearnet SAN leaks (`0.95`), Shodan MurmurHash3 favicon match (`0.80`), ETag match (`0.70`), and server banner sequence match (`0.40`).

### 3.5 Renormalization of Unmeasured Signals
If a component cannot be assessed (for example, text is under 300 characters, or infrastructure is not persona-controlled), Dark Sentinel v2 **redistributes the unmeasured weight** across the active components:

$$A_{\text{renorm}} = \frac{\sum_{k \in \text{measured}} w_k \cdot X_k}{\sum_{k \in \text{measured}} w_k}$$

Every output record explicitly states which components contributed and why unmeasured components were excluded, preventing artificial score degradation.

---

## 4. Evaluation Benchmark Results

Running `python scripts/evaluate.py` benchmarks the linking engine against the ground truth of 20 personas across 3 platforms (190 pairwise comparisons):

```
──────────────────────────────────────────────────────────────────────────────
PAIRWISE EVALUATION SUMMARY
──────────────────────────────────────────────────────────────────────────────
  Threshold             Precision    Recall        F1    TP    FP    FN
  >= CONFIRMED (0.85)       1.000     0.750     0.857     6     0     2
  >= PROBABLE (0.65)        1.000     0.750     0.857     6     0     2
  >= POSSIBLE (0.45)        1.000     0.750     0.857     6     0     2

  Accepted Positive Pairs (Direct Evidence):
    1~16  Dr3adPirat3  ~ BlackSailsRX  : 0.925 [CONFIRMED] (H=1.000, S=0.857, B=0.858)
    3~18  Vect0rShop   ~ V3ct0r_Supply : 0.909 [CONFIRMED] (H=1.000, S=0.870, B=0.795)
    4~19  silk_hands   ~ SilkHands     : 0.884 [CONFIRMED] (H=0.945, S=0.841, B=0.819)
    1~9   Dr3adPirat3  ~ Dread_P1rate  : 0.876 [CONFIRMED] (H=0.917, S=0.762, B=0.902)
    2~17  NordicPharm  ~ NordPharmaEU  : 0.862 [CONFIRMED] (H=0.900, S=0.777, B=0.870)
    2~10  NordicPharm  ~ nordic_pharm  : 0.853 [CONFIRMED] (H=0.917, S=0.721, B=0.855)

  Cluster-Resolved Pairs (Transitive Closure):
    9~16  Dread_P1rate ~ BlackSailsRX  : Resolved via Node 1 (Dr3adPirat3)
    10~17 nordic_pharm ~ NordPharmaEU  : Resolved via Node 2 (NordicPharm)

  Separation Margin:
    Weakest Accepted Positive : 0.853
    Strongest Rejected Pair   : 0.345
    Discrimination Gap        : +0.508
──────────────────────────────────────────────────────────────────────────────
HARD NEGATIVES & REFUSAL SAFEGUARDS (All Passed)
──────────────────────────────────────────────────────────────────────────────
  [PASS] 2~20 (NordicPharm ~ AtlasMeds) : 0.242 [WEAK] (Shares formal register, but distinct actor)
  [PASS] 4~15 (silk_hands ~ plainbagel) : 0.228 [WEAK] (Shares sentence length, but distinct actor)
  [PASS] Refusal on Short Text         : persona 7 (paperghost, 152 chars) -> S returned None
  [PASS] Refusal on Bad Wallets        : persona 8 (CryoVault) -> 2 corrupt checksums dropped
```

---

## 5. Comprehensive Gap Analysis: What Is Needed Next

While Phases 0 through 3 are fully verified, the project currently requires the implementation of **Phase 4** and **Phase 5** to deliver a complete end-to-end tactical operations platform.

### Priority 1: Phase 4 — Tactical FastAPI Backend (`api/`)
Currently, `api/` does not exist in the repository; only the legacy `legacy/alert_api.py` exists from Dark Sentinel v1. We must construct a clean, modular FastAPI application:

* **File: `api/main.py`**:
  * FastAPI application bootstrap, CORS middleware, global error handling.
  * Dependency injection for database sessions (`get_db`).
* **File: `api/routers/actors.py`**:
  * `GET /api/v2/actors`: Filter by category, confidence band, source ID, min score.
  * `GET /api/v2/actors/{id}`: Detailed profile with grouped personas, verified identifiers, post excerpts, and link history.
* **File: `api/routers/graph.py`**:
  * `GET /api/v2/graph`: Returns JSON payload formatted for D3/Cytoscape (`{nodes: [...], edges: [...]}`).
  * Dynamic threshold filtering (`?min_score=0.65`) and cluster partitioning.
* **File: `api/routers/timeline.py`**:
  * `GET /api/v2/timeline`: Aggregated post counts and migration events bucketed by week/month for activity pattern analysis.
* **File: `api/routers/recon.py`**:
  * `GET /api/v2/recon/{onion}`: Returns `infra_findings` and `infra_correlations` for a target onion service.
* **File: `api/routers/scan.py`**:
  * `POST /api/v2/scan`: Initiates background scan job, logs to `scans` table with SHA-256 audit hash.

### Priority 2: Phase 4 — Modernization of Next.js Frontend (`ui/app`)
The `ui/app` directory currently contains the Dark Sentinel v1 interface (centered on alerts, PII redaction, and threats). It must be updated to an **Actor Attribution Tactical Console**:

* **Route: `/actors`**:
  * TanStack Table listing all identified threat entities.
  * Badges for confidence bands (`CONFIRMED`, `PROBABLE`, `POSSIBLE`, `WEAK`).
  * Chips displaying verified cryptocurrency wallets, PGP fingerprints, and active sources.
* **Route: `/actors/[id]`**:
  * Forensic Actor Dossier.
  * Tab 1: **Cryptographic Identifiers** (Fingerprints, addresses with validation status).
  * Tab 2: **Linked Personas & Evidence** (Detailed score breakdown $H, S, B, I$ and raw citations).
  * Tab 3: **Stylometric & Linguistic Analysis** (Top n-grams, vocabulary traits, writeprint).
  * Tab 4: **Circadian Clock** (24-hour UTC posting histogram chart).
* **Route: `/graph`**:
  * Interactive force-directed link graph.
  * Node colors indicate threat category; edge thickness reflects attribution confidence.
  * Clicking an edge opens a side drawer displaying the complete evidentiary basis.
* **Route: `/recon`**:
  * Hidden service infrastructure leak dashboard.
  * Displays server banners, Favicon mmh3 hashes, ETag matching, and clearnet correlation cards.

### Priority 3: Phase 5 — Autonomous Scheduler & Case Report Generation
* **Autonomous Ingestion Worker (`scripts/scheduler.py` or APScheduler integration)**:
  * Periodic background loop scanning registered onions, triggering incremental feature extraction, and updating `links`.
  * Every scan automatically appends an immutable audit record in `scans` table with operator ID and payload SHA-256.
* **Forensic PDF Case Dossier Generator (`export/report.py`)**:
  * Law-enforcement grade PDF generation using ReportLab.
  * Sections: Executive Summary, Target Actor Profile, Cryptographic Proofs, Stylometric Evidence, Infrastructure Correlation, Chain of Custody, and Legal Attestation block.

### Priority 4: Live Harvesters & Tor Proxy Pool Integration (`collectors/`)
* Create modular harvesters connecting the battle-tested Tor SOCKS5 proxy logic in `legacy/darksearch.py` with `extract/`:
  * `forum_collector.py`: Parser for Dread-style forums (thread titles, post bodies, author timestamps).
  * `market_collector.py`: Parser for marketplace vendor profiles, PGP blocks, and mirror listings.

---

## 6. Execution Roadmap & Milestones

| Milestone | Deliverable | Primary Objective |
|---|---|---|
| **Phase 4.1** | `api/` FastAPI Routers | Provide clean REST API for actors, graph, timeline, and recon. |
| **Phase 4.2** | `ui/app` Attribution Console | Replace v1 alerts pages with Actor Dossier, Table, and Graph UI. |
| **Phase 5.1** | `export/report.py` | Generate court-admissible PDF intelligence briefs. |
| **Phase 5.2** | Autonomous Crawler Daemon | Background APScheduler loop with cryptographic audit logging. |
| **Phase 5.3** | Docker Compose Verification | One-command deployment: `docker compose up -d`. |

---

## 7. Operational & Evidentiary Standard

Dark Sentinel v2 is designed to adhere to strict investigative and legal evidentiary standards:
1. **Chain of Custody**: Every database insert records source URL, raw body hash (`body_hash`), and ingestion timestamp.
2. **Defensible Algorithms**: No probabilistic prediction is presented without its underlying mathematical components ($H, S, B, I$) and explicit corroborating citations.
3. **Passive Integrity**: The platform strictly listens and observes; it never conducts active intrusions, preserving the integrity of intelligence leads.
