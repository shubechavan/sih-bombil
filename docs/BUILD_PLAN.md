# Dark Sentinel v2 — Actor Attribution Platform

Build plan for the new SIH problem statement (deanonymization + attribution of dark web threat actors),
reusing the existing `sih_hackathon-master` repo.

---

## 1. What changed vs the old project

| Old project (Dark Sentinel v1) | New problem statement |
|---|---|
| Find **threatening content** on onion sites | Find **who is behind it** and link them across sites |
| Unit of analysis = a scraped page | Unit of analysis = an **actor** (a person/persona) |
| Output = risk score + alert | Output = actor profile + identifier set + link graph + attribution confidence |
| One-shot scan | Timeline queryable database, autonomous continuous mode |

So the scraper stays, the AI stays, but the **data model flips from page-centric to actor-centric**.
That is the single biggest change. Everything else follows from it.

---

## 2. Reuse map — what to keep, change, drop

### Keep almost as-is
| File | Why |
|---|---|
| `darksearch.py` | Tor session, SOCKS proxy pool, circuit rotation, engine ranking, adaptive timeouts, content-hash caching, circuit breaker. This is 1100 lines of working crawler plumbing. Huge head start. |
| `fix_postgres.sql` | Migration style (`CREATE TABLE IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS`) — keep the pattern, add new tables. |
| `alert_api.py` | FastAPI shell, CORS, error handling. Add new routers to it. |
| `darksentinel-ui/` | Next.js 14 + zustand + recharts + tanstack-table + the whole tactical component library (`TacticalPanel`, `ThreatRow`, `ConfidenceMeter`, `NavRail`...). Reskin the pages, keep the components. **What happened:** the toolchain and four components (`TacticalPanel`, `StatCard`, `CommandBar`, `NavRail`) were kept; the rest went. `ThreatRow` and `ConfidenceMeter` were superseded by `components/attribution/` — a confidence band is not a severity, and a component breakdown has to render "not assessed" where a meter would draw a zero. v1's pages were deleted in Phase 5 rather than reskinned. |
| `obfuslex_engine.py` → `leet_decode()` | Repurposed: `Dr3adPirat3` → `dreadpirate`. This is exactly what you need for alias matching. Best hidden gem in the old repo. |

### Repurpose (same code, opposite job)
| Old | New |
|---|---|
| `llm.py :: scrub_pii()` (GLiNER strips 55+ PII types) | **Flip it.** Same GLiNER model, but now you *keep* what it finds. Emails, usernames, nicknames, locations become actor identifiers instead of being redacted. Two modes: `extract` (for the actor DB) and `redact` (for what you show on screen / send to the LLM). |
| `llm.py :: calculate_risk()` (R = 0.35T + 0.45C + 0.20H) | Becomes `calculate_attribution()` — same weighted-evidence shape, new inputs. Judges liked the transparent formula last time, keep that style. |
| `llm.py :: dual_model_consensus()` | Same idea, now for persona-match verdicts: statistical stylometry vs LLM judgement, agree = high confidence. |
| `llm.py :: classify_llm()` | Prompt changes from "is this a threat" to "does actor A write like actor B". |

### Drop
`cic_watcher.py`, `darknet.py`, `cic_rf_model.pkl`, `retrain_cic_rf_model.py`, `start_cicflowmeter.py`, `cicflowmeter-1.0.tar.gz`, `old_ml_models/`

Network-flow classification does not map to this problem statement, and a judge will ask why a
NetFlow model is in a persona-linking tool. Cutting it also removes the Java/CICFlowMeter setup
dependency, which was the most fragile part of the old demo.

---

## 3. Target architecture

```
LAYER 1 — COLLECTION
  tor_client.py          (from darksearch.py — session, proxy pool, circuit rotation)
  collectors/
    search_collector.py  multi-engine onion search  (existing code)
    forum_collector.py   thread + post + author parsing
    market_collector.py  vendor page + listing + feedback parsing
    seed_loader.py       loads local fixture corpus for demos

LAYER 2 — EXTRACTION
  extract/identifiers.py   regex + GLiNER: PGP blocks & fingerprints, BTC/ETH/XMR/LTC,
                           emails, jabber/XMPP, session IDs, telegram, mirror onions
  extract/normalize.py     leet_decode + unicode fold + separator strip  (from obfuslex)
  extract/pgp.py           parse ASCII-armoured key -> fingerprint, keyid, created, uid

LAYER 3 — RECON  (hidden service -> clearnet infrastructure)
  recon/fingerprint.py     HTTP headers, server banner, X-Powered-By, ETag,
                           favicon mmh3 hash, /server-status & /server-info exposure,
                           default index pages, TLS cert SAN/issuer/serial,
                           open dir listings, robots.txt, sitemap leaks
  recon/correlate.py       match those fingerprints against clearnet observations
                           (favicon hash / cert serial / ETag are the strong pivots)

LAYER 4 — LINKING
  link/stylometry.py       char 3-5 gram TF-IDF + function-word freq + punctuation
                           + emoji + avg sentence len -> writeprint vector, cosine sim
  link/behaviour.py        posting-hour histogram (timezone hint), session gaps,
                           category mix, price patterns, vocabulary of trade terms
  link/resolve.py          evidence -> weighted edges between personas
  link/graph.py            networkx: components, communities, shortest evidence path

LAYER 5 — SCORING & OUTPUT
  score/attribution.py     confidence formula (below)
  api/                     FastAPI: /actors /actors/{id} /graph /timeline /export /scan
  export/                  CSV, JSON, PDF report
  ui/                      Next.js — Actor list, Actor profile, Link graph, Timeline, Recon, Export
```

