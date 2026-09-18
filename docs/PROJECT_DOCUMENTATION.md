# Dark Sentinel v2 — Technical Architecture & Attribution Manual

**Classification**: Authorized Investigative Use Only
**Problem statement**: SIH — threat actor attribution and deanonymization across onion networks
**Scope of this document**: Phases 0–3, which are built and measurable. Phases 4–5 are specified in
§7 as work not yet done.

Every figure, path and behaviour in this document is reproducible on a clean checkout with the
command printed beside it. Nothing here describes a component that does not exist.

---

## 1. Why this works at all

Darknet operators are not caught by breaking onion routing. They are caught because identity leaks
across time and platforms in four recurring ways, and each maps to a term in the attribution
formula.

**Identifier reuse (`H`).** Carrying an OpenPGP key, a wallet address or a Jabber handle from one
alias to the next. Ross Ulbricht advertised Silk Road on Bitcointalk as `altoid` citing
`rossulbricht@gmail.com`, and later carried the key tag `frosty@frosty` matching his machine's
username. Alexandre Cazes reused his `Alpha02` alias from French-Canadian webmaster forums, and
AlphaBay's welcome-mail headers carried `pimp_alex_91@hotmail.com`.

**Writing habit (`S`).** Sentence-length variance, function-word distribution, punctuation cadence,
capitalisation quirks and greeting tropes survive a rebrand because they are involuntary.

**Circadian rhythm (`B`).** Sleep cycles and working hours produce a recognisable distribution over
24 UTC buckets regardless of which circuit carried the request.

**Infrastructure misconfiguration (`I`).** Unstripped `/server-status`, ETags derived from
filesystem inodes, TLS SANs naming clearnet hosts, favicon hashes matching public scan data.

This platform automates the first three end to end. The fourth is built and, on the shipped corpus,
correctly declines to produce a number — §5 explains why that is a result rather than a gap.

---

## 2. Directory map

Exactly what is in the repository. Directories not listed do not exist.

```
dark-sentinel-v2/
├── CLAUDE.md                 Project directives, attribution rubric, coding rules
├── README.md                 Overview, quickstart, reproducible evaluation output
├── db.py                     SQLAlchemy 2.x models, engine, session factory, UTC helpers,
│                             IDENTIFIER_WEIGHTS, BAND_THRESHOLDS, require_schema
├── schema_v2.sql             Re-runnable DDL: 11 tables + 1 view, indexes, constraints
├── docker-compose.yml        PostgreSQL 16 only. tor/api/ui services are commented out.
├── requirements.txt          torch + GLiNER, scikit-learn, networkx, SQLAlchemy, mmh3,
│                             cryptography, requests + PySocks
│
├── docs/
│   ├── BUILD_PLAN.md         5-phase build plan, reuse map, and the known-corpus-gap record
│   └── PROJECT_DOCUMENTATION.md  This document
│
├── extract/                  Layer 2 — forensic extraction
│   ├── identifiers.py        Checksum-validated wallets, PGP, email, jabber, session, telegram,
│   │                         onion mirrors; returns {type, value, raw_context, confidence}
│   ├── normalize.py          leet_decode + Unicode NFKD fold + separator strip; normalize_identifier
│   ├── pgp.py                Armored key parsing, fingerprint computation, crc24, UID extraction
│   └── gliner_extract.py     Zero-shot NER in extract and redact modes
│
├── recon/                    Layer 3 — passive reconnaissance
│   ├── fingerprint.py        Six-path passive probe, HostRateLimiter, misconfig rubric
│   ├── correlate.py          Onion↔clearnet scoring, ClearnetProvider interface
│   └── tor.py                Import-safe shim onto legacy/darksearch.py's Tor session
│
├── link/                     Layer 4 — multi-signal linking
│   ├── stylometry.py         Char 3–5 gram TF-IDF writeprints, identifier masking, 300-char floor
│   ├── behaviour.py          Posting-hour histogram, category Jaccard, trade vocab, day-of-week
│   ├── infra.py              The I term: persona-controlled vs site-broadcast
│   ├── resolve.py            Pairwise resolution, evidence assembly, link storage
│   └── graph.py              NetworkX components, evidence paths, transitive closure
│
├── score/
│   └── attribution.py        The formula, weight presets, noisy-OR, renormalisation, bands
│
├── fixtures/                 Synthetic ground-truth corpus
│   ├── ground_truth.json     Answer key: actors, positive pairs, hard negatives, refusal cases
│   ├── sources.json          3 sources
│   ├── pgp_blocks.json       4 armoured OpenPGP key blocks
│   ├── infra_findings.json   3 recon findings, one per source
│   ├── clearnet_obs/         10 Shodan-shaped observations
│   ├── market_alpha/         8 personas, 79 posts
│   ├── forum_beta/           7 personas, 70 posts
│   └── market_gamma/         5 personas, 51 posts
│
├── legacy/                   Dark Sentinel v1, kept for reuse
│   ├── darksearch.py         Tor SOCKS5 session builder, proxy pool, adaptive timeouts
│   ├── llm.py                GLiNER loading, consensus classification
│   ├── obfuslex_engine.py    leet_decode
│   ├── alert_api.py          v1 FastAPI alert server
│   └── fix_postgres.sql      v1 migration style
│
├── scripts/
│   ├── apply_schema.py       Apply schema_v2.sql; --check reports drift without writing
│   ├── load_fixtures.py      Seed the corpus; --reset truncates first
│   ├── ingest.py             Extract identifiers from prose into the database
│   ├── evaluate.py           Benchmark against ground_truth.json
│   ├── gen_fixtures.py       Corpus generator
│   ├── gen_pgp_blocks.py     OpenPGP block generator
│   ├── content_templates.py  Post text templates for the generator
│   ├── style_profiles.py     Per-persona style parameters for the generator
│   └── wallet_codec.py       Base58Check, Bech32, EIP-55 validation math
│
├── tests/                    7 modules, 230 tests
│   ├── test_attribution.py   test_identifiers.py   test_fixtures.py
│   ├── test_linking.py       test_infra.py         test_recon.py
│   └── test_correlate.py
│
└── ui/                       Dark Sentinel v1 Next.js app. NOT wired to this backend.
    ├── app/  components/  hooks/  lib/  styles/
```

