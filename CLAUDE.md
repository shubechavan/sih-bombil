# Dark Sentinel v2

Dark web threat actor attribution platform. Hackathon project (SIH). Successor to Dark Sentinel v1,
which was content-threat detection — this one is **actor attribution**.

## What it does

Collects footprints from onion marketplaces, forums and deep web sources, extracts identifiers
(handles, PGP keys, wallets), fingerprints hidden service misconfigurations for clearnet correlation,
and links rebranded or migrated personas to known actors using stylometry and behavioural profiling.
Analysts query it over a timeline through a web dashboard and export CSV / JSON / PDF.

**Authorized investigative use only.** Outputs are leads requiring corroboration, never conclusions.
Every scan writes an audit row with operator identity and a SHA-256 of the action.

## Stack

Python 3.11, FastAPI, PostgreSQL, SQLAlchemy 2.x, scikit-learn, networkx, GLiNER, requests+PySocks
over Tor SOCKS5, APScheduler.
Frontend: Next.js 14 App Router, TypeScript, zustand, recharts, @tanstack/react-table, CSS modules.

## Layout

```
legacy/          v1 code kept for reuse — read it before writing anything similar
collectors/      search / forum / market collectors + fixture loader
extract/         identifiers, normalization, PGP, GLiNER
recon/           hidden service fingerprinting + clearnet correlation
link/            stylometry, behaviour, resolution, graph
score/           attribution confidence
api/             FastAPI routers
export/          csv / json / pdf
ui/              Next.js app
fixtures/        synthetic demo corpus + ground_truth.json
scripts/         ingest, evaluate, load_fixtures
tests/           pytest
```

## Data model

- `sources` — a site (onion or clearnet), with `last_scan_at`
- `personas` — one handle on one source. Raw observation. Has `handle`, `handle_normalized`.
- `actors` — resolved entity. One actor owns one or more personas.
- `identifiers` — `{type, value, first_seen, last_seen}`. Types: pgp_fpr, btc, eth, xmr, ltc,
  email, jabber, session, telegram, onion_mirror.
- `persona_identifiers` — join table
- `posts` — text corpus for stylometry, with `posted_at`
- `writeprints` — cached feature vector per persona
- `links` — persona↔persona edge: `score`, `band`, `evidence` (JSONB list), `method`
- `infra_findings` — recon output per onion
- `infra_correlations` — onion↔clearnet candidate with score and reason
- `scans` — audit log

## Attribution formula

```
A = 0.40·H + 0.25·S + 0.20·B + 0.15·I

H  hard identifier overlap  (pgp 1.00, wallet 0.90, email/jabber 0.85,
                             mirror onion 0.80, exact handle 0.60, normalized handle 0.45)
S  stylometric cosine similarity
B  behavioural similarity (posting hours, categories, trade vocabulary)
I  infrastructure overlap (cert, favicon hash, banner, ETag)

CONFIRMED >=0.85 | PROBABLE 0.65-0.85 | POSSIBLE 0.45-0.65 | WEAK <0.45
```

Weights live in a config dict in `score/attribution.py`. Every link stores its evidence list —
a score with no reasons is not shippable.

## Rules

- **Read `legacy/` before building anything.** The Tor session handling, proxy pool, circuit
  rotation, engine ranking and content-hash cache in `legacy/darksearch.py` all work. Reuse, don't
  rewrite. Same for GLiNER loading in `legacy/llm.py` and `leet_decode` in
  `legacy/obfuslex_engine.py`.
- **Validate wallets with checksums.** base58check for BTC, EIP-55 for ETH. A regex-only match on
  a hex string poisons the entire link graph. Failed checksum = dropped, not stored.
- **Minimum 300 characters of text before stylometry runs.** Below that, return `None`. Do not score.
- **Redact before any external LLM call.** Use the GLiNER redact path. Raw scraped PII never leaves
  the box.
- **Recon is passive only.** Read what the server already serves — headers, banners, certs, favicon,
  robots.txt, exposed status pages. No exploitation, no auth bypass, no brute force, no credential
  use. If a task seems to need any of that, stop and ask.
- **Rate limit onion requests**: 1 req / 2s per host, global concurrency cap.
- **Two modes everywhere**: `--source fixtures` (deterministic, offline, for demos) and
  `--source live`. Fixtures must work with no network at all.
- Tests are pytest in `tests/`. For `score/` and `link/`, write the test first.
- Commit after every working phase.

## Local environment

**Use `.venv`, built on Python 3.11 — the version the Dockerfile pins.**

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest httpx
.venv/Scripts/python -m pytest -q
```

`numpy==1.26.4` is pinned and has no wheels for Python 3.13, so a 3.13
interpreter silently resolves numpy 2.x — which computes the behavioural cosine
one unit in the last place differently. Measured: B(1~16) is
`0.8581920990542637` under the pin, `...636` under numpy 2.2.1.

**Verify the attribution numbers in the container, not locally.**

```bash
docker compose exec api python scripts/evaluate.py
docker compose exec api python -m link.resolve --source db
```

The venv fixes the numpy drift and is right for running tests. It does **not**
reproduce the stored writeprint vectors, and that is not a version problem:
scikit-learn 1.6.1 on Windows and the identical version on Linux fit different
TF-IDF vocabularies from byte-identical text. Measured — every one of the 20
vector hashes changes and S moves about 0.002. No band moves and
`scripts/evaluate.py` prints the same precision, recall and margin either way,
but `links.s_score` and `writeprints.vector` belong to whichever platform last
refit them.

The container is the reference because CI and the demo run it. A local full
suite can trigger a refit through the scheduler tests, so after one, restore
with `docker compose exec api python -m link.resolve --source db` followed by
`link.cluster` before trusting a numbers diff.

`writeprint_vocab.toolchain` records the fit's library versions and platform,
and a vocabulary from a different one is refused rather than used. It has to be:
a vocabulary fitted by one toolchain and vectors by another transform cleanly
and mean nothing — measured, a persona's self-similarity fell from 1.000 to
0.620 with no error anywhere.

## Commands

```bash
psql $PG_URL -f schema_v2.sql        # migrations, re-runnable
python scripts/load_fixtures.py      # seed demo corpus
python scripts/ingest.py --source fixtures
python scripts/evaluate.py           # precision/recall vs ground_truth.json
uvicorn api.main:app --reload --port 8000
cd ui && npm run dev                 # :3000
pytest -q
```

## Env

```
PG_HOST PG_PORT PG_DB PG_USER PG_PASSWORD
TOR_SOCKS=socks5h://127.0.0.1:9050
SHODAN_API_KEY        # optional, correlation falls back to fixtures
OPERATOR_ID           # written into the audit log
JWT_SECRET            # signs session tokens; any long random string

# Behavioural profiling. Optional — with no key the profile falls back to a
# deterministic rule-based summary, labelled as one. Never an Anthropic key:
# nothing in v2 calls Anthropic.
LLM_PROVIDER          # gemini | groq | openai | custom
LLM_API_KEY
LLM_BASE_URL          # only for custom
LLM_MODEL             # overrides the provider default
```

## Not in scope

Network flow classification (v1's CICFlowMeter/RF pipeline) is removed. Do not reintroduce it.