---

## 4. The attribution confidence formula

Keep the old project's "explainable weighted score" style. This is what makes judges nod.

```
A = 0.40·H + 0.25·S + 0.20·B + 0.15·I     ->  0.0 .. 1.0

H  hard identifier overlap
     same PGP fingerprint          1.00
     same wallet address           0.90
     same email / jabber / session 0.85
     same mirror onion             0.80
     exact handle reuse            0.60
     normalized handle match       0.45   (leet_decode collision)

S  stylometric similarity          cosine of writeprint vectors, 0..1
B  behavioural similarity          posting-hour + category + trade-vocab overlap
I  infrastructure overlap          shared cert / favicon hash / server banner / ETag

Bands:  >=0.85 CONFIRMED   0.65-0.85 PROBABLE   0.45-0.65 POSSIBLE   <0.45 WEAK
```

Every link row stores its **evidence list**, so the UI can say
*"PROBABLE (0.71) — same PGP fingerprint 4A2F…, writeprint cosine 0.68, 84% posting-hour overlap"*.
Never show a bare number without the reasons. That is the whole difference between a demo and a tool
an investigator would actually testify with.

---

## 5. Database schema (new tables)

See `schema_v2.sql`. Core tables:

- `sources` — every onion/site you touch, with last_scan_at
- `personas` — a handle on one specific source (the raw observation)
- `actors` — the resolved entity, one or many personas clustered together
- `identifiers` — PGP / wallet / email / jabber, with `first_seen` and `last_seen`
- `persona_identifiers` — many-to-many join
- `posts` — the text corpus used for stylometry, with timestamp
- `writeprints` — cached feature vectors per persona
- `links` — persona ↔ persona edges with score, band, evidence JSONB, method
- `infra_findings` — recon results per onion
- `infra_correlations` — onion ↔ clearnet candidate matches
- `scans` — audit log of every run, who ran it, what it touched

Keep the SHA-256 per-action audit log from v1. Under this problem statement it matters more,
not less — an attribution claim that can't be audited is worthless in court.

---

## 6. Demo data — do this early, not on the last night

You cannot rely on live onion crawling in a 6-minute demo. Engines go down, Tor bootstraps slowly,
markets vanish. Build a **seed corpus** on day 1:

```
fixtures/                                              <- PLANNED. Not what shipped.
  market_alpha/      vendor pages, listings, feedback  (12 vendors  -> shipped 8)
  forum_beta/        threads + posts                   (30 threads  -> shipped 7 personas)
  market_gamma/      the "migration" — 4 of the alpha vendors rebranded
  clearnet_obs/      fake Shodan-style records for the correlation demo
  ground_truth.json  which persona is really which actor
```

> **The generated corpus came out smaller than this plan — 8 / 7 / 5 personas (20 total, 200 posts)
> across market_alpha / forum_beta / market_gamma, not 12 vendors and 30 threads. `fixtures/` is the
> source of truth for every corpus count; count it, never quote the figures above.**
>
> This paragraph exists because two generated documents already lifted "12 vendors" and "30 threads"
> straight out of the block above and published them as fact. Everything in this file is the plan as
> written before the build, kept for its rationale — where it disagrees with the repository, the
> repository is right.

Two wins from this:
1. The demo is deterministic and works offline.
2. `ground_truth.json` lets you print **real precision/recall for the linking engine**. Almost no
   hackathon team shows an evaluation number. It is the cheapest way to look serious.

Ship both modes: `--source fixtures` and `--source live`. Demo on fixtures, prove live works with one
real search at the end if Tor is up.

### Known corpus gaps (Phase 0 artifact — read before judging Phase 2)

Phase 1 found this while measuring extraction recall. **8 of the 44 identifiers declared in
`fixtures/*/personas.json` appear in no bio and in no post.** They are declared with a placeholder
context (`"declared on the <source> profile of X"`) rather than a prose snippet, so no reader of text
can derive them — not Phase 1's extractor, and not any replacement for it.