**Not present**: `api/`, `export/`, `collectors/`, `tor_client.py`. Those are Phases 4–5 (§7).

---

## 3. Data model

`schema_v2.sql` is idempotent — `CREATE TABLE IF NOT EXISTS` plus `ADD COLUMN IF NOT EXISTS` plus
guarded `DO $$` constraint blocks — so re-running it migrates a stale database and no-ops an
up-to-date one.

```bash
python scripts/apply_schema.py --check    # reports drift, writes nothing
python scripts/apply_schema.py            # applies
```

| table | role |
|---|---|
| `sources` | A site, onion or clearnet, with `last_scan_at` |
| `personas` | One handle on one source — the raw observation |
| `actors` | The resolved entity; one actor owns one or more personas |
| `identifiers` | `{type, value, value_norm, first_seen, last_seen}` |
| `persona_identifiers` | Many-to-many join |
| `posts` | Text corpus with `posted_at` and `body_hash` (sha256, dedupe key per persona) |
| `writeprints` | Cached feature vector per persona, keyed by `feature_version` |
| `links` | Persona↔persona edge: `score`, `band`, `h/s/b/i_score`, `evidence` JSONB, `method` |
| `infra_findings` | Recon output per onion, including `header_order` and `misconfig_score` |
| `infra_correlations` | Onion↔clearnet candidate: `match_type`, `score`, `evidence`, `provider` |
| `scans` | Audit log: operator, mode, data source, `action_hash`, status, timestamps |

Plus the view `v_actor_summary`. `db.REQUIRED_TABLES` holds the 11 tables every entry point checks
via `require_schema()` before touching data.

Two storage details worth knowing:

- **`infra_findings` has no natural key.** A real scan appends a new observation every time, so
  there is no `UNIQUE` on `onion_url` and `ON CONFLICT` is unavailable. Idempotency is
  delete-then-insert by onion.
- **`header_order` is a separate JSONB array.** PostgreSQL `JSONB` re-sorts object keys by length
  then bytewise, so `headers` cannot preserve the order the server sent. Header order is a weak
  fingerprint of the software stack that the banner rule depends on, so it is stored as an array,
  which JSONB does keep ordered. An empty array means *order unknown* and the banner rule declines
  rather than comparing a sequence the storage layer invented.

---

## 4. The attribution engine

