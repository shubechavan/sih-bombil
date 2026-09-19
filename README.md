# Dark Sentinel v2 — Dark Web Threat Actor Attribution Platform

[![Tests](https://img.shields.io/badge/pytest-309%20passed-brightgreen.svg)](tests/)
[![UI checks](https://img.shields.io/badge/browser%20checks-27%20passed-brightgreen.svg)](ui/scripts/verify-pages.mjs)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.13-blue.svg)](requirements.txt)
[![Status](https://img.shields.io/badge/status-Phases%200--5%20complete-brightgreen.svg)](docs/BUILD_PLAN.md)
[![Use](https://img.shields.io/badge/use-Authorized%20Investigative%20Only-red.svg)](#legal--operational-disclaimer)

> Operators are not caught because Tor's cryptography fails. They are caught because the same
> PGP key, the same wallet, the same sleep schedule and the same misconfigured web server follow
> them from one identity to the next.

Dark Sentinel v2 links **personas** — one handle on one site — to the **actors** behind them. It
extracts hard identifiers from scraped prose, builds a stylometric writeprint and a behavioural
profile for each persona, passively fingerprints hidden services, and scores every persona pair with
a transparent weighted formula that stores its reasons alongside its number.

Successor to Dark Sentinel v1, which scored *content* for threat. This one scores *people* for
identity.

**Everything claimed in this README can be reproduced on a clean checkout in under a minute.**
Commands are given for each claim. Where something is not built, it says so.

---

## What actually runs today

| Phase | Scope | State |
|---|---|---|
| 0 | Schema, ground-truth fixture corpus | **Complete** |
| 1 | Identifier extraction, normalization, PGP, ingest | **Complete** |
| 2 | Stylometry, behaviour, resolution, graph, scoring | **Complete** |
| 3 | Passive recon, clearnet correlation, the I term | **Complete** |
| 4 | FastAPI endpoints, Next.js attribution console | **Complete** |
| 5 | Autonomous mode, PDF case report, compose stack | **Complete** |

The one thing the build plan asked for that does **not** exist is a `collectors/` package.
`legacy/darksearch.py` holds v1's working Tor crawler and [recon/tor.py](recon/tor.py) exposes its
session builder, but no forum or market parser was written — live acquisition today means handing an
onion to `recon/fingerprint.py --source live` yourself. Everything else runs on the fixture corpus,
which is what the demo uses.

`ui/` carries the five attribution pages and nothing else. v1's alerts, threats, analytics, PII,
pipeline and system pages — and the eight API routes that fed them — were deleted rather than left
lying around; the console's service strip now reports what `GET /health` actually knows (API,
Postgres, whether the pipeline has been run) instead of v1's n8n and RoBERTa, which never resolved.

---

## The attribution formula

```
A = w_H·H + w_S·S + w_B·B + w_I·I     →  0.0 .. 1.0

CONFIRMED ≥ 0.85 | PROBABLE 0.65–0.85 | POSSIBLE 0.45–0.65 | WEAK < 0.45
```

Two weight presets live in [score/attribution.py](score/attribution.py); `measured` is the default:

| preset | H | S | B | I |
|---|---|---|---|---|
| `claude_md` | 0.40 | 0.25 | 0.20 | 0.15 |
| `measured` *(default)* | 0.40 | 0.20 | 0.25 | 0.15 |

**H — hard identifier overlap.** Independent matches combine by noisy-OR, `H = 1 − Π(1 − mₖ)`:

| match | weight |
|---|---|
| OpenPGP fingerprint | 1.00 |
| Checksum-valid wallet (BTC / ETH / XMR / LTC) | 0.90 |
| Email / Jabber / Session id | 0.85 |
| Mirror onion | 0.80 |
| Exact handle reuse | 0.60 |
| Normalized handle collision (`Dr3ad_P1rat3` → `dreadpirate`) | 0.45 |

**S — stylometric cosine.** Character 3–5 gram TF-IDF (`max_features=5000`) plus function-word
frequencies, punctuation ratios, capitalisation, type-token ratio and sentence/word length.
Identifiers are masked out first, so a shared wallet cannot masquerade as a shared writing habit.
**Below 300 characters of masked prose, S returns `None` and the pair is not scored on style.**

**B — behavioural.** Posting-hour histogram (0.70, circularly smoothed so 23:00 and 00:00 are
adjacent), category Jaccard (0.20), trade vocabulary (0.05), day-of-week (0.05). The two demoted
sub-signals are weighted so they cannot move a band; [link/behaviour.py](link/behaviour.py) records
the measurements behind every weight, including why trade vocabulary ranks *backwards* on this
corpus.

**I — infrastructure overlap.** See below. On this corpus it is unmeasured, deliberately.

**Unmeasured is not zero.** When a component cannot be assessed, its weight is redistributed over
the measured ones rather than counted as 0.0, and the link's evidence list says which component was
skipped and why.

---

## Two findings worth leading with

### 1. The I term is unmeasured on all 190 pairs, and that is the correct answer

`I` measures infrastructure the **personas control**. Every other term measures something the two
personas produced themselves. A vendor renting a stall on market_alpha does not run market_alpha's
nginx — the favicon, ETag, banner and certificate in `infra_findings` belong to the *marketplace
operator*.

Recon works and produced findings for all three sources. But nothing in this corpus is
persona-scoped: all 20 profile URLs resolve to the 3 source onions, `infra_findings.json` is keyed
by `source_id`, and neither infra table has a `persona_id` column. So
[link/infra.py](link/infra.py) **refuses by rule, not for want of data** — hand it a corpus where
vendors run their own mirrors and it measures. `tests/test_infra.py` contains exactly that case,
passing.

### 2. The tempting alternative was measured, not argued away

Broadcasting a market's fingerprint to its vendors is the obvious shortcut. It is implemented, as an
opt-in evaluation mode, so its cost is a number rather than an opinion:

```bash
python scripts/evaluate.py --infra site-broadcast
```

| | `--infra off` (default) | `--infra site-broadcast` |
|---|---|---|
| precision, all bands | 1.000 | 1.000 |
| recall @ CONFIRMED | 6/8 | **4/8** |
| separation margin | **+0.508** | **+0.287** |

It gives all 40 alpha×gamma pairs an identical value — the same number for the 4 real migrations and
the 36 that are not — so it cannot rank anything. It demotes two true positives (1~9 and 2~10 moved
to forum_beta, which shares no infrastructure) and its largest gains land on pairs containing
persona 7, the persona stylometry *refuses* for having too little text. A signal that manufactures
confidence about the one persona the system was right to say nothing about is not a signal.

It is never the default, and `evaluate.py` labels it as unsound every time it runs.

---

## Evaluation against a known answer key

`fixtures/ground_truth.json` declares which persona is really which actor, so the linking engine can
be scored rather than demonstrated. Verbatim output of `python scripts/evaluate.py`:

```
──────────────────────────────────────────────────────────────────────────────
Dark Sentinel v2 — linking engine evaluation
on a synthetic corpus with known ground truth (fixtures/ground_truth.json)
──────────────────────────────────────────────────────────────────────────────
  corpus    20 personas, 190 pairs, 14 actors, 8 expected positive pairs
  source    fixtures
  weights   preset measured: H 0.40 S 0.20 B 0.25 I 0.15
  renorm    unmeasured components renormalised: True
  infra     mode off: 0/20 personas control a fingerprinted host
  Recon ran and produced findings for all 3 sources, but they are site-level:
  they fingerprint the marketplace, not the vendor. No persona here controls a
  host of their own, so I is unmeasured on every pair and its 0.15 weight is
  redistributed. See link/infra.py.

──────────────────────────────────────────────────────────────────────────────
PAIRWISE — direct evidence only (method='pairwise')
──────────────────────────────────────────────────────────────────────────────
  threshold                  P       R      F1    TP  FP  FN
  >=CONFIRMED (0.85)     1.000   0.750   0.857     6   0   2
  >=PROBABLE (0.65)      1.000   0.750   0.857     6   0   2
  >=POSSIBLE (0.45)      1.000   0.750   0.857     6   0   2

  reached at PROBABLE or better — 6/8
     1~16    Dr3adPirat3 ~ BlackSailsRX   0.925 CONFIRMED  H=1.000 S=0.857 B=0.858 I=  --
     3~18     Vect0rShop ~ V3ct0r_Supply  0.909 CONFIRMED  H=1.000 S=0.870 B=0.795 I=  --
     4~19     silk_hands ~ SilkHands      0.884 CONFIRMED  H=0.945 S=0.841 B=0.819 I=  --
     1~9     Dr3adPirat3 ~ Dread_P1rate   0.876 CONFIRMED  H=0.917 S=0.762 B=0.902 I=  --
     2~17    NordicPharm ~ NordPharmaEU   0.862 CONFIRMED  H=0.900 S=0.777 B=0.870 I=  --
     2~10    NordicPharm ~ nordic_pharm   0.853 CONFIRMED  H=0.917 S=0.721 B=0.855 I=  --

  not reached — 2/8
     9~16   Dread_P1rate ~ BlackSailsRX   0.429 WEAK       H=0.000 S=0.761 B=0.850 I=  --
    10~17   nordic_pharm ~ NordPharmaEU   0.415 WEAK       H=0.000 S=0.713 B=0.841 I=  --

  Both share no identifier at all, so H = 0 and only S and B remain. The
  answer key says as much itself: "9<->16 share nothing hard and must be
  resolved through the cluster". Pairwise scoring cannot reach these two and
  does not pretend to — see the closure block below.

  separation
    weakest accepted positive   2~10    NordicPharm ~ nordic_pharm   0.853
    strongest rejected pair    14~15     graypigeon ~ plainbagel     0.345
    margin                     +0.508

──────────────────────────────────────────────────────────────────────────────
HARD NEGATIVES — designed to be refused
──────────────────────────────────────────────────────────────────────────────
  [PASS]  2~20    NordicPharm ~ AtlasMeds      0.242 WEAK       ceiling POSSIBLE
         H=0.000 S=0.633 B=0.316 I=  --
         NordicPharm and AtlasMeds share a formal register, a greeting and a
         category. AtlasMeds is a genuinely new vendor: different
         contractions, punctuation, misspellings and posting hours.
  [PASS]  4~15     silk_hands ~ plainbagel     0.228 WEAK       ceiling POSSIBLE
         H=0.000 S=0.491 B=0.381 I=  --
         silk_hands and plainbagel share a sentence-length band and two
         discourse markers but no identifier, no misspellings and no posting
         hours. Stylometry should rate them highly; the full formula must not
         confirm.

──────────────────────────────────────────────────────────────────────────────
REFUSALS — where the engine declines to score
──────────────────────────────────────────────────────────────────────────────
  persona 7 (paperghost): stylometry returned None — 152 characters after
  identifier masking, below the 300-character floor. Every pair it appears in
  is scored with S unmeasured rather than with a number from two sentences.
  persona 8 (CryoVault): 2 checksum-failing wallet(s) — dropped, as required
      0x4daF2Cc326158a523D9B67fA72Cf616343455679
      1K1gaku9wLHA1C4JYjQL2z1Nz9HovFbHSE

──────────────────────────────────────────────────────────────────────────────
All designed hard negatives held below their ceiling.
Figures above are on a synthetic corpus with known ground truth; they measure
this engine against this answer key, not real-world accuracy.
──────────────────────────────────────────────────────────────────────────────
```

**Recall is reported as 6/8, not 8/8, on purpose.** Pairs 9~16 and 10~17 share no identifier at all;
the answer key says they must be resolved through the cluster. Adding `--transitive` runs the
closure pass as a **separate** method and reaches them — inferred links are decayed, band-capped and
reported apart from direct evidence:

```bash
python scripts/evaluate.py --transitive
```

```
  derived 2 link(s), each decayed and band-capped:
     9~16   Dread_P1rate ~ BlackSailsRX   0.657 PROBABLE   H=  --   S=  --   B=  --   I=  --     [correct]
        via persona 1 (Dr3adPirat3) — 9~1 CONFIRMED 0.876; 1~16 CONFIRMED 0.925
    10~17   nordic_pharm ~ NordPharmaEU   0.640 POSSIBLE   H=  --   S=  --   B=  --   I=  --     [correct]
        via persona 2 (NordicPharm) — 10~2 CONFIRMED 0.853; 2~17 CONFIRMED 0.862

  pairwise + closure, scored together:
  threshold                  P       R      F1    TP  FP  FN
  >=CONFIRMED (0.85)     1.000   0.750   0.857     6   0   2
  >=PROBABLE (0.65)      1.000   0.875   0.933     7   0   1
  >=POSSIBLE (0.45)      1.000   1.000   1.000     8   0   0

  closure added 2 link(s), 0 of them wrong
```

---

## Passive reconnaissance

Six GETs of conventional, publicly-served paths — `/`, `/favicon.ico`, `/robots.txt`,
`/sitemap.xml`, `/server-status`, `/server-info`. That list is the entire request surface. No POST,
no auth header, no credential, no path enumeration beyond those six names; `tests/test_recon.py`
asserts `PROBE_PATHS` against that rule rather than trusting the claim. Rate limited to 1 request
per 2 seconds per host with a global concurrency cap.

```bash
python -m recon.fingerprint --source fixtures --dry-run
python -m recon.correlate  --source fixtures --dry-run
```

Correlation scores onion↔clearnet candidates and keeps the weak signal weak:

| match | score |
|---|---|
| TLS certificate serial | 1.00 |
| TLS SAN naming the clearnet host | 0.95 |
| Favicon mmh3 (Shodan convention) | 0.80 |
| ETag | 0.70 |
| Server banner **and** matching header order | 0.40 |

On the fixture corpus that recovers every planted pivot — market_gamma ↔ `gamma-mirror.hostvault.net`
at 1.000 on cert serial and SAN, market_alpha ↔ `cdn-static-eu.hostvault.net` at 0.964 on favicon,
ETag and banner — while unrelated hosts running the same nginx build stay at 0.40.

Clearnet observations arrive behind a provider interface. The fixtures provider is complete.
**The Shodan provider is implemented against the documented API but has never been run against a
live key in this repository** — it says so at runtime when selected, and writes the same caveat into
the evidence of every row it produces.

---

## Quickstart

### The whole stack, in one command

```bash
cp .env.example .env
docker compose up -d --build              # postgres + tor + api + console
docker compose --profile seed up seed     # schema, corpus, link, cluster, recon, evaluate
```

API on <http://localhost:8000/docs>, console on <http://localhost:3000/actors>.

`seed` sits behind a profile so a plain `up` never rewrites a populated database as a side effect of
starting the stack. Autonomous mode is deliberately **not** a service — see below.

### Offline — no database, no Tor, no network

```bash
pip install -r requirements.txt

python -m pytest -q                                      # 309 passed
python scripts/evaluate.py                               # the table above
python scripts/evaluate.py --transitive                  # closure pass
python scripts/evaluate.py --infra site-broadcast        # the counter-evidence
python -m recon.fingerprint --source fixtures --dry-run
python -m recon.correlate  --source fixtures --dry-run
```

`--source fixtures` is deterministic and needs no network at all. With Postgres down, `pytest`
reports 261 passed and 47 skipped — every test that needs a database skips rather than fails.

### With PostgreSQL, step by step

```bash
python scripts/apply_schema.py            # re-runnable; --check reports drift
python scripts/load_fixtures.py --reset   # seed the corpus
python scripts/ingest.py --source fixtures
python -m link.resolve --source db        # writes links + writeprints
python -m link.cluster  --source db       # writes actors + personas.actor_id
python -m recon.fingerprint --source fixtures
python -m recon.correlate  --source db
python scripts/evaluate.py --source db    # same numbers as fixtures mode

uvicorn api.main:app --reload --port 8000
cd ui && npm install && npm run dev       # :3000
```

`scripts/apply_schema.py` exists because `psql` is frequently absent when Postgres runs in a
container. If you have `psql`, `psql $PG_URL -f schema_v2.sql` is equivalent.

**`link.cluster` is a required step, not an optional one.** Phase 2 deliberately stops short of
writing `actors`, because deciding that two personas are one actor is a judgement rather than a
similarity measurement. Until it runs, `/actors` is empty and `/health` says so.

### PDF case report

```bash
python -m export.report --actor 1 --out case.pdf
python -m export.report --all --out cases.pdf
curl -OJ 'http://localhost:8000/export/pdf?actor=1'
```

Actor profile, identifiers with provenance, the link graph, every link's evidence in plain language,
a methodology note, and the operator and generated-at stamp on every page. It carries the same rule
as the screen: **an unmeasured component prints the words "not assessed" and the reason, never
0.00** — and the synthetic-corpus caveat is on the report itself, not just in the terminal.

### Autonomous mode

```bash
python scripts/scheduler.py --run-once              # one pass
python scripts/scheduler.py --start --interval 15m  # run on a timer
python scripts/scheduler.py --status                # what the last tick did
python scripts/scheduler.py --stop                  # from another shell
```

**Nothing starts this.** It is not a compose service, the API does not launch it, and importing the
module starts no thread — `tests/test_phase5.py` asserts that in a subprocess. A tick ingests, then
compares the corpus's stylometric fingerprint against the last tick's: unchanged means linking is
skipped entirely and the tick says so; changed means a full re-link, because the writeprint
vocabulary is fitted over the whole corpus and a partial re-score would leave two incompatible
scorings in one table. Every tick writes a `scans` row with a SHA-256, including the ones that
changed nothing.

### Live Tor

Requires a Tor daemon on `127.0.0.1:9050`. `TOR_SOCKS` from `.env` is bridged to the variable the
v1 crawler reads.

```bash
python -m recon.fingerprint --source live --onion http://<address>.onion --dry-run
```

It probes Tor first and exits with a message if no proxy answers. Note this is **proxy-pool
round-robin, not circuit rotation** — `stem` is pinned in `requirements.txt` but imported nowhere,
and there is no ControlPort client, so two probes seconds apart may share a circuit.

---

## The fixture corpus

Synthetic, deterministic, and built so the engine can be scored rather than demonstrated.

| source | personas | posts |
|---|---|---|
| `market_alpha` | 8 | 79 |
| `forum_beta` | 7 | 70 |
| `market_gamma` | 5 | 51 |
| **total** | **20** | **200** |

14 actors, 8 expected positive pairs, 2 designed hard negatives, 4 armoured PGP key blocks,
3 recon findings, 10 Shodan-shaped clearnet observations.

Four market_alpha vendors reappear in market_gamma under new handles sharing a PGP key or wallet and
a consistent writing style. Two pairs are built to *look* linkable and must be refused. One persona
is below the stylometry floor; one carries two deliberately corrupted wallets.

`docs/BUILD_PLAN.md` records the corpus's known gaps — including eight declared identifiers that
appear in no prose and are therefore unreachable by any extractor. `scripts/load_fixtures.py` no
longer seeds them, so the database holds only values the pipeline can actually derive; dropping them
was measured first and moved no score, because five sat on a single persona and the sixth was a
mirror onion on a pair that already saturates H on two PGP fingerprints.

---

## Layout

```
db.py              SQLAlchemy 2.x models, engine, session factory, band thresholds
schema_v2.sql      re-runnable DDL — 11 tables, 1 view, indexes, audit trail
extract/           identifiers, normalize (leet decode), pgp, gliner_extract
recon/             fingerprint, correlate, tor (shim onto legacy/darksearch.py)
link/              stylometry, behaviour, infra, resolve, graph, cluster
score/             attribution — the formula, isolated and unit-tested
api/               FastAPI: actors, graph, timeline, recon, scan, export + schemas
export/            report.py — the PDF case report
fixtures/          synthetic corpus + ground_truth.json
scripts/           apply_schema, load_fixtures, ingest, evaluate, scheduler, generators
tests/             11 pytest modules, 309 tests
ui/                Next.js console: /actors /actors/[id] /graph /timeline /export
legacy/            v1 code kept for reuse — darksearch, llm, obfuslex, alert_api
Dockerfile         the Python image: API, pipeline, scheduler
ui/Dockerfile      the console image
docker-compose.yml postgres + tor + api + ui, plus a one-shot `seed` profile
```

## Test suite

`python -m pytest -q` → **309 passed**, plus 27 browser checks via
`node ui/scripts/verify-pages.mjs`.

| module | covers |
|---|---|
| `test_attribution.py` | formula bounds, presets, renormalisation, noisy-OR, transitive decay |
| `test_identifiers.py` | wallet checksums, PGP armor parsing, false-positive rejection, recall |
| `test_fixtures.py` | corpus integrity, stylometric separation, recon fixtures |
| `test_linking.py` | pairwise resolution, closure, hard negatives, refusals |
| `test_infra.py` | persona-controlled vs site-broadcast, and why the latter fails |
| `test_recon.py` | passive-only probe surface, rate limiter, mmh3, misconfig rubric |
| `test_correlate.py` | correlation rubric, planted pivots, noise suppression, providers |
| `test_cluster.py` | the actor partition against the answer key, threshold behaviour |
| `test_persistence.py` | NULL vs 0.0 in the database, stored refusals, identifier provenance |
| `test_api.py` | the wire contract — a component is a value or a reason, never both |
| `test_phase5.py` | the scheduler starts nothing on import; the PDF prints no phantom zeros |

Tests that need Postgres skip rather than fail when it is down, so the offline path stays green on
a clean checkout.

---

## Legal & Operational Disclaimer

> Dark Sentinel v2 is for authorized law enforcement, national security, academic threat research
> and defensive intelligence work only. All reconnaissance is strictly **passive** — it reads what a
> server already publishes to any visitor. There is no exploitation, authentication bypass, brute
> force, credential use or denial of service anywhere in it, and the probe surface is asserted in
> the test suite rather than merely promised.
>
> Every output is a probabilistic investigative **lead requiring human corroboration**, never a
> conclusion. Every scan writes an audit row with operator identity and a SHA-256 of the action.
> The evaluation figures above are measured against a synthetic answer key; they describe this
> engine on this corpus, not real-world accuracy.