| persona | handle | type | value |
|---|---|---|---|
| 3 | Vect0rShop | `onion_mirror` | `x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xawjjhi7ny6hhjuspyd.onion` |
| 8 | CryoVault | `btc` | `1K1gaku9wLHA1C4JYjQL2z1Nz9HovFbHSE` *(corrupted on purpose)* |
| 8 | CryoVault | `eth` | `0x4daF2Cc326158a523D9B67fA72Cf616343455679` *(corrupted on purpose)* |
| 9 | Dread_P1rate | `telegram` | `@dread_fam` |
| 10 | nordic_pharm | `telegram` | `@nordic_supply` |
| 17 | NordPharmaEU | `eth` | `0x6e05b5893F34dd1077cae73F8BB39357759FbE4F` |
| 18 | V3ct0r_Supply | `onion_mirror` | `x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xawjjhi7ny6hhjuspyd.onion` |
| 19 | SilkHands | `btc` | `bc1q3wk97fe0ce3w6p3xxsumxkj57ylhy7rxyrva5y` |

Two of the eight are the deliberately corrupted wallets, which are supposed to be unusable. **The
other six are valid identifiers that the corpus simply never puts into words**, and that has a
consequence Phase 2 must not be blamed for:

- The **H term is thinner than `ground_truth.json` implies** for personas 3, 9, 10, 17, 18 and 19.
- In particular, `notes_on_evidence` claims *"3↔18 share a PGP fingerprint and a mirror onion"*.
  Only the fingerprint is reachable. The mirror onion is not evidence any extractor can produce.
- Pairs 9↔16 and 10↔17 were already designed to share nothing hard, so the missing telegram handles
  cost nothing there — but 1↔9 and 2↔10 lean harder on the shared jabber/email than the answer key
  suggests.

Phase 1 reports recall against both denominators for this reason: **36/36 (100%) of the identifiers
that appear in prose, 36/44 (81.8%) of everything declared.** `scripts/ingest.py --dry-run` and
`pytest tests/test_identifiers.py` both print the list above, and `tests/test_identifiers.py`
asserts the set has not changed.

The corpus itself is not being regenerated. Making those six values appear in prose would perturb
the stylometry corpus that `tests/test_fixtures.py` holds to a margin, and the recall denominators
above stay as they are so the number remains explainable.

**What was fixed (Phase 4): the database no longer carries them.** `scripts/load_fixtures.py` used
to seed every declared identifier, while `scripts/ingest.py` wrote only what the extractor found.
The two disagreed, and the disagreement was visible: `--source db` could show *"3↔18 share a mirror
onion"* as evidence, which `--source fixtures` never showed and which no extractor in this system
could produce. `load_identifiers` now skips any value absent from the persona's bio, posts and key
blocks, so both source modes agree on the evidence as well as on the score.

Dropping them was measured before it was done, and costs nothing:

| | before | after |
|---|---|---|
| precision, all bands | 1.000 | 1.000 |
| recall @ CONFIRMED / PROBABLE | 6/8 | 6/8 |
| separation margin | +0.508 | +0.508 |
| hard negatives | both WEAK | both WEAK |
| pairs whose score moved | — | **0 of 190** |

Five of the six sit on a single persona, so they could never contribute to a pair's H. The sixth —
the mirror onion — is shared by 3 and 18, but that pair already carries two PGP fingerprints at
weight 1.00, and a 1.00 term saturates the noisy-OR: `H = 1 − Π(1 − wᵢ)` is 1.0 with or without an
0.80 alongside it. 3↔18 stays CONFIRMED at 0.909 on the fingerprints alone.

`tests/test_persistence.py` pins it: no identifier may carry a `meta.source` other than `"ingest"`,
the five values are named so a reseed cannot restore them quietly, and 3↔18 must still reach H = 1.0
without citing the mirror.

### Two PGP fingerprints where the corpus intends one (Phase 2 artifact)

The same reconciliation gap runs the other way for the four personas that publish an armoured key.
`scripts/gen_pgp_blocks.py` generates real OpenPGP key blocks whose **actual** fingerprints were
never folded back into the `IDS` catalogue in `scripts/gen_fixtures.py`, so those personas carry two
unrelated `pgp_fpr` values: the synthetic catalogue string printed in their prose, and the genuine
fingerprint of the key block Phase 1 parses.

| personas | catalogue value (prose) | key block fingerprint |
|---|---|---|
| 1, 16 | `CE588316E131A327E4F1AB418BEE1D17E2582FE2` | `A4F57ACBFD98FEB663038D0CA51FDFBD1EDC302F` |
| 3, 18 | `DD0FDE735ED7CBAA5E68B9D41C7FD3AB2319F0E9` | `126CA3123A8A913717E3A5AC093F05A37016D0FE` |