```
A = w_H·H + w_S·S + w_B·B + w_I·I
```

```
Presets (score/attribution.py):
  claude_md           H 0.40   S 0.25   B 0.20   I 0.15
  measured (default)  H 0.40   S 0.20   B 0.25   I 0.15
```

Bands: `CONFIRMED ≥ 0.85`, `PROBABLE 0.65–0.85`, `POSSIBLE 0.45–0.65`, `WEAK < 0.45`.

### 4.1 Hard identifier overlap (H)

Independent matches combine by noisy-OR so corroboration accumulates without exceeding 1.0:

```
H = 1 − Π (1 − mₖ)
```

| match | mₖ | justification |
|---|---|---|
| OpenPGP fingerprint | 1.00 | Cryptographically unique; near-zero accidental collision |
| Checksum-valid wallet | 0.90 | BTC / ETH / XMR / LTC; deliberate financial destination |
| Email / Jabber / Session | 0.85 | Direct contact channel |
| Mirror onion | 0.80 | Hidden service address published across platforms |
| Exact handle reuse | 0.60 | Identical string; impersonation is possible |
| Normalized handle collision | 0.45 | After leet decode: `Dr3ad_P1rat3` → `dreadpirate` |

Exact and normalized handle matches never stack — they are one piece of evidence observed at two
strictnesses. An empty match set returns `0.0`, which is a **measurement** (the identifier sets were
compared and were disjoint), not an unmeasured `None`.

**Wallet validation is mandatory.** Base58Check for legacy BTC/LTC, Bech32 for segwit, EIP-55 for
ETH. A regex-only match on a hex string would link everyone to everyone; failed checksums are
dropped, not stored. `scripts/wallet_codec.py` holds the math and `tests/test_identifiers.py`
verifies it against the two deliberately corrupted fixture wallets.

### 4.2 Stylometric similarity (S)

1. **Identifier masking first.** Wallets, fingerprints and onions are stripped, both by value and
   structurally, so a shared identifier cannot re-enter the score as a shared writing habit.
2. **Floor.** Below 300 characters of masked prose, `similarity()` returns `None` and a paired
   `refusal_reason()` explains why. Two sentences do not make a writeprint.
3. **Features.** Character 3–5 gram TF-IDF (`max_features=5000`), function-word frequencies,
   punctuation ratios, capitalisation ratio, type-token ratio, average word and sentence length.
4. **Comparison.** Cosine similarity between L2-normalized vectors. Writeprints built by different
   extractor versions refuse to compare rather than silently returning a meaningless number.

### 4.3 Behavioural similarity (B)

| sub-signal | weight | note |
|---|---|---|
| Posting hours | 0.70 | 24 UTC buckets, circularly smoothed — 23:00 is adjacent to 00:00 |
| Category | 0.20 | Jaccard over marketplace categories |
| Trade vocabulary | 0.05 | Demoted: ranks *backwards* on this corpus (tracks venue, not author) |
| Day of week | 0.05 | Demoted: ROC-AUC 0.505 here — the generator randomises dates |

The two demoted weights are set so they cannot move a band; `tests/test_linking.py` asserts that
arithmetically. [link/behaviour.py](../link/behaviour.py) records the measurement behind every
weight, including the warning that category agreement alone would confirm **both** designed hard
negatives and is safe only behind the dominant hour term.

### 4.4 Infrastructure overlap (I)

Scored only where both personas control a fingerprinted host, using the same rubric as onion↔clearnet
correlation — cert serial 1.00, shared SAN 0.95, favicon 0.80, ETag 0.70, banner with matching
header order 0.40 — combined by the same noisy-OR as `H`. §5 covers why this is unmeasured on the
shipped corpus.

### 4.5 Renormalisation

```
A_renorm = ( Σ_{k measured} w_k · X_k ) / ( Σ_{k measured} w_k )
```

An unmeasured component is not a zero. Its weight is redistributed over what was measured, and the
link's evidence list gains a `component_not_assessed` entry naming the component, its weight and the
reason. A pair with nothing measured raises rather than being written as a link.

### 4.6 Evidence

Every link stores a JSONB evidence array; a score with no reasons is not shippable. Entry types:
`shared_identifier`, `handle_reuse`, `stylometry`, `behaviour`, `behaviour_score`, `infrastructure`,
`infra_score`, `component_breakdown`, `component_not_assessed`, and for closure results
`transitive_path` and `not_directly_observed`.

