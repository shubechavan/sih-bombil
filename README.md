# Dark Sentinel v2 — Dark Web Threat Actor Attribution Platform

[![Test Suite](https://img.shields.io/badge/pytest-229%20passed-brightgreen.svg)](tests/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.13-blue.svg)](requirements.txt)
[![Status](https://img.shields.io/badge/status-Phases%200--3%20Operational%20%7C%20Phase%204%20In%20Progress-amber.svg)](docs/BUILD_PLAN.md)
[![License](https://img.shields.io/badge/use-Authorized%20Investigative%20Only-red.svg)](#legal--operational-disclaimer)

> **"Criminals do not get caught because Tor fails; they get caught because human operational security (OPSEC) fails."**

**Dark Sentinel v2** is an intelligence and cyber-forensic platform engineered to deanonymize, profile, and attribute illicit operators across the dark web (Tor Onion Services, I2P, and deep web forums).

Unlike legacy scanners that search for *isolated bad words or content on a single webpage*, Dark Sentinel v2 flips the paradigm to **Actor Attribution**: aggregating fragmented forensic footprints (PGP keys, cryptocurrency wallets, leet-speak aliases, communication handles), fingerprinting hidden service infrastructure for clearnet leak correlation, and linking migrated/rebranded threat actors across forums and marketplaces using multi-signal stylometry and behavioural profiling.

---

## The Operational Threat Landscape

The architectural design of Dark Sentinel v2 directly operationalizes the exact forensic vectors that brought down the most notorious darknet marketplace kingpins in cybercrime history:

* **Silk Road (Ross Ulbricht / "Dread Pirate Roberts")**:
  * *Alias & Forum Bleed*: Initial darknet market announcements placed under the handle `altoid` requesting inquiries at `rossulbricht@gmail.com`.
  * *Code & Bug Forensic Trail*: StackOverflow inquiries regarding PHP `curl` over Tor hidden services posted under his true name before being hastily renamed to `frosty`.
  * *Key Pair Cross-Reference*: PGP public key identities tagged with `frosty@frosty`.
  * *Infrastructure Misconfiguration*: Apache server status and CAPTCHA server leaks exposing the real clearnet IP in Reykjavik, Iceland.
* **AlphaBay (Alexandre Cazes / "Alpha02")**:
  * *Header Leakage*: Server welcome emails containing his personal email `pimp_alex_91@hotmail.com` in raw headers.
  * *Cross-Platform Re-use*: PGP keys and aliases cross-indexed between darknet vendor posts and French-Canadian webmaster forums.
  * *Financial Forensics*: High-velocity Bitcoin, Ethereum, and Monero fund consolidation.
* **Hansa Market, Wall Street Market, & Darkode**:
  * Infrastructure re-use, shared TLS certificates, Favicon hashes (MurmurHash3), server banners, and identical timezone posting distributions.

Dark Sentinel v2 automates these exact manual intelligence investigative methodologies into an evidence-driven, mathematically transparent attribution pipeline.

---

## Core System Architecture

The platform is organized into five decoupled layers:

```
LAYER 1 — COLLECTION & INGESTION
  tor_client.py          Tor SOCKS5 proxy pool, session handling, circuit rotation, rate limiter
  collectors/            Onion forum & marketplace crawlers
  fixtures/              Deterministic ground-truth seed corpus for verifiable offline evaluation

LAYER 2 — FORENSIC EXTRACTION & NORMALIZATION
  extract/identifiers.py Multi-regex & checksum validators (BTC Base58/Bech32, ETH EIP-55, XMR, LTC)
  extract/pgp.py         Armored OpenPGP block extraction, fingerprint computation, UID parsing
  extract/normalize.py   ObfusLex engine (leet-speak decoding, Unicode NFKD folding, separator strip)
  extract/gliner_extract.py GLiNER Zero-Shot Named Entity Recognition for PII extraction & redacting

LAYER 3 — RECONNAISSANCE & CLEARNET CORRELATION
  recon/fingerprint.py   Passive hidden service probing (HTTP headers, ETag, Favicon mmh3, server-status)
  recon/correlate.py     Cross-correlate onion fingerprints against clearnet observations (Shodan/Censys)

LAYER 4 — MULTI-SIGNAL ATTRIBUTION LINKING
  link/stylometry.py     Char 3-5 gram TF-IDF, function-word frequency, punctuation, 300-char floor
  link/behaviour.py      24h UTC posting histogram (timezone inference), trade vocab, Jaccard category
  link/resolve.py        Pairwise evidence resolution and transitive actor cluster closure
  link/graph.py          NetworkX graph engine for shortest evidence paths and community detection

LAYER 5 — SCORING, API & TACTICAL UI
  score/attribution.py   Weighted formula: A = 0.40*H + 0.25*S + 0.20*B + 0.15*I
  api/                   FastAPI tactical endpoints (/actors, /graph, /timeline, /recon, /export)
  ui/                    Next.js 14 mission control dashboard (dossiers, force graph, audit log)
```

---

## Attribution Mathematical Formulation

Attribution is probabilistic. Dark Sentinel v2 calculates a unified Attribution Confidence Score $A \in [0.0, 1.0]$:

$$A = w_H \cdot H + w_S \cdot S + w_B \cdot B + w_I \cdot I$$

Where:
* **$H$ (Hard Identifier Overlap — Weight: 0.40)**:
  * Same OpenPGP Fingerprint: `1.00`
  * Same Checksummed Cryptocurrency Wallet: `0.90`
  * Same Email / Jabber (XMPP) / Session ID: `0.85`
  * Same Mirror Onion URL: `0.80`
  * Exact Handle Match: `0.60`
  * Normalized Leet-Speak Collision: `0.45`
* **$S$ (Stylometric Similarity — Weight: 0.20 – 0.25)**:
  * Cosine similarity between 5000-dimensional writeprint vectors (character 3–5 grams, function word frequencies, punctuation distribution, capitalization ratio, emoji patterns, sentence/word length).
  * **Hard Forensic Floor**: Minimum **300 characters** of clean prose required. Below this floor, stylometry strictly returns `None` and is omitted from the score.
* **$B$ (Behavioural Similarity — Weight: 0.20 – 0.25)**:
  * 24-bucket UTC posting-hour histogram (timezone fingerprinting), day-of-week cadence, trade vocabulary overlap, and illicit product category Jaccard similarity.
* **$I$ (Infrastructure Overlap — Weight: 0.15)**:
  * TLS certificate serials, Subject Alternative Names (SANs) leaking clearnet hostnames, Shodan-compatible Favicon MurmurHash3 hashes, Apache/Nginx server banners, and ETags.

### Confidence Bands
| Band | Confidence Score | Investigative Meaning |
|---|---|---|
| **CONFIRMED** | $A \ge 0.85$ | High-probability attribution with corroborating hard cryptographic or multi-vector evidence. |
| **PROBABLE** | $0.65 \le A < 0.85$ | Strong behavioral, stylometric, and infrastructure overlap; actionable investigative lead. |
| **POSSIBLE** | $0.45 \le A < 0.65$ | Plausible link; warrants analyst verification and subpoena/warrant corroboration. |
| **WEAK** | $A < 0.45$ | Below actionable threshold; dismissed to prevent false positive link pollution. |

### Rigorous Evidentiary Safeguards
1. **Wallet Validation Enforcement**: Every Bitcoin address (Base58Check and Bech32), Ethereum address (EIP-55 checksum), and Monero address is mathematically verified. Invalid checksums are dropped immediately and logged to prevent phantom link graph contamination.
2. **Transparent Evidence Trails**: A bare number is legally and forensically useless. Every link record stores an immutable JSONB array of explicit evidence citations (`"same PGP fingerprint A4F57A..."`, `"84% posting-hour overlap"`, `"writeprint cosine 0.81"`).
3. **Renormalization of Unmeasured Signals**: If a signal is legitimately unmeasured (e.g. text under 300 characters, or lack of direct infrastructure control), its weight is redistributed proportionately rather than treated as a punitive zero.

---

## Current Project Status & Gap Analysis

```
[Phase 0] Database Schema & Ground Truth Fixtures   --> [COMPLETED] (schema_v2.sql, db.py, fixtures)
[Phase 1] Forensic Extraction & Normalization       --> [COMPLETED] (extract/ - 100% prose recall)
[Phase 2] Stylometric, Behavioural & Graph Engine   --> [COMPLETED] (link/, score/ - 1.000 Precision)
[Phase 3] Passive Recon & Clearnet Correlation     --> [COMPLETED] (recon/ - Shodan/ETag/Favicon)
[Phase 4] FastAPI Endpoints & Next.js Tactical UI   --> [IN PROGRESS / NEEDED]
[Phase 5] Autonomous Mode & PDF Court Dossier       --> [ROADMAP / NEEDED]
```

### Verified Test Suite
The backend core has been extensively tested with **229 unit and integration tests passing**:
* `tests/test_attribution.py`: Isolated formula bounds, weight presets, edge-case renormalizations.
* `tests/test_identifiers.py`: Checksummed wallet verification, PGP armor parsing, PII extraction.
* `tests/test_fixtures.py`: Stylometric separation margins on ground-truth actor migrations.
* `tests/test_infra.py`: Persona-level vs. site-level infrastructure isolation.
* `tests/test_linking.py`: Pairwise resolution, transitive closures, and hard negative refusals.
* `tests/test_recon.py`: Passive HTTP fingerprinting, MurmurHash3 hashing, and Shodan correlation.

---

## What Is Needed to Complete the Project

To elevate Dark Sentinel v2 from a battle-tested algorithmic engine into a production-grade operations center, the following components are prioritized:

### 1. Phase 4: Tactical API Endpoints (`api/`)
Build out FastAPI routers to bridge the data model to the frontend:
* `GET /actors`: Paginated list of resolved threat actors filtered by category, confidence band, and first/last seen timestamps.
* `GET /actors/{id}`: In-depth actor profile dossier (all grouped personas, verified cryptocurrency wallets, PGP fingerprints, posting history, and migration timeline).
* `GET /graph`: NetworkX node-link payload for interactive D3 / Cytoscape force-directed graph rendering with confidence score filtering.
* `GET /timeline`: Temporal activity distribution across sources for pattern-of-life analysis.
* `GET /recon/{onion}`: Infrastructure fingerprinting report, misconfiguration flags, and clearnet correlation candidates.
* `POST /scan`: Asynchronous scan initiation (fixtures or live Tor targets) returning job tracking IDs.
* `GET /export/{fmt}`: Instant export of query results in CSV, JSON, and forensic briefing PDF.

### 2. Phase 4: Tactical Frontend UI (`ui/app`)
Re-skin and replace the legacy v1 page-centric components with the Actor Attribution suite:
* **Attribution Matrix (`/actors`)**: TanStack data table with quick filtering, confidence band badges, and wallet count chips.
* **Actor Dossier (`/actors/[id]`)**: Detailed profile showcasing cryptographic proofs, leet-speak alias history, stylometric writing samples, and behavioural posting clock.
* **Interactive Threat Graph (`/graph`)**: Force-directed link graph showing persona clusters, edge thickness reflecting score $A$, and slide-out evidence drawer upon clicking any edge.
* **Recon & Misconfig Viewer (`/recon`)**: Visual representation of exposed `/server-status` pages, SSL cert SAN clearnet leaks, and Shodan pivot matches.

### 3. Phase 5: Autonomous Daemon & Audit Logging
* **Continuous Monitoring Loop**: APScheduler daemon periodically scanning monitored onions, running incremental extractions, and updating the link graph.
* **Cryptographic Chain of Custody**: Every scan action records operator identity, UTC timestamp, and SHA-256 hash of the parameters in `scans` table.
* **Forensic PDF Dossier Generator (`export/report.py`)**: Court-admissible intelligence summary complete with methodology disclaimer, evidence matrix, and signature block.

---

## Quickstart Guide

### Prerequisites
* Python 3.11+
* PostgreSQL 15+ (or Docker)
* Node.js 18+ (for UI)
* Tor daemon listening on `127.0.0.1:9050` (optional, only for live scanning)

### 1. Environment Configuration
```bash
cp .env.example .env
# Edit .env with your PostgreSQL credentials
```

### 2. Database Initialization
```bash
# Apply migrations (idempotent and safe)
psql -U postgres -d darksentinel -f schema_v2.sql

# Seed demo corpus fixtures
python scripts/load_fixtures.py --reset
```

### 3. Run Forensic Ingestion & Evaluation
```bash
# Ingest personas, identifiers, and posts from fixtures
python scripts/ingest.py --source fixtures

# Run the algorithmic evaluation against ground truth
python scripts/evaluate.py
```

### 4. Run Unit Tests
```bash
pytest -v
```

---

## Legal & Operational Disclaimer

> **IMPORTANT**: Dark Sentinel v2 is designed exclusively for authorized law enforcement, national security, academic threat research, and defensive cyber-intelligence operations. All reconnaissance modules operate strictly **passively** (analyzing publicly served headers, certificates, and content). The system does not conduct active exploitation, authentication bypasses, denial-of-service, or unauthorized access. All outputs represent probabilistic investigative leads requiring human corroboration, never definitive legal conclusions.