Only these four are affected — personas 2, 6, 11 and 20 declare a fingerprint but have no key block,
so they carry one value each. Both values are correctly extracted and both are genuinely shared
across the pair, so **there is no scoring impact**: `pgp_fpr` weighs 1.00 and the noisy-OR in
`score/attribution.py` saturates at 1.00 on either one alone. What it costs is the evidence
narrative. A link row for 1↔16 reads *"same PGP fingerprint CE5883…"* and *"same PGP fingerprint
A4F57A…"*, implying two independently corroborating keys, where `notes_on_evidence` says *"1↔16
share a PGP fingerprint"* — singular.

Not being fixed now, for the same reason as above: reconciling the catalogue with the generated keys
means regenerating the corpus, and the prose carrying those values feeds the stylometry margin
`tests/test_fixtures.py` asserts. The Phase 0 fix is to derive `IDS["PGP_A1"]` and `IDS["PGP_A3"]`
from the key blocks rather than from `pgp_fingerprint(label)`. Recorded so nobody reads a doubled
evidence list as a second key.

### Infrastructure is site-level, so I is unmeasurable per persona (Phase 3 artifact)

Phase 3 built recon and clearnet correlation, and both work: three fingerprints, thirteen
correlation candidates, every planted pivot recovered (see below). **The `I` term is still
unmeasured on all 190 pairs, and that is the correct outcome rather than an unfinished one.**

`I` measures infrastructure the personas *control*. Every other term in
`A = 0.40H + 0.25S + 0.20B + 0.15I` measures something the two personas themselves produced — the
identifiers they published, the words they wrote, when they posted. A vendor renting a stall on
market_alpha does not run market_alpha's nginx. The favicon, ETag, banner and certificate in
`infra_findings` belong to the marketplace operator, and alpha and gamma sharing them is evidence
about *those two sites having one operator* — a real and useful finding, and not an answer to
"is vendor X vendor Y".

This corpus carries no persona-level infrastructure at all:

- all 20 `profile_url` values resolve to the 3 source onions, varying only by path segment;
- `fixtures/infra_findings.json` holds 3 rows, keyed `source_id`;
- neither `infra_findings` nor `infra_correlations` has a `persona_id` column;
- the 10 clearnet observations carry no vendor dimension;
- the one near-miss, `onion_mirror` on personas 3 and 18, is already scored in **H** at 0.80, is the
  same literal string on both sides rather than two hosts that fingerprint alike, and is one of the
  six unreachable identifiers recorded above.

So `link/infra.py` refuses **by rule**: I is measured only where both personas control a
fingerprinted host. Point it at a corpus where vendors run their own mirrors and it measures —
`tests/test_infra.py::test_two_vendors_running_their_own_hosts_are_measured` is exactly that case,
passing.

**The tempting alternative was measured rather than argued away.** `scripts/evaluate.py --infra
site-broadcast` implements the discredited design — hand every vendor its market's fingerprint — and
prints what it costs. On this corpus:

| | `--infra off` | `--infra site-broadcast` |
|---|---|---|
| precision, all bands | 1.000 | 1.000 |
| recall @ CONFIRMED | 6/8 | **4/8** |
| recall @ PROBABLE | 6/8 | 6/8 |
| hard negatives | both WEAK | both WEAK |
| separation margin | **+0.508** | **+0.287** |

It does not manufacture a false positive here, but it takes 43% of the margin and demotes two real
migrations — 1~9 and 2~10, CONFIRMED → PROBABLE — because those vendors moved to forum_beta, which
shares no infrastructure with market_alpha, so they lose renormalisation and gain nothing. Meanwhile
the largest gains all land on pairs containing persona 7, the persona stylometry *refuses* for having
too little text: giving it a measured I of 0.964 replaces "we do not know" with a number. The hard
negative 2~20 rises 0.242 → 0.337 and the strongest rejected pair 14~15 rises 0.345 → 0.438, within
0.012 of the POSSIBLE floor.

Not being fixed now, and the fix is again a Phase 0 change: give two or three vendors their own
mirror hosts with distinct fingerprints and add `infra_findings` rows for them, so a persona-level I
has something to measure. Recorded so nobody reads an unmeasured I as an unbuilt one.

### misconfig_score: rubric vs the declared fixture values (Phase 3 artifact)

`recon/fingerprint.py::score_misconfig` is a weighted checklist over ten leak signals, applied
identically in fixtures and live mode so the two `--source` modes mean the same pipeline. The
hand-authored `misconfig_score` values in `fixtures/infra_findings.json` predate it and were not
derived from any rubric:

| source | declared | computed | Δ |
|---|---|---|---|
| market_alpha | 0.72 | 0.708 | −0.012 |
| forum_beta | 0.41 | 0.396 | −0.014 |
| market_gamma | 0.68 | 0.708 | +0.028 |

Close enough to be reassuring, with one structural note: **the rubric scores market_alpha and
market_gamma identically**, because their leak signals are identical — both expose `/server-status`,
both reference the same CDN, both carry the same robots.txt, ETag, `X-Powered-By` and HTML build
comment. The declared values differ by 0.04 and imply a distinction the data does not contain.
Recorded, not patched: the fixture file keeps its numbers and the weights were not tuned backwards to
reproduce them.

### Post order was never fixed, so writeprints were not reproducible (Phase 5 artifact)

`link/resolve.py::load_corpus` read posts with a bare `select(Post)` and no `ORDER BY`.
`Corpus.texts()` concatenates a persona's posts and the writeprint is char n-grams over that
concatenation, so the order Postgres happened to return rows in changed the vector — and changed
`corpus_version`, the key the writeprint cache is stored under.

Found by the autonomous scheduler, which compares that fingerprint between ticks to decide whether
re-linking is needed: it moved on every tick even when nothing had changed, because re-running
ingest reshuffled the physical row order. Two consecutive runs of `evaluate --source db` could also
disagree in the low digits, and the writeprint cache could never reliably hit.

Fixed with `ORDER BY posted_at NULLS LAST, id`. The evaluation figures are unchanged — they were
computed from `--source fixtures`, which reads JSON in file order and was always deterministic.

### "Re-link new personas only" is not achievable for S (Phase 5 artifact)

The Phase 5 prompt asks the scheduler to re-run linking on new personas only. It cannot, and the
reason is measurable rather than a matter of effort: the writeprint vectoriser fits its vocabulary
over the whole corpus, so adding one persona changes every other persona's vector. Dropping one of
the twenty fixture personas moves `S(1,16)` from 0.857399 to 0.849284 and `feature_version` from
`sty-1:78f0f5d646ef` to `sty-1:74f21c4d161d` — which is exactly the guard `stylometry.load()` uses
to refuse mixing vectors from two different fittings.

Scoring only the new pairs would therefore leave `links` holding two incompatible scorings, with the
older half silently disagreeing with `scripts/evaluate.py`. `scripts/scheduler.py` takes the saving
where it is real instead: it compares the corpus fingerprint and **skips linking entirely when no
new text arrived**, which is the common case for a polling job, and does a full consistent re-score
when it did. Every tick reports which of the two happened and why.

The genuine fix is a vocabulary fitted once and frozen, which changes what a writeprint means and
belongs in `link/stylometry.py` with its own evaluation.

### Shared-buyer overlap is not an identity signal (Phase 6 artifact)

Phase 6 added buyer feedback to the corpus and asked whether a shared buyer set
between two vendor personas should feed the attribution score. It should not,
and the measurement is unambiguous at every plausible parameter value.

Shared-buyer overlap, as Jaccard over each vendor's buyer set, across the 78
pairs among the 13 personas that have any feedback — the population where the
signal exists at all. (Over all 190 pairs it reads 0.475, but only because the
112 pairs with no feedback on either side tie at zero; that is a coverage
number, not a discrimination one. `python -m link.trust --measure` prints both.)

| | true positives | non-positives |
|---|---|---|
| mean overlap | 0.0250 | 0.0852 |
| ROC-AUC | **0.389** (0.5 is a coin flip) | |
| strongest overlap of all | — | 0.444, pairs (1,4) and (18,20) |

Non-positives score **3.4x higher** than true positives, and three of the four
measurable true pairs share no buyer at all. Every high-overlap pair is
same-market — (1,4) and (3,4) on alpha, (18,20) on gamma — because vendors on
one market draw from one customer pool. The signal tracks the marketplace, not
the trader, which is the `--infra site-broadcast` failure mode exactly: it
would reward strangers who share a landlord and punish the migrations, since a
vendor who moves market gets a new customer base.

Note also that only **4 of the 8** true positives are measurable at all: the
other four involve forum_beta, which has no listings and therefore no feedback.

**The measurement cannot settle the question on its own, and that is the more
important finding.** Feedback is generated, so its usefulness is whatever the
generator encoded. Sweeping `CROSS_MARKET_LOYALTY` in `scripts/gen_fixtures.py`:

| loyalty | 0.00 | 0.15 (shipped) | 0.30 | 0.50 | 0.75 | 1.00 |
|---|---|---|---|---|---|---|
| ROC-AUC | 0.318 | 0.389 | 0.601 | 0.601 | 0.927 | 1.000 |