---

## 5. The I term: a measured refusal

**This is the most defensible result in the project, and it is a negative one.**

`I` measures infrastructure the **personas control**. Every other term measures something the two
personas produced themselves — identifiers they published, words they wrote, hours they posted. A
vendor renting a stall on market_alpha does not run market_alpha's nginx; the favicon, ETag, banner
and certificate in `infra_findings` belong to the marketplace operator.

Recon ran and produced findings for all three sources. But nothing in this corpus is persona-scoped:

- all 20 profile URLs resolve to the 3 source onions, differing only by path segment;
- `fixtures/infra_findings.json` holds 3 rows, keyed `source_id`;
- neither `infra_findings` nor `infra_correlations` has a `persona_id` column;
- the 10 clearnet observations carry no vendor dimension;
- the one near-miss — `onion_mirror` on personas 3 and 18 — is already scored in **H** at 0.80, is
  the same literal string on both sides rather than two hosts that fingerprint alike, and is one of
  the identifiers `docs/BUILD_PLAN.md` records as appearing in no prose.

So [link/infra.py](../link/infra.py) **refuses by rule, not for want of data**. Give it a corpus
where vendors run their own mirrors and it measures —
`tests/test_infra.py::test_two_vendors_running_their_own_hosts_are_measured` is exactly that case,
passing.

### 5.1 The counter-evidence

Broadcasting a market's fingerprint to its vendors is the obvious shortcut, so it is implemented as
an opt-in evaluation mode and measured rather than argued away:

```bash
python scripts/evaluate.py --infra site-broadcast
```

```
  threshold                  P       R      F1    TP  FP  FN
  >=CONFIRMED (0.85)     1.000   0.750   0.857     6   0   2      ← --infra off
  >=CONFIRMED (0.85)     1.000   0.500   0.667     4   0   4      ← --infra site-broadcast

  demoted true positives — 2 real migration(s) moved DOWN a band
     1~9     Dr3adPirat3 ~ Dread_P1rate   CONFIRMED (0.876) → PROBABLE (0.745)   I=0.000
     2~10    NordicPharm ~ nordic_pharm   CONFIRMED (0.853) → PROBABLE (0.725)   I=0.000

  separation margin  +0.508 → +0.287   (-0.221)
    strongest rejected pair 14~15     graypigeon ~ plainbagel     0.345 → 0.438

  biggest gains among pairs that are NOT in the answer key:
     6~7     ObsidianLab ~ paperghost     0.009 → 0.188  (+0.179)   I=0.964
     1~7     Dr3adPirat3 ~ paperghost     0.011 → 0.190  (+0.179)   I=0.964
     2~7     NordicPharm ~ paperghost     0.034 → 0.208  (+0.174)   I=0.964
```

Three structural facts condemn it, each asserted in `tests/test_infra.py`:

1. **All 40 alpha×gamma pairs receive an identical value.** Only 4 are true positives. A feature
   constant across a set carries zero information about labels inside it — it cannot re-rank, only
   shift, and the shift lands on 36 non-positives including the designed hard negative (2,20).
2. **59 same-source pairs score ≥ 0.96** for sharing a website. Not one is a true positive.
3. **It is backwards at both ends.** 1~9 and 2~10 migrated to forum_beta, share no infrastructure,
   and so lose the renormalisation that was carrying them, while (2,20) gains.

The sharpest detail is in the gain list: every top entry contains persona 7, the persona stylometry
*refuses* for having 152 characters. Giving it a measured `I` of 0.964 replaces "we do not know"
with a number — manufacturing confidence about the one persona the system was right to say nothing
about.

`evaluate.py` labels the mode as deliberately unsound every time it runs. It is never the default.

---

## 6. Passive reconnaissance

### 6.1 The request surface

```python
PROBE_PATHS = ("/", "/favicon.ico", "/robots.txt",
               "/sitemap.xml", "/server-status", "/server-info")
PROBE_METHOD = "GET"
```

That is the entire surface. No POST, no auth header, no cookie replay, no parameter fuzzing, no path
enumeration beyond those six conventional names. `tests/test_recon.py` asserts `PROBE_PATHS` against
a forbidden-substring list rather than trusting the prose.