At 1.00 the signal is perfect, because the generator was told to make it so.
This is unlike stylometry, where the generator plants writing quirks and
measuring recovers them — a real test of the extractor. Here a measurement
recovers a constant somebody chose. So the decision rests on the argument, and
the numbers only confirm it holds at every loyalty a real market would produce:
buyers do not follow a vendor across a migration in the three-quarters needed
to make this work, and if they did, the migration would be obvious by other
means.

Trust links are therefore stored in their own `feedback` table, shown on the
actor profile and in the graph as relationship context, and never summed into
A. Buyers are handles on a feedback row and never `personas`, so the 190 pairs
and every published figure are untouched by construction.

### Recon had never met a live certificate (Phase 6 artifact)

Phase 6 pointed `recon/fingerprint.py` at the lab hidden service — the first time any of it touched
a real server rather than reading JSON. Two bugs surfaced in the first minute, and both were
structurally invisible to the fixtures path, which loads `tls_serial` and `tls_sans` out of a file
and so never executes the code that reads them off a socket.

**Self-signed certificates were refused before they could be read.** `_probe` issued its GETs
through `requests` with default verification, so every `https://` onion failed at the handshake and
the certificate we came to look at was never seen. `fetch_tls` had always been explicit about not
validating — the docstring says so — but the HTTP probe beside it was not, and the result was a
fingerprint with every field empty and a misconfig score of 0.000. A v3 onion address *is* a public
key: Tor authenticates the endpoint end-to-end before TLS begins, so certificate validation adds no
assurance on a hidden service and only prevents reading. Now `verify=False`, matching what the TLS
reader had been doing deliberately all along.

**`format(serial, "X")` silently shortened serials.** The lab serves `0F3A9C1D77B54E2A`; we read it
back as `F3A9C1D77B54E2A` and compared that against a Shodan observation carrying the padded form.
The `tls_serial` match is the 1.00-weight signal — the strongest in the entire correlation rubric —
and it would never have fired against real data. Serials are byte strings and both Shodan and
Censys print them padded to whole bytes; we now do the same.

Underneath both sat a third problem worth naming: `fetch_tls` ended in a bare
`except Exception: return {}`. Best-effort was the right behaviour — most onions speak plain HTTP —
but it made a genuine bug (an `AttributeError`, because `get_values_for_type` returns plain strings
and the code read `.value` off them) indistinguishable from "this host has no TLS". It now prints
the reason it gave up.

The lesson generalises past this repo: a fixtures path that supplies a parser's *output* never
exercises the parser. The lab exists so that stops being true.

---

### JSONB does not preserve header order (Phase 3 schema addition)

The banner rule scores 0.40 only when the product/version matches **and** the shared response headers
appear in the same sequence, since header order is a weak fingerprint of the software stack. That
signal did not survive storage: **PostgreSQL `JSONB` re-sorts object keys** (by length, then
bytewise), so `{Server, X-Powered-By, ETag, Content-Type}` came back as
`{ETag, Server, Content-Type, X-Powered-By}` and `recon/correlate.py --source db` silently returned
6 candidates where `--source fixtures` returned 13.

Fixed rather than recorded, because it made the two `--source` modes disagree: `infra_findings` gains
an additive `header_order JSONB` column holding the names as an **array**, which JSONB does keep in
order. `Fingerprint.header_order` is deliberately never defaulted from `headers` — an empty list
means "the order is not known" and the banner rule declines, rather than comparing a sequence the
storage layer invented. Both writers (`recon/fingerprint.py` and `scripts/load_fixtures.py`) populate
it, and both `--source` modes now return the same 13 candidates.

---

## 7. Build order (5 phases)

| Phase | What | Ship criteria |
|---|---|---|
| 0 | Repo cleanup, drop CIC files, schema_v2.sql, fixtures + ground truth | `psql -f schema_v2.sql` clean, fixtures load |
| 1 | Identifier extraction + normalization + persona ingest | Ingest fixtures → personas & identifiers populated |
| 2 | Linking engine: hard identifiers → stylometry → behaviour → score | Precision/recall printed against ground_truth |
| 3 | Recon module + clearnet correlation | Fingerprint report per onion, correlation candidates listed |
| 4 | FastAPI + Next.js UI: actor list, profile, graph, timeline, export | Click actor → see graph → export CSV/JSON/PDF |
| 5 | Autonomous mode (APScheduler), audit log, polish, PPT | Scheduler runs, report PDF generated |
| 6 | Lab hidden service, collectors over Tor, buyer feedback | Crawl the lab onion → same numbers as fixtures mode |

Phase 6 was not in the original five. It was added because, measured against the problem statement,
collection was the missing half: no collectors existed, recon had never touched a real onion, and
the scheduler had nothing live to scan. Its ship criterion is deliberately the harshest one
available — a crawled corpus must score **identically** to the offline one, line for line, because
any loss in the collector shows up as a moved score and nothing else would catch it.

Do **not** build the UI first. It is tempting because it demos well, but with no linking engine
you'll spend phase 4 rewriting API contracts.

---

## 8. Running it in Claude Code

### Setup

```bash
mkdir dark-sentinel-v2 && cd dark-sentinel-v2
git init
# copy the reusable files across
cp ../sih_hackathon-master/darksearch.py         ./legacy/
cp ../sih_hackathon-master/llm.py                ./legacy/
cp ../sih_hackathon-master/obfuslex_engine.py    ./legacy/
cp ../sih_hackathon-master/alert_api.py          ./legacy/
cp ../sih_hackathon-master/fix_postgres.sql      ./legacy/
cp -r ../sih_hackathon-master/darksentinel-ui    ./ui
cp ../sih_hackathon-master/requirements.txt      ./

claude
```

Then drop in `CLAUDE.md` (provided) and run `/init` so Claude indexes the repo.

### How to drive it

- Use **plan mode** (`Shift+Tab` twice) for every phase. Read the plan, fix it, *then* let it build.
  Reviewing a plan takes 2 minutes; reviewing 900 lines of wrong code takes an hour.
- **One phase per session.** `/clear` between phases. A stuffed context makes it forget your schema.
- After every phase: `git add -A && git commit`. Non-negotiable during a hackathon — you will need to
  roll back at 3am.
- Make it write tests as it goes. Say *"write the pytest first, then the implementation"* for the
  linking engine specifically. That module is where silent bugs hide.
- Two terminals, two Claude sessions, different modules (e.g. one on `recon/`, one on `link/`).
  They don't conflict if you scope them to separate folders and commit often.

### Phase prompts (copy-paste)

**Phase 0**
```
Read CLAUDE.md and legacy/fix_postgres.sql.

Create schema_v2.sql for the actor-centric model described in CLAUDE.md section
"Data model". Use the CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN IF NOT
EXISTS style of the legacy file so it is re-runnable. Add indexes on
identifiers.value, personas.handle_normalized, posts.posted_at, links.score.

Then create fixtures/ with a synthetic corpus: 3 sources (market_alpha,
forum_beta, market_gamma), 20 personas, ~200 posts. Four alpha vendors must
reappear in gamma under new handles with the same PGP key or wallet, and with
genuinely similar writing style (consistent quirks: same misspellings, same
greeting, same punctuation habits). Write fixtures/ground_truth.json mapping
persona_id -> true_actor_id.

Also write db.py with a SQLAlchemy engine + session factory reading PG_* env vars,
and scripts/load_fixtures.py.
```

**Phase 1**
```
Build extract/identifiers.py.

Extract from a block of text: PGP public key blocks (parse to fingerprint + key id
+ uid via python-gnupg or pgpy), BTC (legacy + bech32), ETH, XMR, LTC addresses,
emails, XMPP/jabber IDs, Session IDs, telegram handles, and .onion v3 URLs.
Return a list of {type, value, raw_context, confidence}.

Validate wallets properly — base58check for BTC, EIP-55 for ETH. A regex-only
match on a random hex string will poison the whole link graph, so anything that
fails checksum gets dropped, not stored.

Build extract/normalize.py: reuse leet_decode from legacy/obfuslex_engine.py, add
unicode NFKD fold, strip separators (_ - . space), lowercase. normalize("Dr3ad_P1rat3")
must equal normalize("dreadpirate").

Build extract/gliner_extract.py: reuse the GLiNER loading code in legacy/llm.py but
in EXTRACT mode — return the entities instead of replacing them. Keep a redact()
function too, we still need it before sending text to any external LLM.

Write pytest tests for all three. Then scripts/ingest.py: fixtures -> personas,
identifiers, posts tables.
```

**Phase 2**
```
Build the linking engine.

link/stylometry.py — build a writeprint per persona from their concatenated posts:
character 3-5 gram TF-IDF (max_features 5000), function word frequencies, punctuation
ratios, avg sentence + word length, emoji set, capitalisation ratio, type-token ratio.
Cache the vector in the writeprints table. similarity(a, b) -> 0..1 cosine.
Require a minimum of 300 characters of text or return None — do not score a persona
with 2 posts, that is how false attributions happen.

link/behaviour.py — posting-hour histogram (24 buckets, normalised, cosine compared),
day-of-week profile, category overlap (Jaccard), trade vocabulary overlap.

link/resolve.py — for every persona pair: collect hard identifier overlaps, get S and
B, compute A with the formula in CLAUDE.md, assign the band, store the row in links
with a full evidence JSONB list. Skip pairs from the same source with the same handle.

score/attribution.py — the formula itself, isolated and unit-tested, with the weights
in a config dict so we can tune them.

Then scripts/evaluate.py: run the engine on fixtures, compare against
ground_truth.json, print precision / recall / F1 at each band. Write the pytest for
score/attribution.py BEFORE implementing it.
```