**Rate limiting**: 1 request / 2 seconds per host plus a global concurrency cap, enforced by
`HostRateLimiter`. Reservations are written under the lock as absolute slots, so a caller arriving
while another sleeps queues behind it instead of reading an expired timestamp and firing alongside.
Six probes is roughly twelve seconds per host.

**Transport**: [recon/tor.py](../recon/tor.py) defers its import of `legacy/darksearch.py`, which is
not import-safe — it calls `sys.exit(1)` on a missing dependency, runs `build_db()` at module scope
and hijacks root logging. Fixtures mode never pays that cost. `TOR_SOCKS` from `.env` is bridged to
`DS_TOR_PROXY_POOL`, the variable the v1 crawler actually reads.

**This is proxy-pool round-robin, not circuit rotation.** `stem` is pinned in `requirements.txt` and
imported nowhere in the repository; there is no ControlPort client and no `NEWNYM`. Distinct circuits
only result from running more than one tor daemon.

### 6.2 Collected signals

Response headers and served order, `Server`, `X-Powered-By`, `ETag`; favicon mmh3 hash; whether
`/server-status` or `/server-info` returns 200; default-index detection; directory-listing detection;
robots.txt and sitemap.xml; HTML comments; `<meta name="generator">`; absolute clearnet URLs in the
source; and TLS subject, issuer, serial, SANs and notBefore when the onion speaks HTTPS.

TLS reading deliberately does not validate — `check_hostname` off, `verify_mode` `CERT_NONE`. Hidden
service certificates are routinely self-signed and the certificate is presented to every visitor;
recon records what is served rather than establishing trust in it.

**Favicon hashing follows Shodan's convention**: `mmh3.hash(base64.encodebytes(bytes))`, base64 *with*
76-column line wrapping. Hashing the raw bytes produces a self-consistent number that matches nothing
anyone else has published — the worst failure mode for a pivot, because it looks like it works.

### 6.3 misconfig_score

A normalised weighted sum over ten signals, not a noisy-OR: leakiness is cumulative and bounded, and
ten weak signals should not saturate the gauge at 0.98.

| signal | weight | | signal | weight |
|---|---|---|---|---|
| `/server-status` exposed | 0.45 | | default index page | 0.20 |
| clearnet asset references | 0.40 | | ETag published | 0.20 |
| directory listing | 0.35 | | `X-Powered-By` | 0.15 |
| robots.txt naming paths | 0.25 | | `<meta generator>` | 0.15 |
| | | | versioned Server banner | 0.15 |
| | | | HTML comments | 0.10 |

A published sitemap is deliberately **not** a signal — publishing one is intentional, and scoring it
would make "leaky" mean "has a sitemap". `score_misconfig()` returns the per-signal breakdown
alongside the number, because the column has no evidence field of its own.

Applied identically in both `--source` modes, so fixtures and live mean the same pipeline. Where it
disagrees with the hand-authored fixture values the delta is reported and recorded in
`docs/BUILD_PLAN.md` rather than tuned away.

### 6.4 Clearnet correlation

```bash
python -m recon.correlate --source fixtures --dry-run
```

| `match_type` | score | rule |
|---|---|---|
| `tls_serial` | 1.00 | Exact certificate serial |
| `tls_san` | 0.95 | Onion's cert names the clearnet host (wildcards honoured) |
| `favicon` | 0.80 | Equal mmh3, excluding empty-icon sentinels |
| `etag` | 0.70 | Equal ETag |
| `banner` | 0.40 | Product/version match **and** ≥2 shared headers in the same order |

Each signal is stored as its own `infra_correlations` row so evidence stays atomic; the per-host
roll-up is a noisy-OR reusing `score.attribution.hard_identifier_score`, so infrastructure and
identifiers combine the same way rather than two ways.

On the fixture corpus this recovers market_gamma ↔ `gamma-mirror.hostvault.net` at 1.000 (serial
plus SAN), market_alpha ↔ `cdn-static-eu.hostvault.net` at 0.964 (favicon, ETag, banner), and
market_alpha ↔ `staging.hostvault.net` at 0.820 (ETag, banner), while unrelated hosts running the
same nginx build stay at 0.40. The 0.40/0.70 gap is doing real work: thousands of hosts run
nginx/1.18.0, so a banner match is a coincidence until something else agrees with it.