**Phase 3**
```
Build recon/fingerprint.py — for a given .onion, using the Tor session from
legacy/darksearch.py, collect passive, publicly-served surface signals only:
  - full response headers, Server and X-Powered-By banners, ETag
  - favicon bytes -> mmh3 hash (Shodan-compatible)
  - whether /server-status and /server-info respond 200 (misconfiguration flag)
  - default/unmodified index page detection
  - open directory listing detection
  - TLS certificate if HTTPS: subject, issuer, SANs, serial, notBefore
  - robots.txt and sitemap.xml contents
  - HTML comments, generator meta tags, absolute clearnet URLs in the source

Rate-limit to 1 request per 2 seconds per host and honour a global concurrency cap.
Everything goes into infra_findings with a timestamp.

recon/correlate.py — score onion↔clearnet candidate matches from those signals:
cert serial exact match 1.0, cert SAN containing a clearnet domain 0.95, favicon
hash match 0.8, ETag match 0.7, banner + header order match 0.4. Read clearnet
observations from fixtures/clearnet_obs/ behind a provider interface so a real
Shodan/Censys key can be dropped in later. Write to infra_correlations.

This is passive collection of what the server already publishes. No exploitation,
no auth bypass, no brute force — if a task needs credentials, stop and tell me.
```

**Phase 4**
```
Backend: extend legacy/alert_api.py into api/ with routers —
  GET  /actors            filter by category, band, source, first_seen/last_seen range
  GET  /actors/{id}       profile: personas, identifiers, links, posts sample, timeline
  GET  /graph             nodes + edges for the force graph, filterable by min score
  GET  /timeline          activity buckets over a date range
  GET  /recon/{onion}     fingerprint + correlation candidates
  POST /scan              trigger a scan, returns job id
  GET  /export/{fmt}      csv | json | pdf of the current result set

Frontend in ui/ (existing Next.js 14 app): reuse the components in
components/tactical/ and components/charts/. Build five pages —
  /actors      table (tanstack) with filters + confidence band pills
  /actors/[id] profile: identifier chips, linked personas w/ evidence, post samples
  /graph       force-directed link graph, node = persona, edge thickness = score,
               click edge -> evidence panel
  /timeline    activity over time with a date range brush
  /export      pick columns + format, download

Match the existing design tokens in styles/tokens.css. Do not add a UI library.
```

**Phase 5**
```
Add autonomous mode: APScheduler job that re-scans sources on an interval, re-runs
linking on new personas only, and appends to the scans audit table with a SHA-256
of each action. Add export/report.py generating a PDF case report (actor profile,
identifiers table, link graph image, evidence list, methodology note, generated-at
timestamp and operator name).

Then write README.md with setup, a docker-compose.yml (postgres + tor + api + ui),
and .env.example.
```

---

## 9. What wins points with judges

1. **Precision/recall on the linking engine.** Nobody else will have a number. `scripts/evaluate.py`
   exists for this.
2. **Evidence trails, not scores.** Every link explains itself in plain language.
3. **The confidence band vocabulary** (CONFIRMED / PROBABLE / POSSIBLE / WEAK) — it's how real
   intelligence products are written, and it signals you understand that attribution is probabilistic.
4. **Handling the negative case.** Show one pair the system *refuses* to link (too little text,
   wallet checksum failed). A tool that only ever says yes is a tool that's guessing.
5. **The audit log + legal framing.** Authorized-use banner, operator identity on every scan,
   SHA-256 per action. Say out loud that outputs are investigative leads requiring corroboration,
   not conclusions.
6. **The old project's crawler already works.** Say that. You're not starting from zero, and the
   Tor plumbing being battle-tested is a real advantage over teams demoing a fresh scraper.

---

## 10. Traps

- **Don't crawl live markets during the demo.** Fixtures. Every time.
- **Regex-only wallet matching** will link everyone to everyone. Checksum validation is mandatory.
- **Stylometry on short text is noise.** Enforce the 300-character floor.
- **Don't send raw scraped PII to a hosted LLM.** Redact first — you already have GLiNER for that,
  and it's a genuine privacy story to tell.
- **Transitive linking explodes.** A→B and B→C does not mean A→C at the same confidence. Multiply
  and decay, or keep clusters explicit.
- Scope creep: the problem statement mentions a lot. Hard identifiers + stylometry + one recon
  signal, done properly, beats six half-built modules.