**Providers.** `ClearnetProvider` supplies Shodan-shaped records. `FixturesProvider` is complete and
offline. **`ShodanProvider` is implemented against the documented `api.shodan.io` endpoints using
plain `requests`, and has never been executed against a live key in this repository.** Selecting it
prints that caveat, and the same string is written into the evidence of every row it produces, so a
stored correlation carries its own provenance warning. Treat its first real run as untested code.

---

## 7. What is not built

### Phase 4 — API and console

`api/` does not exist; only `legacy/alert_api.py` from v1. Required:

- `api/main.py` — app bootstrap, CORS, error handling, `get_db` dependency
- `api/routers/actors.py` — `GET /actors` (filter by category, band, source, date range),
  `GET /actors/{id}` (personas, identifiers, links with evidence, post samples, timeline)
- `api/routers/graph.py` — `GET /graph`, node-link payload with `min_score` filtering
- `api/routers/timeline.py` — `GET /timeline`, activity buckets over a range
- `api/routers/recon.py` — `GET /recon/{onion}`, findings plus correlation candidates
- `api/routers/scan.py` — `POST /scan`, background job returning an id, audited in `scans`
- `GET /export/{fmt}` — csv | json | pdf

`ui/` is v1's app (alerts, threats, analytics). Its tactical component library — `TacticalPanel`,
`ThreatRow`, `ConfidenceMeter`, `NavRail` — and its design tokens are reusable; the pages are not.
Required routes: `/actors` (table with band pills), `/actors/[id]` (dossier with the H/S/B/I
breakdown and raw citations), `/graph` (force-directed, edge thickness by score, click an edge for
its evidence), `/timeline`, `/recon`.

Build the API before the UI. With no stable contract, Phase 4 becomes a rewrite.

### Phase 5 — autonomy and reporting

- APScheduler loop re-scanning registered sources, linking only new personas, appending to `scans`
- `export/report.py` — PDF case report: actor profile, identifier table, graph image, evidence list,
  methodology note, operator and generated-at

### Live acquisition

No `collectors/` package exists. `forum_collector.py` and `market_collector.py` would join
`legacy/darksearch.py`'s Tor session to `extract/`. Today live acquisition means handing an onion to
`recon/fingerprint.py --source live` yourself.

---

## 8. Reproducing every figure in this document

```bash
pip install -r requirements.txt

python -m pytest -q                                 # 230 passed
python scripts/evaluate.py                          # §4, §5 baseline
python scripts/evaluate.py --transitive             # closure pass
python scripts/evaluate.py --infra site-broadcast   # §5.1 counter-evidence
python -m recon.fingerprint --source fixtures --dry-run   # §6.3
python -m recon.correlate  --source fixtures --dry-run    # §6.4
```

All offline. With Postgres down, `pytest` reports 229 passed and 1 skipped — one Phase 1 test needs
a database. With PostgreSQL available:

```bash
python scripts/apply_schema.py
python scripts/load_fixtures.py
python scripts/ingest.py --source fixtures
python -m link.resolve --source db
python -m recon.fingerprint --source fixtures
python -m recon.correlate  --source db
python scripts/evaluate.py --source db              # identical figures to fixtures mode
```

`docker compose up -d db` provides PostgreSQL 16.

---

## 9. Evidentiary standard

1. **Chain of custody.** Every run writes a `scans` row with operator identity, mode, data source, a
   SHA-256 of the action payload, status and timestamps. Posts carry `body_hash`. The infra mode is
   part of the resolver's action hash, so one hash cannot stand for two different scorings.
2. **Explainability.** No score is presented without its H/S/B/I components and the citations behind
   them. `component_not_assessed` entries name what was skipped and why.
3. **Passivity.** The platform reads what servers already publish. The probe surface is asserted in
   the test suite, not merely promised.
4. **Honest refusal.** The system declines to score short text, drops checksum-failing wallets,
   reports pairwise recall as 6/8 rather than folding in inferred links, and leaves `I` unmeasured
   rather than inventing it. A tool that only ever says yes is a tool that is guessing.
5. **Scope of the numbers.** All evaluation figures are measured against a synthetic answer key.
   They describe this engine on this corpus. They are not real-world accuracy, and outputs are
   investigative leads requiring corroboration, never conclusions.
